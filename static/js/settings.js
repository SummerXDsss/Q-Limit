/**
 * 设置面板 - 服务端 .env 配置
 * 表单结构由 /api/settings/env 返回的分组定义驱动
 */

const EnvSettingsState = {
    loaded: false,
    fields: {},        // key -> 字段定义（含初始值）
    pendingClear: new Set(),
};

async function loadEnvSettings() {
    const body = document.getElementById('envSettingsBody');
    if (!body) return;

    EnvSettingsState.loaded = false;
    EnvSettingsState.fields = {};
    EnvSettingsState.pendingClear = new Set();
    body.innerHTML = '<div class="env-settings-tip">加载中...</div>';

    try {
        const resp = await fetch('/api/settings/env');
        const payload = await resp.json();
        if (!resp.ok) {
            body.innerHTML = '';
            body.appendChild(_envTip(payload.error || '加载服务端配置失败', true));
            return;
        }
        _renderEnvSettings(body, payload.data || {});
        EnvSettingsState.loaded = true;
    } catch (err) {
        body.innerHTML = '';
        body.appendChild(_envTip('加载服务端配置失败: ' + err.message, true));
    }
}

function _envTip(text, isError) {
    const tip = document.createElement('div');
    tip.className = 'env-settings-tip' + (isError ? ' is-error' : '');
    tip.textContent = text;
    return tip;
}

function _renderEnvSettings(container, data) {
    container.innerHTML = '';
    container.appendChild(_envTip(
        data.env_file_exists
            ? '这里的修改会写入服务端的 .env 文件，对所有访问者生效。密钥不会回显，留空表示不修改。'
            : '服务端还没有 .env 文件，保存后会自动创建。密钥不会回显，留空表示不修改。'
    ));

    (data.groups || []).forEach((group, index) => {
        const details = document.createElement('details');
        details.className = 'settings-role env-group';
        details.open = index === 0;

        const summary = document.createElement('summary');
        summary.className = 'settings-role-header';
        summary.textContent = group.title;
        details.appendChild(summary);

        if (group.desc) {
            const desc = document.createElement('div');
            desc.className = 'setting-desc';
            desc.textContent = group.desc;
            details.appendChild(desc);
        }

        (group.fields || []).forEach(field => {
            EnvSettingsState.fields[field.key] = field;
            details.appendChild(_renderEnvField(field));
        });
        container.appendChild(details);
    });
}

function _renderEnvField(field) {
    const wrap = document.createElement('div');
    wrap.className = 'form-group';

    const label = document.createElement('label');
    label.htmlFor = 'env_' + field.key;
    label.textContent = field.label;
    const keyTag = document.createElement('span');
    keyTag.className = 'env-key';
    keyTag.textContent = field.key + (field.restart ? ' · 重启后生效' : '');
    label.appendChild(keyTag);
    wrap.appendChild(label);

    let input;
    if (field.type === 'bool' || field.type === 'select') {
        input = document.createElement('select');
        const options = field.type === 'bool' ? ['true', 'false'] : field.options;
        options.forEach(opt => {
            const el = document.createElement('option');
            el.value = opt;
            el.textContent = field.type === 'bool' ? (opt === 'true' ? '开启' : '关闭') : (opt || '（不限）');
            input.appendChild(el);
        });
        // 未配置时显示默认值，但不算作修改
        field.initial = options.includes(field.value) ? field.value : field.default;
        input.value = field.initial;
    } else {
        input = document.createElement('input');
        input.type = field.type === 'secret' ? 'password' : 'text';
        input.autocomplete = 'off';
        if (field.type === 'int') input.inputMode = 'numeric';
        field.initial = field.type === 'secret' ? '' : (field.value || '');
        input.value = field.initial;
        if (field.type === 'secret') {
            input.placeholder = field.is_set ? '已配置，留空不修改' : '未配置';
        } else {
            input.placeholder = field.default || field.hint || '';
        }
    }
    input.id = 'env_' + field.key;

    if (field.type === 'secret' && field.is_set) {
        const row = document.createElement('div');
        row.className = 'env-secret-row';
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'btn btn-secondary env-clear-btn';
        btn.textContent = '清除';
        btn.addEventListener('click', () => {
            const clearing = !EnvSettingsState.pendingClear.has(field.key);
            if (clearing) {
                EnvSettingsState.pendingClear.add(field.key);
                input.value = '';
            } else {
                EnvSettingsState.pendingClear.delete(field.key);
            }
            input.disabled = clearing;
            input.placeholder = clearing ? '保存后将清除' : '已配置，留空不修改';
            btn.textContent = clearing ? '撤销' : '清除';
        });
        row.appendChild(input);
        row.appendChild(btn);
        wrap.appendChild(row);
    } else {
        wrap.appendChild(input);
    }

    // 文本框没有默认值时 hint 已作为 placeholder 显示，其余情况单独显示一行
    const hintInPlaceholder = input.tagName === 'INPUT' && field.type !== 'secret' && !field.default;
    if (field.hint && !hintInPlaceholder) {
        const hint = document.createElement('div');
        hint.className = 'env-hint';
        hint.textContent = field.hint;
        wrap.appendChild(hint);
    }
    return wrap;
}

function _collectEnvChanges() {
    const values = {};
    Object.values(EnvSettingsState.fields).forEach(field => {
        if (EnvSettingsState.pendingClear.has(field.key)) return;
        const input = document.getElementById('env_' + field.key);
        if (!input) return;
        const value = input.value.trim();
        if (value !== field.initial) values[field.key] = value;
    });
    return { values, clear: Array.from(EnvSettingsState.pendingClear) };
}

/**
 * 保存有改动的服务端配置
 * 返回 {ok, message}；没有改动时 ok=true 且 message 为空
 */
async function saveEnvSettings() {
    if (!EnvSettingsState.loaded) return { ok: true, message: '' };

    const changes = _collectEnvChanges();
    if (!Object.keys(changes.values).length && !changes.clear.length) {
        return { ok: true, message: '' };
    }

    try {
        const resp = await fetch('/api/settings/env', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(changes),
        });
        const payload = await resp.json();
        if (!resp.ok) {
            _showEnvSaveError(payload.error || '保存服务端配置失败');
            return { ok: false, message: '' };
        }

        const data = payload.data || {};
        let message = `✅ 配置已保存，${(data.updated || []).length} 项已写入服务端 .env`;
        if ((data.restart_required || []).length) {
            message += `；${data.restart_required.join('、')} 需要重启服务后生效`;
        }
        if ((data.notes || []).length) {
            message += `；${data.notes.join('；')}`;
        }
        // AI 默认配置可能已变化，刷新前端缓存的服务端默认值
        await loadServerModelConfigs();
        return { ok: true, message };
    } catch (err) {
        _showEnvSaveError('保存服务端配置失败: ' + err.message);
        return { ok: false, message: '' };
    }
}

function _showEnvSaveError(text) {
    // 切到服务端配置 Tab，把错误显示在表单顶部
    document.querySelectorAll('.settings-tab').forEach(t => t.classList.toggle('active', t.dataset.tab === 'env'));
    document.querySelectorAll('.settings-tab-content').forEach(c => c.classList.toggle('active', c.id === 'tabEnv'));

    const body = document.getElementById('envSettingsBody');
    const old = body.querySelector('.env-save-error');
    if (old) old.remove();
    const tip = _envTip('❌ ' + text, true);
    tip.classList.add('env-save-error');
    body.prepend(tip);
    document.getElementById('tabEnv').scrollTop = 0;
}
