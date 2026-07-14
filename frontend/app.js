// app.js

const token = localStorage.getItem('vc_token');
if (!token) {
    window.location.href = '/login_page';
}

let isEditMode = false;
let currentEditTokenId = null;
let currentTokensData = {};
let tokenIdList = [];
let vcTokenIdList = [];
let vcDraftInputs = {};
let vcPollInterval = null;

function escapeHtml(value) {
    return String(value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    if (!container) return;
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(() => toast.remove(), 3200);
}

function showSection(sectionId) {
    document.querySelectorAll('.section-view').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));

    document.getElementById(`section-${sectionId}`).classList.add('active');
    document.getElementById(`nav-${sectionId}`).classList.add('active');

    if (vcPollInterval) {
        clearInterval(vcPollInterval);
        vcPollInterval = null;
    }
    if (typeof logsPollInterval !== 'undefined' && logsPollInterval) {
        clearInterval(logsPollInterval);
        logsPollInterval = null;
    }

    if (sectionId === 'dashboard') {
        loadTokens();
    }
    if (sectionId === 'vc') {
        loadVC();
        vcPollInterval = setInterval(loadVC, 4000);
    }
    if (sectionId === 'logs') {
        fetchTasks();
        logsPollInterval = setInterval(fetchTasks, 2500);
    }
    if (sectionId === 'reactions') {
        refreshActiveCount();
    }
}


async function fetchWithAuth(url, options = {}) {
    options.headers = {
        ...options.headers,
        'Authorization': `Bearer ${token}`
    };
    const response = await fetch(url, options);
    if (response.status === 401) {
        localStorage.removeItem('vc_token');
        window.location.href = '/login_page';
    }
    return response;
}

function captureVcInputs() {
    const values = {};
    vcTokenIdList.forEach((tokenId, index) => {
        const gEl = document.getElementById(`vc-g-${index}`);
        const cEl = document.getElementById(`vc-c-${index}`);
        if (gEl && cEl) {
            values[tokenId] = { guild: gEl.value, channel: cEl.value };
        }
    });
    return values;
}

async function loadTokens() {
    try {
        const res = await fetchWithAuth('/api/tokens');
        if (!res.ok) return;
        currentTokensData = await res.json();
        tokenIdList = Object.keys(currentTokensData);

        let total = 0, online = 0, rpc = 0, vc = 0;
        for (const [, config] of Object.entries(currentTokensData)) {
            total++;
            if (config.status && config.status !== 'offline') online++;
            if (config.rpc?.name) rpc++;
            if (config.voice?.channel_id) vc++;
        }
        document.getElementById('stat-total').innerText = total;
        document.getElementById('stat-online').innerText = online;
        document.getElementById('stat-rpc').innerText = rpc;
        document.getElementById('stat-vc').innerText = vc;

        const grid = document.getElementById('token-grid');
        grid.innerHTML = '';

        if (tokenIdList.length === 0) {
            grid.innerHTML = `
                <div class="empty-state" style="grid-column: 1/-1;">
                    <div class="icon">🎙️</div>
                    <h3>No tokens yet</h3>
                    <p>Add your first Discord token to get started.</p>
                    <button class="btn" onclick="openAddModal()">+ Add Token</button>
                </div>`;
            return;
        }

        tokenIdList.forEach((tokenId, index) => {
            const config = currentTokensData[tokenId];
            const profile = config.profile;
            let displayName = tokenId.substring(0, 15) + '...';
            let avatarHtml = `<div class="token-avatar-placeholder">${displayName.charAt(0).toUpperCase()}</div>`;

            if (profile && profile.username) {
                displayName = profile.global_name || profile.username;
                if (profile.avatar) {
                    const avatarUrl = `https://cdn.discordapp.com/avatars/${profile.id}/${profile.avatar}.png`;
                    avatarHtml = `<img src="${avatarUrl}" class="token-avatar" alt="${escapeHtml(displayName)}">`;
                }
            }

            const status = config.status || 'offline';
            const statusText = config.status_text || 'No custom status';
            const platformBadge = config.platform === 'mobile'
                ? '<span class="badge mobile">📱 Mobile</span>'
                : '<span class="badge">💻 PC</span>';

            const card = document.createElement('div');
            card.className = `token-card ${status}`;
            card.innerHTML = `
                <div class="token-card-accent"></div>
                <div class="token-card-body">
                    <div class="token-card-header">
                        ${avatarHtml}
                        <div class="token-card-name">
                            <div class="name">${escapeHtml(displayName)}</div>
                            <div class="sub">${platformBadge}</div>
                        </div>
                        <span class="badge ${status}"><span class="status-dot ${status}"></span>${escapeHtml(status)}</span>
                    </div>
                    <div class="token-card-meta">
                        <div class="row"><span>Token:</span><span class="val token-blur" title="Hover to reveal">${escapeHtml(tokenId.substring(0, 20))}...</span></div>
                        <div class="row"><span>Status:</span><span class="val">${escapeHtml(statusText)}</span></div>
                        <div class="row"><span>Activity:</span><span class="val">${escapeHtml(config.rpc?.name || 'None')}</span></div>
                        <div class="row"><span>Voice:</span><span class="val">${config.voice?.channel_id ? 'Auto-join enabled' : 'Disabled'}</span></div>
                    </div>
                    <div class="token-card-actions">
                        <button class="btn btn-sm" onclick="openProfileModal(${index})">Edit Profile</button>
                        <button class="btn btn-sm" onclick="openEditModal(${index})">Settings</button>
                        <button class="btn btn-sm" style="background:var(--bg-tertiary); color:var(--text-muted);" onclick="setTokenOffline(${index})">Offline</button>
                        <button class="btn btn-warning btn-sm" onclick="restartToken(${index})">Restart</button>
                        <button class="btn btn-danger btn-sm" onclick="deleteToken(${index})">Delete</button>
                    </div>
                </div>`;
            grid.appendChild(card);
        });
    } catch (err) {
        console.error(err);
    }
}

async function loadVC() {
    try {
        const savedInputs = captureVcInputs();
        Object.assign(vcDraftInputs, savedInputs);

        const res = await fetchWithAuth('/api/vc-states');
        if (!res.ok) return;
        const vcStates = await res.json();
        vcTokenIdList = Object.keys(vcStates);

        const list = document.getElementById('vc-list');
        list.innerHTML = '';

        if (vcTokenIdList.length === 0) {
            list.innerHTML = `
                <div class="empty-state">
                    <div class="icon">🔊</div>
                    <h3>No tokens configured</h3>
                    <p>Add a token from the dashboard first.</p>
                </div>`;
            return;
        }

        vcTokenIdList.forEach((tokenId, index) => {
            const data = vcStates[tokenId];
            const profile = data.profile;
            const vcState = data.vc_state || {};
            const isConnected = !!vcState.connected;
            const draft = vcDraftInputs[tokenId];

            const guildValue = draft?.guild ?? vcState.guild_id ?? '';
            const channelValue = draft?.channel ?? vcState.channel_id ?? '';

            let displayName = profile?.username ? (profile.global_name || profile.username) : (tokenId.substring(0, 15) + '...');
            let avatarHtml = `<div class="vc-card-avatar-ph">${displayName.charAt(0).toUpperCase()}</div>`;
            if (profile?.avatar) {
                const avatarUrl = `https://cdn.discordapp.com/avatars/${profile.id}/${profile.avatar}.png`;
                avatarHtml = `<img src="${avatarUrl}" class="vc-card-avatar-ph" alt="${escapeHtml(displayName)}">`;
            }

            let serverDetailsHtml = `
                <div class="vc-card-status">
                    <span class="badge disconnected">Disconnected</span>
                    <span style="font-size:0.8rem;color:var(--text-muted);">Not in any voice channel</span>
                </div>`;

            if (isConnected) {
                const guildName = vcState.guild_name || vcState.guild_id || 'Unknown Server';
                const channelName = vcState.channel_name || vcState.channel_id || 'Unknown Channel';
                const guildIcon = vcState.guild_icon;
                let guildIconHtml = `<div style="width:32px;height:32px;border-radius:8px;background:var(--blurple);display:flex;align-items:center;justify-content:center;font-weight:bold;font-size:0.8rem;">${escapeHtml(guildName.substring(0, 2).toUpperCase())}</div>`;
                if (guildIcon) {
                    guildIconHtml = `<img src="${guildIcon}" style="width:32px;height:32px;border-radius:8px;" alt="${escapeHtml(guildName)}">`;
                }

                serverDetailsHtml = `
                    <div class="vc-card-status">
                        <div style="display:flex;align-items:center;gap:6px;">
                            <span class="badge connected">Connected</span>
                            <span style="font-size:0.8rem;font-weight:600;color:var(--green);">${escapeHtml(channelName)}</span>
                        </div>
                        <div class="vc-state-info" style="margin-top:6px;">
                            ${guildIconHtml}
                            <div style="display:flex;flex-direction:column;line-height:1.2;">
                                <span class="val" style="font-size:0.85rem;font-weight:600;">${escapeHtml(guildName)}</span>
                                <span style="font-size:0.75rem;color:var(--text-muted);">ID: ${escapeHtml(vcState.guild_id || '')}</span>
                            </div>
                        </div>
                    </div>`;
            }

            const item = document.createElement('div');
            item.className = 'vc-card';
            item.innerHTML = `
                <div class="vc-card-user">
                    ${avatarHtml}
                    <div class="vc-name">${escapeHtml(displayName)}</div>
                </div>
                ${serverDetailsHtml}
                <div class="vc-card-controls">
                    <div class="vc-input-row">
                        <input type="text" id="vc-g-${index}" placeholder="Server ID" value="${escapeHtml(guildValue)}" oninput="saveVcDraft(${index})">
                        <input type="text" id="vc-c-${index}" placeholder="Channel ID" value="${escapeHtml(channelValue)}" oninput="saveVcDraft(${index})">
                    </div>
                    <div class="vc-btn-row">
                        <button class="btn btn-success btn-sm" style="flex:1;" onclick="joinVC(${index})">Join</button>
                        <button class="btn btn-danger btn-sm" style="flex:1;" onclick="disconnectVC(${index})">Disconnect</button>
                    </div>
                </div>`;
            list.appendChild(item);
        });
    } catch (err) {
        console.error(err);
    }
}

function saveVcDraft(index) {
    const tokenId = vcTokenIdList[index];
    if (!tokenId) return;
    const gEl = document.getElementById(`vc-g-${index}`);
    const cEl = document.getElementById(`vc-c-${index}`);
    if (gEl && cEl) {
        vcDraftInputs[tokenId] = { guild: gEl.value, channel: cEl.value };
    }
}

async function joinVC(index) {
    const tokenId = vcTokenIdList[index];
    const guild = document.getElementById(`vc-g-${index}`).value.trim();
    const channel = document.getElementById(`vc-c-${index}`).value.trim();
    if (!guild || !channel) {
        showToast('Server ID and Channel ID are required', 'error');
        return;
    }

    try {
        const response = await fetchWithAuth('/api/vc/join', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                token: tokenId,
                guild_id: guild,
                channel_id: channel,
                self_mute: true,
                self_deaf: false
            })
        });
        if (response.ok) {
            vcDraftInputs[tokenId] = { guild, channel };
            showToast('Join request sent!', 'success');
            loadVC();
        } else {
            showToast('Failed to join voice channel', 'error');
        }
    } catch (err) {
        console.error(err);
        showToast('An error occurred', 'error');
    }
}

async function disconnectVC(index) {
    const tokenId = vcTokenIdList[index];
    const guild = document.getElementById(`vc-g-${index}`).value.trim();
    try {
        const response = await fetchWithAuth('/api/vc/disconnect', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ token: tokenId, guild_id: guild })
        });
        if (response.ok) {
            vcDraftInputs[tokenId] = { guild, channel: '' };
            showToast('Disconnect request sent!', 'success');
            loadVC();
        } else {
            showToast('Failed to disconnect', 'error');
        }
    } catch (err) {
        console.error(err);
        showToast('An error occurred', 'error');
    }
}

async function restartToken(index) {
    const tokenId = tokenIdList[index];
    try {
        const res = await fetchWithAuth(`/api/tokens/${encodeURIComponent(tokenId)}/restart`, { method: 'POST' });
        if (res.ok) {
            showToast('Token restarted!', 'success');
            loadTokens();
        } else {
            showToast('Failed to restart token', 'error');
        }
    } catch (err) {
        console.error(err);
        showToast('An error occurred', 'error');
    }
}

function toggleStreamingUrl() {
    const type = document.getElementById('t-rpc-type').value;
    document.getElementById('streaming-url-group').style.display = type === 'streaming' ? 'block' : 'none';
}

function normalizeUrl(url) {
    const trimmed = url.trim();
    if (!trimmed) return '';
    if (trimmed.startsWith('http://') || trimmed.startsWith('https://')) return trimmed;
    return `https://${trimmed}`;
}

function openAddModal() {
    isEditMode = false;
    currentEditTokenId = null;
    document.getElementById('modal-title').innerText = 'Add New Token';
    document.getElementById('t-token').value = '';
    document.getElementById('t-token').disabled = false;
    document.getElementById('token-form').reset();
    document.getElementById('t-vc-mute').checked = true;
    toggleStreamingUrl();
    document.getElementById('token-modal').classList.add('active');
}

function openEditModal(index) {
    const tokenId = tokenIdList[index];
    const config = currentTokensData[tokenId];
    if (!config) return;

    isEditMode = true;
    currentEditTokenId = tokenId;

    document.getElementById('modal-title').innerText = 'Edit Token';
    document.getElementById('t-token').value = tokenId;
    document.getElementById('t-token').disabled = true;

    document.getElementById('t-status').value = config.status || 'online';
    document.getElementById('t-platform').value = config.platform || 'pc';
    document.getElementById('t-status-text').value = config.status_text || '';

    document.getElementById('t-app-id').value = config.rpc?.application_id || '';
    document.getElementById('t-rpc-type').value = config.rpc?.activity_type || 'playing';
    document.getElementById('t-rpc-url').value = config.rpc?.url || '';
    document.getElementById('t-rpc-name').value = config.rpc?.name || '';
    document.getElementById('t-rpc-details').value = config.rpc?.details || '';
    document.getElementById('t-rpc-state').value = config.rpc?.state || '';
    document.getElementById('t-rpc-large-img').value = config.rpc?.large_image || '';
    document.getElementById('t-rpc-large-text').value = config.rpc?.large_text || '';
    document.getElementById('t-rpc-small-img').value = config.rpc?.small_image || '';
    document.getElementById('t-rpc-small-text').value = config.rpc?.small_text || '';
    document.getElementById('t-rpc-timestamp-start').value = config.rpc?.timestamp_start || '';
    document.getElementById('t-rpc-timestamp-end').value = config.rpc?.timestamp_end || '';
    document.getElementById('t-rpc-btn1-label').value = config.rpc?.btn1_label || '';
    document.getElementById('t-rpc-btn1-url').value = config.rpc?.btn1_url || '';
    document.getElementById('t-rpc-btn2-label').value = config.rpc?.btn2_label || '';
    document.getElementById('t-rpc-btn2-url').value = config.rpc?.btn2_url || '';
    document.getElementById('t-vc-guild').value = config.voice?.guild_id || '';
    document.getElementById('t-vc-channel').value = config.voice?.channel_id || '';
    document.getElementById('t-vc-mute').checked = config.voice?.self_mute !== false;
    document.getElementById('t-vc-deaf').checked = config.voice?.self_deaf === true;
    document.getElementById('t-vc-video').checked = config.voice?.self_video === true;
    document.getElementById('t-vc-stream').checked = config.voice?.self_stream === true;

    toggleStreamingUrl();
    document.getElementById('token-modal').classList.add('active');
}

function closeModal() {
    document.getElementById('token-modal').classList.remove('active');
}

async function saveToken() {
    const tokenId = document.getElementById('t-token').value.trim();
    if (!tokenId) {
        showToast('Token is required', 'error');
        return;
    }

    const btn1Label = document.getElementById('t-rpc-btn1-label').value.trim();
    const btn1Url = normalizeUrl(document.getElementById('t-rpc-btn1-url').value);
    const btn2Label = document.getElementById('t-rpc-btn2-label').value.trim();
    const btn2Url = normalizeUrl(document.getElementById('t-rpc-btn2-url').value);
    const appId = document.getElementById('t-app-id').value.trim();
    const rpcName = document.getElementById('t-rpc-name').value.trim();

    const hasButtons = (btn1Label && btn1Url) || (btn2Label && btn2Url);
    if (hasButtons && !appId) {
        showToast('Application ID is required when using RPC buttons', 'error');
        return;
    }
    if (hasButtons && !rpcName) {
        showToast('Activity Name is required when using RPC buttons', 'error');
        return;
    }

    const config = {
        status: document.getElementById('t-status').value,
        platform: document.getElementById('t-platform').value,
        status_text: document.getElementById('t-status-text').value,
        rpc: {
            application_id: appId,
            activity_type: document.getElementById('t-rpc-type').value,
            url: normalizeUrl(document.getElementById('t-rpc-url').value),
            name: rpcName,
            details: document.getElementById('t-rpc-details').value,
            state: document.getElementById('t-rpc-state').value,
            large_image: document.getElementById('t-rpc-large-img').value,
            large_text: document.getElementById('t-rpc-large-text').value,
            small_image: document.getElementById('t-rpc-small-img').value,
            small_text: document.getElementById('t-rpc-small-text').value,
            timestamp_start: document.getElementById('t-rpc-timestamp-start').value,
            timestamp_end: document.getElementById('t-rpc-timestamp-end').value,
            btn1_label: btn1Label,
            btn1_url: btn1Url,
            btn2_label: btn2Label,
            btn2_url: btn2Url
        },
        voice: {
            guild_id: document.getElementById('t-vc-guild').value,
            channel_id: document.getElementById('t-vc-channel').value,
            self_mute: document.getElementById('t-vc-mute').checked,
            self_deaf: document.getElementById('t-vc-deaf').checked,
            self_video: document.getElementById('t-vc-video').checked,
            self_stream: document.getElementById('t-vc-stream').checked
        }
    };

    try {
        let res;
        if (isEditMode) {
            res = await fetchWithAuth(`/api/tokens/${encodeURIComponent(currentEditTokenId)}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(config)
            });
        } else {
            res = await fetchWithAuth('/api/tokens', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ token: tokenId, config })
            });
        }

        if (res.ok) {
            closeModal();
            showToast('Token saved successfully!', 'success');
            loadTokens();
        } else {
            const data = await res.json();
            showToast('Error: ' + (data.detail || 'Save failed'), 'error');
        }
    } catch (err) {
        console.error(err);
        showToast('An error occurred', 'error');
    }
}

async function deleteToken(index) {
    const tokenId = tokenIdList[index];
    if (!confirm('Are you sure you want to delete this token?')) return;
    try {
        const res = await fetchWithAuth(`/api/tokens/${encodeURIComponent(tokenId)}`, { method: 'DELETE' });
        if (res.ok) {
            delete vcDraftInputs[tokenId];
            showToast('Token deleted', 'success');
            loadTokens();
        }
    } catch (err) {
        console.error(err);
    }
}

async function bulkChangeStatus(status) {
    if (!confirm(`Set all tokens to ${status.toUpperCase()}?`)) return;
    try {
        const res = await fetchWithAuth('/api/tokens/bulk/status', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ status })
        });
        if (res.ok) {
            showToast(`All tokens set to ${status}`, 'success');
            loadTokens();
        } else {
            showToast('Failed to update status', 'error');
        }
    } catch (err) {
        console.error(err);
    }
}

async function bulkRestart() {
    if (!confirm('Restart all token clients?')) return;
    try {
        const res = await fetchWithAuth('/api/tokens/bulk/restart', { method: 'POST' });
        if (res.ok) {
            showToast('All tokens restarted!', 'success');
            loadTokens();
        } else {
            showToast('Failed to restart tokens', 'error');
        }
    } catch (err) {
        console.error(err);
    }
}

async function bulkDisconnectVC() {
    if (!confirm('Disconnect all tokens from voice channels?')) return;
    try {
        const res = await fetchWithAuth('/api/tokens/bulk/disconnect-vc', { method: 'POST' });
        if (res.ok) {
            showToast('Disconnect requests sent!', 'success');
            if (document.getElementById('section-vc').classList.contains('active')) {
                loadVC();
            } else {
                loadTokens();
            }
        } else {
            showToast('Failed to disconnect tokens', 'error');
        }
    } catch (err) {
        console.error(err);
    }
}

function logout() {
    localStorage.removeItem('vc_token');
    window.location.href = '/login_page';
}

// ─── Active Token Counter ─────────────────────────────────────────────────────

let _activeTokenCount = 0;

async function refreshActiveCount() {
    try {
        const res = await fetchWithAuth('/api/active-count');
        if (res.ok) {
            const data = await res.json();
            _activeTokenCount = data.count || 0;
            const badge = document.getElementById('global-token-badge');
            if (badge) {
                badge.textContent = `🟢 ${_activeTokenCount} Active Tokens`;
                badge.style.background = _activeTokenCount > 0 ? 'rgba(87,242,135,0.15)' : 'rgba(237,66,69,0.15)';
                badge.style.color = _activeTokenCount > 0 ? '#57f287' : '#ed4245';
            }
        }
    } catch (err) {
        console.error('Failed to fetch active count', err);
    }
}

function updateTokenCounter(inputId, badgeId) {
    const inputEl = document.getElementById(inputId);
    const badgeEl = document.getElementById(badgeId);
    if (!inputEl || !badgeEl) return;
    const requested = parseInt(inputEl.value) || 0;
    const available = _activeTokenCount;
    const willUse = Math.min(requested, available);
    if (available === 0) {
        badgeEl.textContent = '⚠️ No active tokens available';
        badgeEl.style.color = '#ed4245';
        badgeEl.style.background = 'rgba(237,66,69,0.1)';
    } else if (requested > available) {
        badgeEl.textContent = `⚠️ Using ${available} of ${available} active tokens (requested ${requested})`;
        badgeEl.style.color = '#faa61a';
        badgeEl.style.background = 'rgba(250,166,26,0.1)';
    } else {
        badgeEl.textContent = `✅ Using ${willUse} of ${available} active tokens`;
        badgeEl.style.color = '#57f287';
        badgeEl.style.background = 'rgba(87,242,135,0.08)';
    }
    badgeEl.style.display = 'block';
    badgeEl.style.padding = '4px 10px';
    badgeEl.style.borderRadius = '6px';
    badgeEl.style.fontSize = '0.8rem';
    badgeEl.style.fontWeight = '600';
    badgeEl.style.marginTop = '4px';
}

// ─── Reactions & Logs ────────────────────────────────────────────────────────

document.getElementById('reaction-form')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const btn = e.target.querySelector('button');
    btn.disabled = true;

    const data = {
        channel_id: document.getElementById('channel_id').value,
        message_id: document.getElementById('message_id').value,
        emoji: document.getElementById('emoji').value,
        count: parseInt(document.getElementById('count').value) || 1,
        delay_min: parseFloat(document.getElementById('delay_min').value) || 1.0,
        delay_max: parseFloat(document.getElementById('delay_max').value) || 5.0
    };

    try {
        const res = await fetchWithAuth('/api/react', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });

        if (res.ok) {
            showToast('⚡ Single reaction task started!', 'success');
            e.target.reset();
        } else {
            const err = await res.json();
            showToast('Error: ' + (err.detail || 'Failed to start task'), 'error');
        }
    } catch (err) {
        console.error(err);
        showToast('An error occurred', 'error');
    } finally {
        btn.disabled = false;
    }
});

document.getElementById('react-all-form')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const btn = e.target.querySelector('button');
    btn.disabled = true;

    const data = {
        channel_id: document.getElementById('all_channel_id').value,
        message_id: document.getElementById('all_message_id').value,
        count: parseInt(document.getElementById('all_count').value) || 1,
        delay_min: parseFloat(document.getElementById('all_delay_min').value) || 1.0,
        delay_max: parseFloat(document.getElementById('all_delay_max').value) || 5.0
    };

    try {
        const res = await fetchWithAuth('/api/react-all', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });

        if (res.ok) {
            showToast('📋 Copy all reactions task started!', 'success');
            e.target.reset();
        } else {
            const err = await res.json();
            showToast('Error: ' + (err.detail || 'Failed to start task'), 'error');
        }
    } catch (err) {
        console.error(err);
        showToast('An error occurred', 'error');
    } finally {
        btn.disabled = false;
    }
});

// Emoji Bomb
document.getElementById('react-bomb-form')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const btn = e.target.querySelector('button[type="submit"]');
    btn.disabled = true;

    const data = {
        channel_id: document.getElementById('bomb_channel_id').value,
        message_id: document.getElementById('bomb_message_id').value,
        emojis: document.getElementById('bomb_emojis').value,
        count: parseInt(document.getElementById('bomb_count').value) || 1,
        delay_min: parseFloat(document.getElementById('bomb_delay_min').value) || 0.5,
        delay_max: parseFloat(document.getElementById('bomb_delay_max').value) || 2.0
    };

    try {
        const res = await fetchWithAuth('/api/react-bomb', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });

        if (res.ok) {
            const resp = await res.json();
            showToast('💣 ' + (resp.message || 'Emoji bomb launched!'), 'success');
            e.target.reset();
        } else {
            const err = await res.json();
            showToast('Error: ' + (err.detail || 'Failed to launch emoji bomb'), 'error');
        }
    } catch (err) {
        console.error(err);
        showToast('An error occurred', 'error');
    } finally {
        btn.disabled = false;
    }
});

// Remove Reactions
document.getElementById('react-remove-form')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const btn = e.target.querySelector('button[type="submit"]');
    btn.disabled = true;

    const data = {
        channel_id: document.getElementById('remove_channel_id').value,
        message_id: document.getElementById('remove_message_id').value,
        emoji: document.getElementById('remove_emoji').value,
        count: parseInt(document.getElementById('remove_count').value) || 1,
        delay_min: parseFloat(document.getElementById('remove_delay_min').value) || 0.5,
        delay_max: parseFloat(document.getElementById('remove_delay_max').value) || 2.0
    };

    try {
        const res = await fetchWithAuth('/api/react-remove', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });

        if (res.ok) {
            showToast('🗑️ Remove reaction task started!', 'success');
            e.target.reset();
        } else {
            const err = await res.json();
            showToast('Error: ' + (err.detail || 'Failed to start remove task'), 'error');
        }
    } catch (err) {
        console.error(err);
        showToast('An error occurred', 'error');
    } finally {
        btn.disabled = false;
    }
});

// Scheduled Reaction
let _schedTimer = null;

document.getElementById('react-scheduled-form')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (_schedTimer) {
        showToast('A scheduled reaction is already pending. Cancel it first.', 'error');
        return;
    }

    const channelId = document.getElementById('sched_channel_id').value;
    const messageId = document.getElementById('sched_message_id').value;
    const emoji = document.getElementById('sched_emoji').value;
    const count = parseInt(document.getElementById('sched_count').value) || 1;
    const schedDelay = parseInt(document.getElementById('sched_delay').value) || 10;

    const submitBtn = document.getElementById('sched-submit-btn');
    const cancelBtn = document.getElementById('sched-cancel-btn');
    const countdownEl = document.getElementById('sched-countdown');

    submitBtn.disabled = true;
    if (cancelBtn) cancelBtn.style.display = 'block';
    countdownEl.style.display = 'block';

    let remaining = schedDelay;
    const tick = () => {
        countdownEl.textContent = `⏰ Firing in ${remaining}s...`;
        remaining--;
    };
    tick();
    const interval = setInterval(tick, 1000);

    _schedTimer = setTimeout(async () => {
        clearInterval(interval);
        countdownEl.textContent = '🚀 Sending reactions now...';

        const data = {
            channel_id: channelId,
            message_id: messageId,
            emoji: emoji,
            count: count,
            delay_min: 0.5,
            delay_max: 2.0
        };

        try {
            const res = await fetchWithAuth('/api/react', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data)
            });

            if (res.ok) {
                showToast('⏰ Scheduled reaction fired!', 'success');
            } else {
                const err = await res.json();
                showToast('Error: ' + (err.detail || 'Scheduled reaction failed'), 'error');
            }
        } catch (err) {
            console.error(err);
            showToast('Scheduled reaction failed', 'error');
        } finally {
            _schedTimer = null;
            submitBtn.disabled = false;
            if (cancelBtn) cancelBtn.style.display = 'none';
            setTimeout(() => { countdownEl.style.display = 'none'; }, 2000);
        }
    }, schedDelay * 1000);
});

function cancelScheduledReaction() {
    if (_schedTimer) {
        clearTimeout(_schedTimer);
        _schedTimer = null;
        const submitBtn = document.getElementById('sched-submit-btn');
        const cancelBtn = document.getElementById('sched-cancel-btn');
        const countdownEl = document.getElementById('sched-countdown');
        if (submitBtn) submitBtn.disabled = false;
        if (cancelBtn) cancelBtn.style.display = 'none';
        if (countdownEl) countdownEl.style.display = 'none';
        showToast('⏰ Scheduled reaction cancelled', 'info');
    }
}



let logsPollInterval = null;

async function fetchTasks() {
    try {
        const res = await fetchWithAuth('/api/tasks');
        if (!res.ok) return;
        const data = await res.json();
        const tbody = document.getElementById('logs-table-body');
        
        if (!data.tasks || data.tasks.length === 0) {
            tbody.innerHTML = '<tr><td colspan="6" style="text-align: center; color: var(--text-muted); padding: 20px;">No activity logs found.</td></tr>';
            return;
        }
        
        tbody.innerHTML = '';
        data.tasks.forEach(task => {
            const tr = document.createElement('tr');
            tr.style.borderBottom = '1px solid var(--bg-modifier-accent)';
            
            const timeDate = new Date(task.timestamp * 1000);
            const timeStr = timeDate.toLocaleTimeString();
            
            let statusBadge = '';
            if (task.status === 'Running') statusBadge = '<span class="badge" style="background:var(--yellow);color:#000;">Running</span>';
            else if (task.status === 'Completed') statusBadge = '<span class="badge" style="background:var(--green);color:#fff;">Completed</span>';
            else statusBadge = '<span class="badge" style="background:var(--red);color:#fff;">Failed</span>';
            
            const errStr = task.error_message ? `<div style="font-size:0.75rem;color:var(--red); margin-top: 4px;">${escapeHtml(task.error_message)}</div>` : '';
            
            tr.innerHTML = `
                <td style="padding: 10px;">${timeStr}</td>
                <td style="padding: 10px; font-weight: 500;">${escapeHtml(task.task_type)}</td>
                <td style="padding: 10px; font-size: 0.85rem;">
                    <div>Ch: <span style="color: var(--text-normal);">${escapeHtml(task.channel_id)}</span></div>
                    <div>Msg: <span style="color: var(--text-normal);">${escapeHtml(task.message_id)}</span></div>
                </td>
                <td style="padding: 10px; font-size: 1.2rem;">${escapeHtml(task.emoji)}</td>
                <td style="padding: 10px;">
                    <div style="font-weight: 600;">${task.success_count} / ${task.target_count}</div>
                    <div style="font-size:0.75rem;color:var(--text-muted);">${task.total_attempts} attempts</div>
                </td>
                <td style="padding: 10px;">
                    ${statusBadge}
                    ${errStr}
                </td>
            `;
            tbody.appendChild(tr);
        });
    } catch (err) {
        console.error(err);
    }
}

// ─── Profile Editing ─────────────────────────────────────────────────────────
let currentProfileTokenId = null;

function openProfileModal(index) {
    const tokenId = tokenIdList[index];
    currentProfileTokenId = tokenId;
    const config = currentTokensData[tokenId];
    
    document.getElementById('p-global-name').value = config.profile?.global_name || '';
    document.getElementById('p-bio').value = '';
    document.getElementById('p-avatar').value = '';
    
    document.getElementById('profile-modal').classList.add('active');
}

function closeProfileModal() {
    document.getElementById('profile-modal').classList.remove('active');
}

async function saveProfile() {
    if (!currentProfileTokenId) return;
    const btn = document.getElementById('p-save-btn');
    btn.disabled = true;
    
    const payload = {};
    const globalName = document.getElementById('p-global-name').value;
    if (globalName) payload.global_name = globalName;
    
    const bio = document.getElementById('p-bio').value;
    if (bio) payload.bio = bio;
    
    const avatarInput = document.getElementById('p-avatar');
    if (avatarInput.files && avatarInput.files[0]) {
        const file = avatarInput.files[0];
        const reader = new FileReader();
        reader.onload = async function(e) {
            payload.avatar = e.target.result;
            await sendProfileUpdate(payload);
        };
        reader.readAsDataURL(file);
    } else {
        await sendProfileUpdate(payload);
    }
}

async function sendProfileUpdate(payload) {
    try {
        const res = await fetchWithAuth(`/api/tokens/${encodeURIComponent(currentProfileTokenId)}/profile`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        
        if (res.ok) {
            const data = await res.json();
            closeProfileModal();
            showToast(data.message || 'Profile updated!', 'success');
            loadTokens();
        } else {
            const data = await res.json();
            const detail = data.detail || 'Failed to update profile';
            showToast('❌ ' + detail, 'error');
        }
    } catch (err) {
        console.error(err);
        showToast('An error occurred', 'error');
    } finally {
        document.getElementById('p-save-btn').disabled = false;
    }
}

async function setTokenOffline(index) {
    const tokenId = tokenIdList[index];
    try {
        const res = await fetchWithAuth(`/api/tokens/${encodeURIComponent(tokenId)}/offline`, { method: 'POST' });
        if (res.ok) {
            showToast('Token set to Offline (Invisible)', 'success');
            loadTokens();
        } else {
            showToast('Failed to set token offline', 'error');
        }
    } catch (err) {
        console.error(err);
        showToast('An error occurred', 'error');
    }
}

// ─── Settings & Theming ──────────────────────────────────────────────────────

async function loadSettings() {
    try {
        const res = await fetchWithAuth('/api/settings');
        if (res.ok) {
            const data = await res.json();
            document.documentElement.style.setProperty('--blurple', data.theme_accent);
            document.documentElement.style.setProperty('--bg-primary', data.theme_bg);
            
            const accentInput = document.getElementById('theme-accent');
            const bgInput = document.getElementById('theme-bg');
            if (accentInput) {
                accentInput.value = data.theme_accent;
                document.getElementById('theme-accent-val').innerText = data.theme_accent;
            }
            if (bgInput) {
                bgInput.value = data.theme_bg;
                document.getElementById('theme-bg-val').innerText = data.theme_bg;
            }
        }
    } catch (err) {
        console.error('Failed to load settings', err);
    }
}

document.getElementById('theme-accent')?.addEventListener('input', (e) => {
    document.documentElement.style.setProperty('--blurple', e.target.value);
    document.getElementById('theme-accent-val').innerText = e.target.value;
});

document.getElementById('theme-bg')?.addEventListener('input', (e) => {
    document.documentElement.style.setProperty('--bg-primary', e.target.value);
    document.getElementById('theme-bg-val').innerText = e.target.value;
});

async function saveThemeSettings() {
    const data = {
        theme_accent: document.getElementById('theme-accent').value,
        theme_bg: document.getElementById('theme-bg').value
    };
    try {
        const res = await fetchWithAuth('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });
        if (res.ok) showToast('Theme settings saved!', 'success');
        else showToast('Failed to save settings', 'error');
    } catch (err) {
        showToast('An error occurred', 'error');
    }
}

async function resetThemeSettings() {
    document.getElementById('theme-accent').value = '#5865f2';
    document.getElementById('theme-bg').value = '#0f0f14';
    document.getElementById('theme-accent').dispatchEvent(new Event('input'));
    document.getElementById('theme-bg').dispatchEvent(new Event('input'));
    saveThemeSettings();
}

async function exportData() {
    try {
        const res = await fetchWithAuth('/api/settings/export');
        if (res.ok) {
            const data = await res.json();
            const blob = new Blob([JSON.stringify(data, null, 4)], { type: 'application/json' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `voicecord_backup_${new Date().getTime()}.json`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
            showToast('Data exported successfully!', 'success');
        }
    } catch (err) {
        console.error(err);
        showToast('Failed to export data', 'error');
    }
}

function importData(event) {
    const file = event.target.files[0];
    if (!file) return;
    
    if (!confirm('This will overwrite all existing tokens and settings. Are you sure?')) {
        event.target.value = '';
        return;
    }
    
    const reader = new FileReader();
    reader.onload = async (e) => {
        try {
            const data = JSON.parse(e.target.result);
            const res = await fetchWithAuth('/api/settings/import', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data)
            });
            if (res.ok) {
                showToast('Data imported successfully! Tokens restarting...', 'success');
                setTimeout(() => window.location.reload(), 1500);
            } else {
                showToast('Failed to import data', 'error');
            }
        } catch (err) {
            showToast('Invalid JSON file', 'error');
        }
        event.target.value = '';
    };
    reader.readAsText(file);
}

loadSettings();
showSection('dashboard');
