/**
 * 记录弹窗：分析历史（chat_history 按会话聚合）+ 运行日志（app_logs）
 */

const RecordsState = {
    view: 'history',
    sessions: [],
    activeKey: '',
    searchTimer: null,
};

const LOG_CATEGORY_TEXT = { trade: '交易', bot: '机器人', ai: 'AI', settings: '配置', system: '系统' };
const HISTORY_SOURCE_TEXT = { web: '网页', bot: '机器人' };

document.addEventListener('DOMContentLoaded', () => {
    const btn = document.getElementById('btnRecords');
    if (!btn) return;

    btn.addEventListener('click', openRecordsModal);
    document.getElementById('btnCloseRecords').addEventListener('click', closeRecordsModal);
    document.getElementById('recordsModal').addEventListener('click', (e) => {
        if (e.target.id === 'recordsModal') closeRecordsModal();
    });
    document.querySelectorAll('.records-tab').forEach(tab => {
        tab.addEventListener('click', () => _setRecordsView(tab.dataset.view));
    });

    const debounce = (fn) => () => {
        clearTimeout(RecordsState.searchTimer);
        RecordsState.searchTimer = setTimeout(fn, 300);
    };
    document.getElementById('historySearch').addEventListener('input', debounce(loadHistorySessions));
    document.getElementById('logSearch').addEventListener('input', debounce(loadLogs));
    document.getElementById('logCategory').addEventListener('change', loadLogs);
    document.getElementById('logLevel').addEventListener('change', loadLogs);
    document.getElementById('btnLogRefresh').addEventListener('click', loadLogs);
});

function openRecordsModal() {
    document.getElementById('recordsModal').style.display = 'flex';
    _setRecordsView(RecordsState.view);
}

function closeRecordsModal() {
    document.getElementById('recordsModal').style.display = 'none';
}

function _setRecordsView(view) {
    RecordsState.view = view;
    document.querySelectorAll('.records-tab').forEach(tab => tab.classList.toggle('active', tab.dataset.view === view));
    document.getElementById('recordsHistory').style.display = view === 'history' ? '' : 'none';
    document.getElementById('recordsLogs').style.display = view === 'logs' ? '' : 'none';
    if (view === 'history') loadHistorySessions();
    else loadLogs();
}

async function _recordsFetch(url, options) {
    const resp = await fetch(url, options);
    let payload = {};
    try { payload = await resp.json(); } catch (e) { /* 非 JSON 响应按失败处理 */ }
    if (!resp.ok || payload.error) throw new Error(payload.error || `请求失败（HTTP ${resp.status}）`);
    return payload.data;
}

function _recordsMessage(container, text, isError) {
    container.innerHTML = '';
    const el = document.createElement('div');
    el.className = 'records-empty' + (isError ? ' is-error' : '');
    el.textContent = text;
    container.appendChild(el);
}

function _fmtRecordTime(value) {
    // 2026-10-10T23:32:02.435 → 10-10 23:32
    const text = String(value || '');
    return text.length >= 16 ? `${text.slice(5, 10)} ${text.slice(11, 16)}` : text;
}

function _recordsTag(text, extraClass) {
    const tag = document.createElement('span');
    tag.className = 'records-tag' + (extraClass ? ' ' + extraClass : '');
    tag.textContent = text;
    return tag;
}

// ============================================================
// 分析历史
// ============================================================
const _sessionKey = (s) => `${s.stock_code}\u0000${s.session_id}`;

async function loadHistorySessions() {
    const list = document.getElementById('historyList');
    const keyword = document.getElementById('historySearch').value.trim();
    try {
        RecordsState.sessions = await _recordsFetch(`/api/chat/sessions?limit=200&q=${encodeURIComponent(keyword)}`);
    } catch (err) {
        return _recordsMessage(list, err.message, true);
    }
    _renderHistoryList();
    const active = RecordsState.sessions.find(s => _sessionKey(s) === RecordsState.activeKey);
    if (active) return;
    if (RecordsState.sessions.length) {
        _openHistorySession(RecordsState.sessions[0]);
    } else {
        RecordsState.activeKey = '';
        _recordsMessage(document.getElementById('historyDetail'), keyword ? '没有匹配的记录' : '还没有分析记录。和 AI 对话、发起辩论或让机器人 #ANALYZE 后会出现在这里。');
    }
}

function _renderHistoryList() {
    const list = document.getElementById('historyList');
    list.innerHTML = '';
    if (!RecordsState.sessions.length) return _recordsMessage(list, '暂无记录');

    RecordsState.sessions.forEach(session => {
        const item = document.createElement('button');
        item.type = 'button';
        item.className = 'records-item' + (_sessionKey(session) === RecordsState.activeKey ? ' active' : '');

        const head = document.createElement('div');
        head.className = 'records-item-head';
        const name = document.createElement('span');
        name.className = 'records-item-name';
        name.textContent = session.stock_name ? `${session.stock_name} ${session.stock_code}` : (session.stock_code || '未选股票');
        const time = document.createElement('span');
        time.className = 'records-item-time';
        time.textContent = _fmtRecordTime(session.updated_at);
        head.appendChild(name);
        head.appendChild(time);

        const title = document.createElement('div');
        title.className = 'records-item-title';
        title.textContent = session.title;

        const meta = document.createElement('div');
        meta.className = 'records-item-meta';
        meta.appendChild(_recordsTag(HISTORY_SOURCE_TEXT[session.source] || session.source));
        meta.appendChild(_recordsTag(session.mode === 'debate' ? '辩论' : '对话'));
        meta.appendChild(_recordsTag(`${session.count} 条`));

        item.appendChild(head);
        item.appendChild(title);
        item.appendChild(meta);
        item.addEventListener('click', () => _openHistorySession(session));
        list.appendChild(item);
    });
}

async function _openHistorySession(session) {
    RecordsState.activeKey = _sessionKey(session);
    _renderHistoryList();
    const detail = document.getElementById('historyDetail');
    _recordsMessage(detail, '加载中...');

    let messages;
    try {
        messages = await _recordsFetch(
            `/api/chat/session?stock_code=${encodeURIComponent(session.stock_code)}&session_id=${encodeURIComponent(session.session_id)}`
        );
    } catch (err) {
        return _recordsMessage(detail, err.message, true);
    }
    if (_sessionKey(session) !== RecordsState.activeKey) return;  // 加载期间已切到别的会话

    detail.innerHTML = '';
    const header = document.createElement('div');
    header.className = 'records-detail-header';
    const heading = document.createElement('div');
    heading.className = 'records-detail-title';
    heading.textContent = `${session.stock_name || ''} ${session.stock_code} · ${_fmtRecordTime(session.started_at)}`;
    const actions = document.createElement('div');
    actions.className = 'records-detail-actions';

    if (session.source === 'web' && session.market) {
        const resume = document.createElement('button');
        resume.className = 'btn-sm';
        resume.textContent = '继续对话';
        resume.addEventListener('click', () => _resumeHistorySession(session, messages));
        actions.appendChild(resume);
    }
    const remove = document.createElement('button');
    remove.className = 'btn-sm records-delete';
    remove.textContent = '删除';
    remove.addEventListener('click', () => _deleteHistorySession(session, remove));
    actions.appendChild(remove);
    header.appendChild(heading);
    header.appendChild(actions);
    detail.appendChild(header);

    const body = document.createElement('div');
    body.className = 'records-detail-body';
    messages.forEach(msg => _historyBubbles(session, msg).forEach(el => body.appendChild(el)));
    detail.appendChild(body);
}

/** 一条 chat_history 记录 → 提问气泡（可选）+ 回答气泡 */
function _historyBubbles(session, msg) {
    const bubbles = [];
    // 辩论和机器人分析的 user_message 是系统拼的长提示词，不展示
    if (session.mode !== 'debate' && session.source !== 'bot' && msg.user_message) {
        bubbles.push(_historyBubble('user', msg.user_message, ''));
    }
    bubbles.push(_historyBubble(msg.role, msg.ai_response || '', _fmtRecordTime(msg.created_at)));
    return bubbles;
}

function _historyBubble(role, content, time) {
    const config = ROLE_CONFIG[role] || ROLE_CONFIG.user;
    const wrap = document.createElement('div');
    wrap.className = 'chat-msg';
    const header = document.createElement('div');
    header.className = 'chat-msg-header';
    const icon = document.createElement('span');
    icon.className = 'chat-msg-icon';
    icon.textContent = config.icon;
    const name = document.createElement('span');
    name.className = `chat-msg-role ${config.roleClass}`;
    name.textContent = config.name;
    header.appendChild(icon);
    header.appendChild(name);
    if (time) {
        const t = document.createElement('span');
        t.className = 'records-item-time';
        t.textContent = time;
        header.appendChild(t);
    }
    const body = document.createElement('div');
    body.className = `chat-msg-body ${config.msgClass}`;
    if (role === 'user') body.textContent = content;
    else body.innerHTML = renderMarkdown(content);
    wrap.appendChild(header);
    wrap.appendChild(body);
    return wrap;
}

async function _deleteHistorySession(session, btn) {
    if (btn.dataset.armed !== '1') {
        btn.dataset.armed = '1';
        btn.textContent = '确认删除';
        setTimeout(() => {
            if (!btn.isConnected) return;
            btn.dataset.armed = '';
            btn.textContent = '删除';
        }, 3000);
        return;
    }
    btn.disabled = true;
    try {
        await _recordsFetch('/api/chat/sessions', {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ stock_code: session.stock_code, session_id: session.session_id }),
        });
        RecordsState.activeKey = '';
        await loadHistorySessions();
    } catch (err) {
        btn.disabled = false;
        btn.textContent = '删除失败，重试';
    }
}

/** 把历史会话载回聊天窗口：切到对应股票、沿用 session_id，后续提问会带上这段上下文 */
function _resumeHistorySession(session, messages) {
    selectStock({
        code: session.stock_code,
        name: session.stock_name || session.stock_code,
        market: session.market,
        product: session.product || 'ST',
    });
    ChatState.sessionId = session.session_id;
    document.getElementById('chatMessages').innerHTML = '';
    messages.forEach(msg => {
        if (session.mode !== 'debate' && msg.user_message) appendMessage('user', msg.user_message);
        appendMessage(msg.role, msg.ai_response || '');
    });
    appendSystemMessage('已载入历史会话，可以接着提问');
    closeRecordsModal();
    if (!ChatState.isOpen) toggleChat();
}

// ============================================================
// 运行日志
// ============================================================
async function loadLogs() {
    const list = document.getElementById('logList');
    const params = new URLSearchParams({
        category: document.getElementById('logCategory').value,
        level: document.getElementById('logLevel').value,
        q: document.getElementById('logSearch').value.trim(),
        limit: '300',
    });
    let rows;
    try {
        rows = await _recordsFetch(`/api/logs?${params}`);
    } catch (err) {
        return _recordsMessage(list, err.message, true);
    }
    if (!rows.length) return _recordsMessage(list, '没有符合条件的日志');

    list.innerHTML = '';
    rows.forEach(row => {
        const item = document.createElement('details');
        item.className = `records-log level-${row.level}`;
        const summary = document.createElement('summary');
        const time = document.createElement('span');
        time.className = 'records-log-time';
        time.textContent = String(row.time || '').replace('T', ' ').slice(5);
        summary.appendChild(time);
        summary.appendChild(_recordsTag(LOG_CATEGORY_TEXT[row.category] || row.category, `cat-${row.category}`));
        const message = document.createElement('span');
        message.className = 'records-log-message';
        message.textContent = row.message;
        summary.appendChild(message);
        item.appendChild(summary);

        const detail = document.createElement('pre');
        detail.className = 'records-log-detail';
        detail.textContent = JSON.stringify(row.detail || {}, null, 2);
        item.appendChild(detail);
        list.appendChild(item);
    });
}
