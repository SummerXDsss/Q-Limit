/**
 * 交易面板（同花顺中转）
 * 查询走 /api/trade/account|orders|trades，下单 / 撤单走 /api/trade/order|cancel
 * 中转服务每次读取客户端要几秒且只能串行，所以不做自动轮询，只在打开、切换 Tab、下单后刷新
 */

const TradeState = {
    status: null,
    side: 'buy',
    tab: 'positions',
    data: { positions: null, orders: null, trades: null },
    loading: false,
    pendingOrder: null,
    pin: '',            // 只留在内存里，刷新页面即失效
};

document.addEventListener('DOMContentLoaded', () => {
    const btn = document.getElementById('btnTrade');
    if (!btn) return;

    btn.addEventListener('click', openTradeModal);
    document.getElementById('btnCloseTrade').addEventListener('click', closeTradeModal);
    document.getElementById('tradeModal').addEventListener('click', (e) => {
        if (e.target.id === 'tradeModal') closeTradeModal();
    });
    document.getElementById('btnTradeRefresh').addEventListener('click', () => refreshTrade(true));

    document.querySelectorAll('.trade-side').forEach(el => {
        el.addEventListener('click', () => _setTradeSide(el.dataset.side));
    });
    document.querySelectorAll('.trade-tab').forEach(el => {
        el.addEventListener('click', () => _setTradeTab(el.dataset.tab));
    });

    ['tradeCode', 'tradePrice', 'tradeAmount'].forEach(id => {
        document.getElementById(id).addEventListener('input', _onTradeInput);
    });
    document.getElementById('tradeForm').addEventListener('submit', (e) => {
        e.preventDefault();
        _askTradeConfirm();
    });
    document.getElementById('btnTradeConfirmCancel').addEventListener('click', _hideTradeConfirm);
    document.getElementById('btnTradeConfirmOk').addEventListener('click', _submitTradeOrder);
});

async function _tradeFetch(url, options) {
    const resp = await fetch(url, options);
    let payload = {};
    try { payload = await resp.json(); } catch (e) { /* 非 JSON 响应按失败处理 */ }
    if (!resp.ok || payload.error) {
        throw new Error(payload.error || `请求失败（HTTP ${resp.status}）`);
    }
    return payload.data;
}

function openTradeModal() {
    document.getElementById('tradeModal').style.display = 'flex';
    _prefillTradeFromCurrentStock();
    _hideTradeConfirm();
    refreshTrade(false);
}

function closeTradeModal() {
    document.getElementById('tradeModal').style.display = 'none';
}

function _isAShare(stock) {
    return !!stock && ['SH', 'SZ', 'BJ'].includes((stock.market || '').toUpperCase()) && /^\d{6}$/.test(stock.code || '');
}

function _prefillTradeFromCurrentStock() {
    const stock = AppState.currentStock;
    if (!_isAShare(stock)) return;
    document.getElementById('tradeCode').value = stock.code;
    document.getElementById('tradeStockName').textContent = stock.name || '';
    const price = parseFloat(document.getElementById('headerStockPrice').textContent);
    if (!isNaN(price) && !document.getElementById('tradePrice').value) {
        document.getElementById('tradePrice').value = price;
    }
    _onTradeInput();
}

/** 刷新状态 + 当前 Tab；force 时连持仓资产一起重新读 */
async function refreshTrade(force) {
    if (TradeState.loading) return;
    TradeState.loading = true;
    document.getElementById('btnTradeRefresh').disabled = true;
    try {
        TradeState.status = await _tradeFetch('/api/trade/status');
        _renderTradeStatus();
        if (!TradeState.status.configured || !TradeState.status.bridge_online) {
            _renderTradeTable();
            return;
        }
        if (force || TradeState.data.positions === null) await _loadTradeAccount();
        if (TradeState.tab !== 'positions') await _loadTradeTab(TradeState.tab);
        _renderTradeTable();
    } catch (err) {
        _showTradeNotice(err.message, true);
    } finally {
        TradeState.loading = false;
        document.getElementById('btnTradeRefresh').disabled = false;
    }
}

async function _loadTradeAccount() {
    _setTradeTableMessage('正在读取资产与持仓（客户端每次读取需要几秒）...');
    const account = await _tradeFetch('/api/trade/account');
    TradeState.data.positions = account.positions || [];
    _renderTradeAssets((account.assets || [])[0]);
}

async function _loadTradeTab(tab) {
    _setTradeTableMessage('正在读取...');
    TradeState.data[tab] = await _tradeFetch(`/api/trade/${tab}`);
}

function _renderTradeStatus() {
    const st = TradeState.status;
    const badge = document.getElementById('tradeBadge');
    const reasons = [];

    if (!st.configured) {
        reasons.push('还没有配置同花顺中转服务。请到「设置 → 服务端配置 → 同花顺交易」填写中转服务地址和口令。');
    } else if (!st.bridge_online) {
        reasons.push('连不上中转服务，请确认 Windows 上的 ths_bridge 正在运行、地址正确。');
    } else {
        if (!st.trade_enabled) reasons.push('服务端未开启「允许网页下单 / 撤单」。');
        if (!st.pin_set) reasons.push('服务端还没有设置交易口令。');
        if (!st.bridge_trade) reasons.push('中转服务未以 --allow-trade 启动。');
    }

    const canTrade = st.configured && st.bridge_online && st.trade_enabled && st.pin_set && st.bridge_trade;
    badge.textContent = !st.configured ? '未配置' : !st.bridge_online ? '离线' : canTrade ? (st.bridge_mock ? '示例数据' : '可交易') : '只读';
    badge.className = 'trade-badge ' + (canTrade ? (st.bridge_mock ? 'is-mock' : 'is-live') : 'is-readonly');

    if (reasons.length) {
        const prefix = st.configured && st.bridge_online ? '当前只能查询，不能下单：' : '';
        _showTradeNotice(prefix + reasons.join(' '), !st.configured || !st.bridge_online);
    } else if (st.bridge_mock) {
        _showTradeNotice('中转服务运行在示例数据模式（--mock），下单不会到达券商。', false);
    } else {
        document.getElementById('tradeNotice').style.display = 'none';
    }
    document.getElementById('btnTradeSubmit').disabled = !canTrade;
}

function _showTradeNotice(text, isError) {
    const el = document.getElementById('tradeNotice');
    el.textContent = text;
    el.className = 'trade-notice' + (isError ? ' is-error' : '');
    el.style.display = 'block';
}

function _fmtTradeNum(value, digits) {
    const num = parseFloat(value);
    if (isNaN(num)) return '--';
    return num.toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function _renderTradeAssets(asset) {
    const el = document.getElementById('tradeAssets');
    el.innerHTML = '';
    if (!asset) return;
    [['总资产', asset.net_assets], ['资金余额', asset.cash], ['可用资金', asset.buy_power]].forEach(([label, value]) => {
        const item = document.createElement('div');
        item.className = 'trade-asset';
        const l = document.createElement('span');
        l.className = 'trade-asset-label';
        l.textContent = label;
        const v = document.createElement('span');
        v.className = 'trade-asset-value';
        v.textContent = _fmtTradeNum(value, 2);
        item.appendChild(l);
        item.appendChild(v);
        el.appendChild(item);
    });
}

function _setTradeSide(side) {
    TradeState.side = side;
    document.querySelectorAll('.trade-side').forEach(el => el.classList.toggle('active', el.dataset.side === side));
    const label = side === 'buy' ? '买入' : '卖出';
    ['btnTradeSubmit', 'btnTradeConfirmOk'].forEach(id => {
        const btn = document.getElementById(id);
        btn.classList.toggle('buy', side === 'buy');
        btn.classList.toggle('sell', side === 'sell');
    });
    document.getElementById('btnTradeSubmit').textContent = label;
    document.getElementById('tradeAmount').placeholder = side === 'buy' ? '买入为 100 的整数倍' : '卖出数量';
    _hideTradeConfirm();
    _onTradeInput();
}

async function _setTradeTab(tab) {
    TradeState.tab = tab;
    document.querySelectorAll('.trade-tab').forEach(el => el.classList.toggle('active', el.dataset.tab === tab));
    if (TradeState.data[tab] === null && TradeState.status && TradeState.status.configured && TradeState.status.bridge_online) {
        await refreshTrade(false);
    } else {
        _renderTradeTable();
    }
}

function _setTradeTableMessage(text) {
    const wrap = document.getElementById('tradeTableWrap');
    wrap.innerHTML = '';
    const msg = document.createElement('div');
    msg.className = 'trade-empty';
    msg.textContent = text;
    wrap.appendChild(msg);
}

const TRADE_COLUMNS = {
    positions: [['symbol', '代码'], ['name', '名称'], ['quantity', '持仓', 0], ['cost_price', '成本价', 3]],
    orders: [['time', '时间'], ['symbol', '代码'], ['name', '名称'], ['side', '方向'], ['price', '委托价', 3],
             ['quantity', '委托量', 0], ['filled_quantity', '成交量', 0], ['status', '状态']],
    trades: [['time', '时间'], ['symbol', '代码'], ['name', '名称'], ['side', '方向'], ['filled_price', '成交价', 3],
             ['filled_quantity', '成交量', 0], ['amount', '成交金额', 2]],
};
const TRADE_EMPTY_TEXT = { positions: '当前无持仓', orders: '今日没有委托', trades: '今日没有成交' };

function _renderTradeTable() {
    const tab = TradeState.tab;
    const rows = TradeState.data[tab];
    if (rows === null) return _setTradeTableMessage('暂无数据');
    if (!rows.length) return _setTradeTableMessage(TRADE_EMPTY_TEXT[tab]);

    const columns = TRADE_COLUMNS[tab];
    const table = document.createElement('table');
    table.className = 'trade-table';
    const head = table.createTHead().insertRow();
    columns.forEach(([, label, digits]) => {
        const th = document.createElement('th');
        th.textContent = label;
        if (digits !== undefined) th.className = 'num';
        head.appendChild(th);
    });
    head.appendChild(document.createElement('th'));

    const body = table.createTBody();
    rows.forEach(row => {
        const tr = body.insertRow();
        columns.forEach(([key, , digits]) => {
            const td = tr.insertCell();
            td.textContent = digits === undefined ? (row[key] || '--') : _fmtTradeNum(row[key], digits);
            if (digits !== undefined) td.className = 'num';
            if (key === 'side') td.className = String(row.side).includes('买') ? 'side-buy' : 'side-sell';
        });
        const action = tr.insertCell();
        action.className = 'num';
        if (tab === 'positions') {
            action.appendChild(_tradeRowButton('卖出', () => _fillTradeFromPosition(row)));
        } else if (tab === 'orders' && row.cancelable) {
            action.appendChild(_tradeRowButton('撤单', (btn) => _cancelTradeOrder(row, btn)));
        }
    });

    const wrap = document.getElementById('tradeTableWrap');
    wrap.innerHTML = '';
    wrap.appendChild(table);
}

function _tradeRowButton(text, onClick) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'btn-sm trade-row-btn';
    btn.textContent = text;
    btn.addEventListener('click', () => onClick(btn));
    return btn;
}

function _fillTradeFromPosition(row) {
    _setTradeSide('sell');
    document.getElementById('tradeCode').value = row.symbol;
    document.getElementById('tradeStockName').textContent = row.name || '';
    document.getElementById('tradeAmount').value = parseInt(row.quantity, 10) || '';
    document.getElementById('tradePrice').value = '';
    document.getElementById('tradePrice').focus();
    _onTradeInput();
}

function _readTradeForm() {
    return {
        side: TradeState.side,
        code: document.getElementById('tradeCode').value.trim(),
        price: document.getElementById('tradePrice').value.trim(),
        amount: document.getElementById('tradeAmount').value.trim(),
        pin: document.getElementById('tradePin').value,
    };
}

function _onTradeInput() {
    const form = _readTradeForm();
    const nameEl = document.getElementById('tradeStockName');
    const known = (TradeState.data.positions || []).find(p => p.symbol === form.code);
    const current = AppState.currentStock;
    if (known) nameEl.textContent = known.name;
    else if (_isAShare(current) && current.code === form.code) nameEl.textContent = current.name || '';
    else nameEl.textContent = '';

    const hint = document.getElementById('tradeAmountHint');
    hint.textContent = form.side === 'sell' && known ? `持仓 ${_fmtTradeNum(known.quantity, 0)}` : '';

    const total = parseFloat(form.price) * parseInt(form.amount, 10);
    document.getElementById('tradeEstimate').textContent = isNaN(total) ? '' : `预估金额 ${_fmtTradeNum(total, 2)} 元（不含费用）`;
    _hideTradeConfirm();
}

function _validateTradeForm(form) {
    if (!/^\d{6}$/.test(form.code)) return '证券代码必须是 6 位数字';
    if (!(parseFloat(form.price) > 0)) return '请填写大于 0 的委托价格';
    if (!/^\d+$/.test(form.amount) || parseInt(form.amount, 10) <= 0) return '请填写正整数数量';
    if (!form.pin) return '请输入交易口令';
    return '';
}

function _showTradeResult(text, isError) {
    const el = document.getElementById('tradeResult');
    el.textContent = text;
    el.className = 'trade-result' + (isError ? ' is-error' : text ? ' is-ok' : '');
}

function _askTradeConfirm() {
    const form = _readTradeForm();
    const error = _validateTradeForm(form);
    if (error) return _showTradeResult(error, true);

    TradeState.pendingOrder = form;
    const name = document.getElementById('tradeStockName').textContent;
    const total = parseFloat(form.price) * parseInt(form.amount, 10);
    document.getElementById('tradeConfirmText').textContent =
        `${form.side === 'buy' ? '买入' : '卖出'} ${name ? name + ' ' : ''}${form.code}，` +
        `${_fmtTradeNum(form.amount, 0)} 股 @ ${form.price}，约 ${_fmtTradeNum(total, 2)} 元。` +
        '确认后会真实报单到券商。';
    document.getElementById('btnTradeSubmit').style.display = 'none';
    document.getElementById('tradeConfirm').style.display = 'block';
    _showTradeResult('', false);
}

function _hideTradeConfirm() {
    TradeState.pendingOrder = null;
    document.getElementById('tradeConfirm').style.display = 'none';
    document.getElementById('btnTradeSubmit').style.display = '';
}

async function _submitTradeOrder() {
    const order = TradeState.pendingOrder;
    if (!order) return;
    const okBtn = document.getElementById('btnTradeConfirmOk');
    okBtn.disabled = true;
    okBtn.textContent = '报单中...';
    try {
        const result = await _tradeFetch('/api/trade/order', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(order),
        });
        TradeState.pin = order.pin;
        _hideTradeConfirm();
        _showTradeResult(result.entrust_no ? `已报单，委托编号 ${result.entrust_no}` : `已提交：${result.message || '请在当日委托中确认'}`, false);
        TradeState.data.orders = null;
        TradeState.data.positions = null;
        await _setTradeTab('orders');
    } catch (err) {
        _hideTradeConfirm();
        _showTradeResult(err.message, true);
    } finally {
        okBtn.disabled = false;
        okBtn.textContent = '确认下单';
    }
}

async function _cancelTradeOrder(row, btn) {
    const pin = document.getElementById('tradePin').value || TradeState.pin;
    if (!pin) {
        _showTradeResult('撤单前请先在左侧输入交易口令', true);
        document.getElementById('tradePin').focus();
        return;
    }
    if (btn.dataset.armed !== '1') {
        // 第一次点击只是进入确认状态，3 秒内再点一次才真正撤单
        btn.dataset.armed = '1';
        btn.textContent = '确认撤单';
        btn.classList.add('is-armed');
        setTimeout(() => {
            if (!btn.isConnected || btn.disabled) return;
            btn.dataset.armed = '';
            btn.textContent = '撤单';
            btn.classList.remove('is-armed');
        }, 3000);
        return;
    }
    btn.disabled = true;
    btn.textContent = '撤单中...';
    try {
        const result = await _tradeFetch('/api/trade/cancel', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ entrust_no: row.entrust_no, pin }),
        });
        TradeState.pin = pin;
        _showTradeResult(`撤单 ${row.entrust_no}：${result.message || '已提交'}`, false);
        TradeState.data.orders = null;
        await refreshTrade(false);
    } catch (err) {
        _showTradeResult(err.message, true);
        btn.disabled = false;
        btn.textContent = '撤单';
    }
}
