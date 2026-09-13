/**
 * Clipboard Dispatcher — 前端交互
 */

async function parseApiResponse(resp) {
    if (resp.status === 401) {
        window.location.assign('/login');
        throw new Error('登录状态已失效');
    }
    if (resp.status === 204) {
        return null;
    }
    const payload = await resp.json();
    if (!resp.ok) {
        throw new Error(payload.message || `${resp.status} ${resp.statusText}`);
    }
    return payload;
}

function readInteger(id, fallback) {
    const value = Number.parseInt(document.getElementById(id)?.value || '', 10);
    return Number.isNaN(value) ? fallback : value;
}

const API = {
    async get(path) {
        const resp = await fetch(path, {
            credentials: 'same-origin',
        });
        return parseApiResponse(resp);
    },

    async put(path, data) {
        const resp = await fetch(path, {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json',
            },
            credentials: 'same-origin',
            body: JSON.stringify(data),
        });
        return parseApiResponse(resp);
    },

    async post(path, data) {
        const options = {
            method: 'POST',
            credentials: 'same-origin',
        };
        if (data !== undefined) {
            options.headers = {'Content-Type': 'application/json'};
            options.body = JSON.stringify(data);
        }
        const resp = await fetch(path, options);
        return parseApiResponse(resp);
    },

    async delete(path) {
        const resp = await fetch(path, {
            method: 'DELETE',
            credentials: 'same-origin',
        });
        return parseApiResponse(resp);
    },
};

function formatSyncTime(timestamp) {
    return new Intl.DateTimeFormat('zh-CN', {
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hour12: false,
    }).format(new Date(timestamp * 1000));
}

function renderRecentSyncActivities(activities) {
    const list = document.getElementById('recent-sync-activities');
    if (!list) return;
    list.replaceChildren();
    if (activities.length === 0) {
        const empty = document.createElement('li');
        empty.className = 'sync-activity-empty';
        empty.textContent = '暂无同步活动';
        list.appendChild(empty);
        return;
    }
    const directionLabels = {upload: '上传', download: '下载'};
    const contentLabels = {text: '文字', image: '图片'};
    activities.forEach((activity) => {
        const item = document.createElement('li');
        item.className = 'sync-activity-item';

        const device = document.createElement('strong');
        device.textContent = activity.device_id;
        const detail = document.createElement('span');
        const source = activity.direction === 'download' ? `，来自 ${activity.source_device_id}` : '';
        detail.textContent = `${directionLabels[activity.direction]}了${contentLabels[activity.content_type]}${source}`;
        const time = document.createElement('time');
        time.dateTime = new Date(activity.occurred_at * 1000).toISOString();
        time.textContent = formatSyncTime(activity.occurred_at);

        item.append(device, detail, time);
        list.appendChild(item);
    });
}

// ── Toast ──────────────────────────────────
function showToast(message, type = 'success') {
    let container = document.querySelector('.toast-container');
    if (!container) {
        container = document.createElement('div');
        container.className = 'toast-container';
        document.body.appendChild(container);
    }
    const el = document.createElement('div');
    el.className = `toast ${type}`;
    el.textContent = message;
    container.appendChild(el);
    setTimeout(() => el.remove(), 3000);
}

// ── 仪表盘 ─────────────────────────────────
async function loadDashboard() {
    try {
        const data = await API.get('/api/config/status');
        const el = (id) => document.getElementById(id);

        if (el('server-status')) {
            el('server-status').innerHTML = data.server_enabled
                ? '<span class="status-dot online"></span>运行中'
                : '<span class="status-dot offline"></span>仅客户端';
        }
        if (el('runtime-mode')) {
            const labels = {
                windows_all_in_one: 'Windows 一体化',
                docker_server: 'Docker 容器',
                server_only: '服务端模式',
                client_only: '仅客户端',
            };
            el('runtime-mode').textContent = labels[data.runtime_mode] || data.runtime_mode || '-';
        }
        if (el('sse-count')) el('sse-count').textContent = data.sse_subscribers;
        if (el('server-port')) el('server-port').textContent = data.server_port;
        if (el('app-version')) el('app-version').textContent = data.app_version ?? '-';
        if (el('cross-device-sync-count')) {
            el('cross-device-sync-count').textContent = data.cross_device_sync_count ?? 0;
        }
        renderRecentSyncActivities(data.recent_sync_activities || []);

    } catch (e) {
        console.error('Failed to load dashboard:', e);
    }
}

// ── 配置管理 ────────────────────────────────
async function loadSettings() {
    try {
        const data = await API.get('/api/config/all');

        // 填充表单
        const fill = (id, val) => {
            const el = document.getElementById(id);
            if (!el) return;
            if (el.type === 'checkbox') el.checked = val;
            else el.value = val ?? '';
        };

        fill('cfg-server-enabled', data.server?.enabled);
        fill('cfg-host', data.server?.host);
        fill('cfg-port', data.server?.port);
        fill('cfg-public-base-url', data.server?.public_base_url);
        fill('cfg-ttl', data.server?.clipboard_ttl);
        fill('cfg-max-size', data.server?.max_payload_size);
        fill('cfg-image-sync', data.server?.enable_image_sync);
        fill('cfg-max-image-size', data.server?.max_image_size);
        fill('cfg-server-url', data.client?.server_url);
        fill('cfg-client-device-id', data.client?.device_id);
        const clientToken = document.getElementById('cfg-client-token');
        if (clientToken && data.client?.auth_token_configured) {
            clientToken.placeholder = '已配置，留空保持不变';
        }
        fill('cfg-auto-upload', data.client?.enable_auto_upload);
        fill('cfg-auto-download', data.client?.enable_auto_download);
        fill('cfg-debounce', data.client?.debounce_delay_ms);
        fill('cfg-ignore-empty', data.client?.ignore_empty);
        fill('cfg-auto-start', data.system?.auto_start);
        // 服务端开关联动
        toggleServerFields(data.server?.enabled);
    } catch (e) {
        showToast('加载配置失败: ' + e.message, 'error');
    }
}

function validateWindowsConfiguration(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
        throw new TypeError('配置文件根节点必须是 JSON 对象');
    }
    const {server_url: serverUrl, auth_token: authToken, device_id: deviceId} = value;
    if (typeof serverUrl !== 'string' || !serverUrl) {
        throw new TypeError('配置缺少有效的 server_url');
    }
    let parsedUrl;
    try {
        parsedUrl = new URL(serverUrl);
    } catch (error) {
        throw new TypeError(`server_url 不是完整 URL：${error.message}`);
    }
    if (!['http:', 'https:'].includes(parsedUrl.protocol) || parsedUrl.username || parsedUrl.password) {
        throw new TypeError('server_url 必须是未包含账号信息的 HTTP 或 HTTPS 地址');
    }
    if (typeof authToken !== 'string' || authToken.length < 32 || authToken.length > 512) {
        throw new TypeError('auth_token 长度必须为 32 至 512 个字符');
    }
    if (typeof deviceId !== 'string' || deviceId.trim().length < 1 || deviceId.trim().length > 64) {
        throw new TypeError('device_id 必须是 1 至 64 个字符的设备名称');
    }
    if (/[/?#]/.test(deviceId)) {
        throw new TypeError('device_id 不得包含 /、? 或 #');
    }
    return {
        server_url: serverUrl.replace(/\/+$/, ''),
        auth_token: authToken,
        device_id: deviceId.trim(),
    };
}

async function importWindowsConfiguration(input) {
    const file = input.files?.[0];
    if (!file) return;
    try {
        const configuration = validateWindowsConfiguration(JSON.parse(await file.text()));
        await API.put('/api/config/client/import', configuration);
        document.getElementById('cfg-server-url').value = configuration.server_url;
        document.getElementById('cfg-client-device-id').value = configuration.device_id;
        const tokenInput = document.getElementById('cfg-client-token');
        tokenInput.value = '';
        tokenInput.placeholder = '已通过配置文件导入，留空保持不变';
        showToast('Windows 设备配置已导入并保存');
    } catch (error) {
        showToast(`导入 Windows 配置失败：${error.message}`, 'error');
    } finally {
        input.value = '';
    }
}

function getProvisioningElement(root, role) {
    return root.querySelector(`[data-provisioning-role="${role}"]`);
}

function setProvisioningLink(root, role, url) {
    const link = getProvisioningElement(root, role);
    if (!link) return;
    if (!url) {
        link.removeAttribute('href');
        link.setAttribute('aria-disabled', 'true');
        link.classList.add('is-disabled');
        link.textContent = '快捷指令链接待补充';
        return;
    }
    link.href = url;
    link.removeAttribute('aria-disabled');
    link.classList.remove('is-disabled');
}

function setProvisioningDownload(root, role, content, filename) {
    const link = getProvisioningElement(root, role);
    if (!link) return;
    link.href = `data:application/json;charset=utf-8,${encodeURIComponent(content)}`;
    link.download = filename;
}

function findDeviceRow(deviceId) {
    const rows = document.querySelectorAll('#device-list-body .device-table-row');
    return Array.from(rows).find(row => row.dataset.deviceId === deviceId) || null;
}

function findDeviceProvisioningRow(deviceId) {
    const rows = document.querySelectorAll('#device-list-body .device-assets-row');
    return Array.from(rows).find(row => row.dataset.provisioningDeviceId === deviceId) || null;
}

function removeDeviceProvisioningRow(deviceId) {
    findDeviceProvisioningRow(deviceId)?.remove();
}

function syncProvisioningRowIdentity(row, device) {
    const root = row.querySelector('.device-provisioning');
    if (!root) return;
    const title = getProvisioningElement(root, 'title');
    const mark = getProvisioningElement(root, 'platform-mark');
    if (title) title.textContent = `${device.name} 的配置信息`;
    if (mark) mark.textContent = platformLabel(device.platform).toUpperCase();
}

function renderDeviceProvisioning(data) {
    const template = document.getElementById('device-provisioning-template');
    const deviceRow = findDeviceRow(data.device_id);
    const root = template?.content.firstElementChild?.cloneNode(true);
    if (!deviceRow || !root) return;

    removeDeviceProvisioningRow(data.device_id);
    const assetRow = document.createElement('tr');
    assetRow.className = 'device-assets-row';
    assetRow.dataset.provisioningDeviceId = data.device_id;
    const assetCell = document.createElement('td');
    assetCell.colSpan = 6;
    assetCell.className = 'device-assets-cell';
    assetCell.appendChild(root);
    assetRow.appendChild(assetCell);
    deviceRow.after(assetRow);

    syncProvisioningRowIdentity(assetRow, data);
    const summary = getProvisioningElement(root, 'summary');
    if (summary) summary.textContent = `设备名称 ${data.device_id}，服务地址 ${data.base_url}`;

    const windowsSection = getProvisioningElement(root, 'windows');
    if (data.windows && windowsSection) {
        windowsSection.hidden = false;
        getProvisioningElement(root, 'windows-server-url').value = data.windows.server_url;
        getProvisioningElement(root, 'windows-device-id').value = data.windows.device_id;
        getProvisioningElement(root, 'windows-token').value = data.windows.auth_token;
        setProvisioningDownload(
            root,
            'windows-config-download',
            data.windows.configuration_json,
            data.windows.configuration_filename,
        );
    }

    const iosSection = getProvisioningElement(root, 'ios');
    if (data.ios && iosSection) {
        iosSection.hidden = false;
        setProvisioningLink(root, 'ios-configuration-shortcut-link', data.ios.configuration_shortcut_url);
        setProvisioningLink(root, 'ios-push-shortcut-link', data.ios.push_shortcut_url);
        setProvisioningLink(root, 'ios-pull-shortcut-link', data.ios.pull_shortcut_url);
        setProvisioningLink(root, 'ios-screenshot-upload-shortcut-link', data.ios.screenshot_upload_shortcut_url);
        const configurationShortcutQr = getProvisioningElement(root, 'ios-configuration-shortcut-qr');
        if (configurationShortcutQr) {
            configurationShortcutQr.innerHTML = data.ios.configuration_shortcut_qr_svg;
            if (!data.ios.configuration_shortcut_qr_svg) {
                configurationShortcutQr.textContent = '快捷指令链接待补充';
                configurationShortcutQr.classList.add('provisioning-qr-pending');
            }
        }
        getProvisioningElement(root, 'ios-push-shortcut-qr').innerHTML = data.ios.push_shortcut_qr_svg;
        getProvisioningElement(root, 'ios-pull-shortcut-qr').innerHTML = data.ios.pull_shortcut_qr_svg;
        getProvisioningElement(root, 'ios-screenshot-upload-shortcut-qr').innerHTML = data.ios.screenshot_upload_shortcut_qr_svg;
        getProvisioningElement(root, 'ios-config-qr').innerHTML = data.ios.configuration_qr_svg;
    }

    const taskerSection = getProvisioningElement(root, 'tasker');
    if (data.tasker && taskerSection) {
        taskerSection.hidden = false;
        getProvisioningElement(root, 'tasker-project-qr').src = data.tasker.project_qr_url;
        getProvisioningElement(root, 'tasker-upload-qr').src = data.tasker.upload_qr_url;
        getProvisioningElement(root, 'tasker-download-qr').src = data.tasker.download_qr_url;
        setProvisioningLink(root, 'tasker-project-download', data.tasker.project_url);
        setProvisioningLink(root, 'tasker-upload-download', data.tasker.upload_url);
        setProvisioningLink(root, 'tasker-download-download', data.tasker.download_url);
    }

    const actionButton = deviceRow.querySelector('[data-device-action="provision"]');
    if (actionButton) actionButton.textContent = '收起当前配置';
    root.scrollIntoView({behavior: 'smooth', block: 'nearest'});
}

function toggleDeviceProvisioning(deviceId) {
    const assetRow = findDeviceProvisioningRow(deviceId);
    const deviceRow = findDeviceRow(deviceId);
    if (!assetRow || !deviceRow) return;
    assetRow.hidden = !assetRow.hidden;
    const actionButton = deviceRow.querySelector('[data-device-action="provision"]');
    if (actionButton) actionButton.textContent = assetRow.hidden ? '查看当前配置' : '收起当前配置';
}

async function viewDeviceConfiguration(device) {
    if (findDeviceProvisioningRow(device.device_id)) {
        toggleDeviceProvisioning(device.device_id);
        return;
    }
    try {
        const data = await API.get(`/api/devices/${encodeURIComponent(device.device_id)}/configuration`);
        renderDeviceProvisioning(data);
        showToast(`已读取“${data.name}”的当前配置`);
    } catch (e) {
        showToast(`读取设备当前配置失败：${e.message}`, 'error');
    }
}

async function rotateDeviceConfiguration(device) {
    if (!device.enabled) {
        showToast('请先启用设备', 'error');
        return;
    }
    if (!window.confirm(`重新签发“${device.name}”的 Token 后，旧配置立即失效。确认继续？`)) return;
    try {
        const data = await API.post(`/api/devices/${encodeURIComponent(device.device_id)}/provision`);
        await loadDevices();
        renderDeviceProvisioning(data);
        showToast(`已为“${data.name}”重新签发 Token`);
    } catch (e) {
        showToast(`重新签发设备 Token 失败：${e.message}`, 'error');
    }
}

async function copyProvisioningValue(button, role, label) {
    const root = button.closest('.device-provisioning');
    const source = root ? getProvisioningElement(root, role) : null;
    const value = source?.value || source?.textContent || '';
    if (!value) {
        showToast(`${label}尚未生成`, 'error');
        return;
    }
    try {
        await navigator.clipboard.writeText(value);
        showToast(`${label}已复制`);
    } catch (e) {
        showToast(`复制失败：${e.message}`, 'error');
    }
}

function formatDeviceTime(timestamp) {
    if (!timestamp) return '从未在线';
    return new Date(timestamp * 1000).toLocaleString('zh-CN');
}

function platformLabel(platform) {
    const labels = {windows: 'Windows', ios: 'iOS', android: 'Android'};
    return labels[platform] || platform;
}

function makeDeviceButton(text, className, onClick) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = className;
    button.textContent = text;
    button.addEventListener('click', onClick);
    return button;
}

function makeDeviceActionMenu(device) {
    const menu = document.createElement('details');
    menu.className = 'device-action-menu';
    const summary = document.createElement('summary');
    summary.className = 'btn btn-outline btn-sm';
    summary.textContent = '更多';
    const items = document.createElement('div');
    items.className = 'device-action-menu-items';
    const rotateButton = makeDeviceButton(
        '重新签发 Token',
        'device-menu-item',
        () => rotateDeviceConfiguration(device),
    );
    rotateButton.dataset.deviceAction = 'rotate';
    rotateButton.disabled = !device.enabled;
    items.appendChild(rotateButton);
    items.appendChild(makeDeviceButton(
        '重命名设备',
        'device-menu-item',
        () => renameDevice(device.device_id, device.name),
    ));
    items.appendChild(makeDeviceButton(
        device.enabled ? '禁用设备' : '启用设备',
        'device-menu-item',
        () => setDeviceEnabled(device.device_id, !device.enabled),
    ));
    items.appendChild(makeDeviceButton(
        '删除设备',
        'device-menu-item device-menu-item-danger',
        () => deleteDevice(device.device_id, device.name),
    ));
    menu.append(summary, items);
    return menu;
}

function closeDeviceActionMenus(exceptMenu) {
    for (const menu of document.querySelectorAll('.device-action-menu[open]')) {
        if (menu !== exceptMenu) {
            menu.open = false;
        }
    }
}

function handleDeviceActionMenuClick(event) {
    if (!(event.target instanceof Element)) return;
    closeDeviceActionMenus(event.target.closest('.device-action-menu'));
}

async function loadDevices() {
    const body = document.getElementById('device-list-body');
    if (!body) return;
    const provisioningRows = new Map(
        Array.from(body.querySelectorAll('.device-assets-row')).map(row => [row.dataset.provisioningDeviceId, row]),
    );
    try {
        const devices = await API.get('/api/devices');
        body.replaceChildren();
        if (devices.length === 0) {
            const row = document.createElement('tr');
            const cell = document.createElement('td');
            cell.colSpan = 6;
            cell.style.textAlign = 'center';
            cell.style.color = 'var(--text-muted)';
            cell.textContent = '暂无设备';
            row.appendChild(cell);
            body.appendChild(row);
            return;
        }
        for (const device of devices) {
            const row = document.createElement('tr');
            row.className = 'device-table-row';
            row.dataset.deviceId = device.device_id;
            const values = [
                device.name,
                platformLabel(device.platform),
                formatDeviceTime(device.created_at),
                formatDeviceTime(device.last_seen_at),
                device.enabled ? '已启用' : '已禁用',
            ];
            const labels = ['名称', '平台', '创建时间', '最后在线', '状态'];
            for (const [index, value] of values.entries()) {
                const cell = document.createElement('td');
                cell.dataset.label = labels[index];
                cell.textContent = value;
                row.appendChild(cell);
            }
            const actions = document.createElement('td');
            actions.className = 'device-row-actions';
            actions.dataset.label = '操作';
            const savedProvisioningRow = provisioningRows.get(device.device_id);
            const provisioningButton = makeDeviceButton(
                savedProvisioningRow
                    ? (savedProvisioningRow.hidden ? '查看当前配置' : '收起当前配置')
                    : '查看当前配置',
                'btn btn-primary btn-sm',
                () => viewDeviceConfiguration(device),
            );
            provisioningButton.dataset.deviceAction = 'provision';
            actions.appendChild(provisioningButton);
            actions.appendChild(makeDeviceActionMenu(device));
            row.appendChild(actions);
            body.appendChild(row);
            if (savedProvisioningRow) {
                syncProvisioningRowIdentity(savedProvisioningRow, device);
                body.appendChild(savedProvisioningRow);
            }
        }
    } catch (e) {
        body.replaceChildren();
        const row = document.createElement('tr');
        const cell = document.createElement('td');
        cell.colSpan = 6;
        cell.style.color = 'var(--danger)';
        cell.textContent = `加载设备失败：${e.message}`;
        row.appendChild(cell);
        body.appendChild(row);
    }
}

async function renameDevice(deviceId, currentName) {
    const name = window.prompt('输入新的设备名称', currentName);
    if (name === null) return;
    const normalizedName = name.trim();
    if (!normalizedName) {
        showToast('设备名称不能为空', 'error');
        return;
    }
    try {
        await API.put(`/api/devices/${encodeURIComponent(deviceId)}/name`, {name: normalizedName});
        showToast('设备名称已更新');
        await loadDevices();
    } catch (e) {
        showToast(`重命名设备失败：${e.message}`, 'error');
    }
}

async function createDevice() {
    const nameInput = document.getElementById('device-name');
    const platformInput = document.getElementById('device-platform');
    const name = nameInput?.value.trim() || '';
    if (!name) {
        showToast('请输入设备名称', 'error');
        return;
    }
    try {
        const device = await API.post('/api/devices', {name, platform: platformInput?.value || 'windows'});
        if (nameInput) nameInput.value = '';
        await loadDevices();
        renderDeviceProvisioning(device);
        showToast(`设备“${device.name}”已创建，接入资产已显示在设备列表中`);
    } catch (e) {
        showToast(`创建设备失败：${e.message}`, 'error');
    }
}

async function setDeviceEnabled(deviceId, enabled) {
    try {
        await API.put(`/api/devices/${encodeURIComponent(deviceId)}/enabled`, {enabled});
        if (!enabled) removeDeviceProvisioningRow(deviceId);
        showToast(enabled ? '设备已启用' : '设备已禁用');
        await loadDevices();
    } catch (e) {
        showToast(`更新设备失败：${e.message}`, 'error');
    }
}

async function deleteDevice(deviceId, name) {
    if (!window.confirm(`确认删除设备“${name}”？该设备将立即无法同步。`)) return;
    try {
        await API.delete(`/api/devices/${encodeURIComponent(deviceId)}`);
        removeDeviceProvisioningRow(deviceId);
        showToast('设备已删除');
        await loadDevices();
    } catch (e) {
        showToast(`删除设备失败：${e.message}`, 'error');
    }
}

function toggleServerFields(enabled) {
    const serverFields = document.getElementById('server-fields');
    const clientFields = document.getElementById('client-remote-fields');
    const localCredentialsNote = document.getElementById('local-client-credentials-note');
    if (serverFields) serverFields.style.display = enabled ? 'block' : 'none';
    if (clientFields) clientFields.style.display = enabled ? 'none' : 'block';
    if (localCredentialsNote) localCredentialsNote.style.display = enabled ? 'block' : 'none';
}

async function saveServerConfig() {
    try {
        const dockerRuntime = document.getElementById('settings-page')?.dataset.runtimeMode === 'docker_server';
        const data = {
            enabled: dockerRuntime ? true : (document.getElementById('cfg-server-enabled')?.checked ?? true),
            host: dockerRuntime ? '0.0.0.0' : (document.getElementById('cfg-host')?.value || '0.0.0.0'),
            port: dockerRuntime ? 8000 : readInteger('cfg-port', 8000),
            public_base_url: document.getElementById('cfg-public-base-url')?.value || '',
            clipboard_ttl: readInteger('cfg-ttl', 300),
            max_payload_size: readInteger('cfg-max-size', 5242880),
            enable_image_sync: document.getElementById('cfg-image-sync')?.checked ?? true,
            max_image_size: readInteger('cfg-max-image-size', 5242880),
        };
        await API.put('/api/config/server', data);
        const newToken = document.getElementById('cfg-auth-token')?.value || '';
        if (newToken) {
            await API.put('/api/config/server/token', { auth_token: newToken });
            window.location.assign('/login');
            return;
        }
        showToast(dockerRuntime ? 'Docker 服务配置已保存' : '服务配置已保存（监听地址和端口需重启生效）');
    } catch (e) {
        showToast('保存失败: ' + e.message, 'error');
    }
}

async function checkPublicUrlStatus() {
    const status = document.getElementById('public-url-status');
    if (status) {
        status.textContent = '正在检测...';
        status.style.color = 'var(--text-secondary)';
    }
    try {
        const result = await API.get('/api/config/server/public-url-status');
        if (!status) return;
        status.textContent = result.detail;
        status.style.color = result.state === 'healthy_https' ? 'var(--success)' : 'var(--warning)';
    } catch (e) {
        if (status) {
            status.textContent = `检测失败：${e.message}`;
            status.style.color = 'var(--danger)';
        }
    }
}

async function saveClientConfig() {
    try {
        const data = {
            server_url: document.getElementById('cfg-server-url')?.value || '',
            device_id: document.getElementById('cfg-client-device-id')?.value || '',
            enable_auto_upload: document.getElementById('cfg-auto-upload')?.checked ?? true,
            enable_auto_download: document.getElementById('cfg-auto-download')?.checked ?? true,
            debounce_delay_ms: readInteger('cfg-debounce', 500),
            ignore_empty: document.getElementById('cfg-ignore-empty')?.checked ?? true,
        };
        await API.put('/api/config/client', data);
        const newToken = document.getElementById('cfg-client-token')?.value || '';
        if (newToken) {
            await API.put('/api/config/client/token', { auth_token: newToken });
            document.getElementById('cfg-client-token').value = '';
        }
        showToast('客户端配置已保存');
    } catch (e) {
        showToast('保存失败: ' + e.message, 'error');
    }
}

async function saveSystemConfig() {
    try {
        const data = {
            auto_start: document.getElementById('cfg-auto-start')?.checked ?? false,
        };
        await API.put('/api/config/system', data);
        showToast('系统配置已保存');
    } catch (e) {
        showToast('保存失败: ' + e.message, 'error');
    }
}

// ── 初始化 ──────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    document.addEventListener('click', handleDeviceActionMenuClick);
    if (document.getElementById('dashboard-page')) {
        loadDashboard();
        setInterval(loadDashboard, 5000);
    }
    if (document.getElementById('settings-page')) {
        loadSettings();
    }
    if (document.getElementById('devices-page')) {
        loadDevices();
    }
});
