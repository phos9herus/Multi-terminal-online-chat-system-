// 从全局作用域获取配置 (APP_CONFIG 在 HTML 中定义)
const SERVER_URL = APP_CONFIG.SERVER_URL;
const socket = io(SERVER_URL);

let myInfo = { username: '', uid: '', avatar: '' };
let currentTarget = 'global';
let reactionDataCache = {};
let lastReadMap = {};
let friendsList = [];
let currentCardUser = {};
let renderedFingerprints = new Set();
let currentQuote = null;
let currentRoomMessages = [];
let isServerConnected = false;

// --- 初始化逻辑 ---
function initReadStatus() {
    fetch('/api/get_read_status').then(r => r.json()).then(data => {
        lastReadMap = { ...lastReadMap, ...data };
    });
}

function initRandomBackground() {
    const overlay = document.getElementById('auth-overlay');
    if(!overlay) return;
    overlay.querySelectorAll('.orb').forEach(el => el.remove());

    const winW = window.innerWidth;
    const winH = window.innerHeight;
    const count = Math.floor(Math.random() * (6 - 3 + 1)) + 3;
    const colors = ['#0084ff', '#9b59b6', '#1abc9c', '#3498db', '#e67e22', '#2ecc71', '#e74c3c'];
    const placedOrbs = [];

    for (let i = 0; i < count; i++) {
        let orbData = null;
        let attempts = 0;
        const maxAttempts = 100;
        while (attempts < maxAttempts) {
            const maxSize = Math.min(500, Math.min(winW, winH) * 0.6);
            const size = Math.floor(Math.random() * (maxSize - 200 + 1)) + 200;
            const radius = size / 2;
            const left = Math.floor(Math.random() * (winW - size));
            const top = Math.floor(Math.random() * (winH - size));
            const centerX = left + radius;
            const centerY = top + radius;

            let overlap = false;
            for (const existing of placedOrbs) {
                const dx = centerX - existing.x;
                const dy = centerY - existing.y;
                const distance = Math.sqrt(dx * dx + dy * dy);
                if (distance < (radius + existing.radius)) { overlap = true; break; }
            }
            if (!overlap) { orbData = { left, top, size, radius, x: centerX, y: centerY }; placedOrbs.push(orbData); break; }
            attempts++;
        }
        if (!orbData) continue;

        const orb = document.createElement('div');
        orb.classList.add('orb');
        orb.style.width = orbData.size + 'px';
        orb.style.height = orbData.size + 'px';
        orb.style.left = orbData.left + 'px';
        orb.style.top = orbData.top + 'px';
        orb.style.background = colors[Math.floor(Math.random() * colors.length)];
        const duration = Math.floor(Math.random() * (35 - 18 + 1)) + 18;
        const delay = Math.floor(Math.random() * 10) * -1;
        orb.style.animationDuration = duration + 's';
        orb.style.animationDelay = delay + 's';
        overlay.prepend(orb);
    }
}

// --- Socket 事件 ---
socket.on('connect', () => console.log("[Socket] Connected"));

socket.on('history_loaded', (data) => {
    if (data.target_uid === currentTarget || (data.target_uid === 'global' && currentTarget === 'global')) {
        renderMessages(data.messages, false);
    }
});

socket.on('reaction_update', (data) => {
    reactionDataCache[data.msg_id] = data.reactions;
    updateMessageReactions(data.msg_id, data.reactions);
});

// --- 轮询循环 (Status Polling) ---
setInterval(() => {
    fetch('/api/status').then(r => r.json()).then(d => {
        isServerConnected = (d.connection_status === 'Connected');
        const statusLabel = document.getElementById('login-status-indicator');
        const loginBtn = document.querySelector('#auth-overlay .btn-full');

        if (statusLabel) {
            if (isServerConnected) {
                statusLabel.innerHTML = '<i class="fas fa-check-circle"></i> Server Connected';
                statusLabel.style.color = '#2ecc71';
                if(loginBtn) { loginBtn.disabled = false; loginBtn.style.opacity = "1"; }
            } else {
                statusLabel.innerHTML = '<i class="fas fa-circle-notch fa-spin"></i> Connecting...';
                statusLabel.style.color = '#e74c3c';
                if(loginBtn) { loginBtn.disabled = true; loginBtn.style.opacity = "0.6"; }
            }
        }

        if (d.verified) {
            if (document.getElementById('auth-overlay').style.display !== 'none') {
                myInfo = { username: d.username, uid: d.uid, avatar: d.avatar };
                document.getElementById('auth-overlay').style.display = 'none';
                document.getElementById('app').style.display = 'flex';
                document.getElementById('my-name-display').innerText = d.username;
                document.getElementById('my-uid-display').innerText = "UID: " + d.uid;
                if(d.avatar) document.getElementById('my-avatar-img').src = SERVER_URL + d.avatar;

                fetchFriends();
                initReadStatus();
                switchChat('global', 'Global Chat Room');
            } else if (myInfo.username !== d.username) {
                myInfo.username = d.username;
                document.getElementById('my-name-display').innerText = d.username;
            }
            if (d.messages) processDataAndRender(d.messages);
        }

        if (d.notification) {
            const codeMatch = d.notification.match(/Verification Code:\s*(\d{6})/);
            if (codeMatch) {
                const code = codeMatch[1];
                const inputs = [document.getElementById('code'), document.getElementById('st-code')];
                inputs.forEach(i => { if(i) i.value = code; });
            }
            if(d.notification !== "Media sent!") showNotification(d.notification);
            fetch('/api/clear_notification', { method: 'POST' });
        }
    });
}, 800);

// --- 核心渲染逻辑 ---
function parseTimestamp(tsStr) {
    if (!tsStr) return 0;
    return new Date(tsStr.replace(' ', 'T')).getTime();
}

function processDataAndRender(messages) {
    let conversations = {};
    conversations['global'] = { uid: 'global', username: 'Global Chat Room', avatar: '', lastMsg: null, unread: false };

    messages.forEach(m => {
        let key = null;
        if (!m.target_uid || m.target_uid === 'global') key = 'global';
        else if (m.uid === myInfo.uid) key = m.target_uid;
        else if (m.target_uid === myInfo.uid) key = m.uid;

        if (key) {
            if (!conversations[key]) {
                const friend = friendsList.find(f => f.uid === key);
                const partnerName = friend ? friend.username : ((m.uid === myInfo.uid) ? 'User ' + key : m.sender);
                conversations[key] = { uid: key, username: partnerName, avatar: null, lastMsg: null, unread: false };
            }
            conversations[key].lastMsg = m;
            if (key !== 'global' && m.uid === key) conversations[key].username = m.sender;
            if (key !== 'global' && m.uid !== myInfo.uid && currentTarget !== key) {
                const msgTime = parseTimestamp(m.timestamp);
                const lastReadTime = lastReadMap[key] || 0;
                if (msgTime > lastReadTime) conversations[key].unread = true;
            }
        }
    });

    if (currentTarget !== 'global' && !conversations[currentTarget]) {
        const friend = friendsList.find(f => f.uid === currentTarget);
        conversations[currentTarget] = {
            uid: currentTarget,
            username: friend ? friend.username : ('User ' + currentTarget),
            avatar: null, lastMsg: null, unread: false
        };
    }
    renderSidebar(conversations);

    const relevantMsgs = messages.filter(m => {
        if (currentTarget === 'global') return !m.target_uid || m.target_uid === 'global';
        return (m.uid === myInfo.uid && m.target_uid === currentTarget) || (m.uid === currentTarget && m.target_uid === myInfo.uid);
    });

    if (relevantMsgs.length > 0) renderMessages(relevantMsgs, false);
}

function renderSidebar(convs) {
    const container = document.getElementById('chat-tabs-container');
    const sortedKeys = Object.keys(convs).sort((a, b) => {
        if (a === 'global') return -1;
        if (b === 'global') return 1;
        if (a === 'ADMIN') return -1;
        if (b === 'ADMIN') return 1;
        const timeA = convs[a].lastMsg ? convs[a].lastMsg.timestamp : '';
        const timeB = convs[b].lastMsg ? convs[b].lastMsg.timestamp : '';
        return timeB.localeCompare(timeA);
    });

    const validIds = new Set();
    sortedKeys.forEach(key => {
        const c = convs[key];
        const isActive = (key === currentTarget);
        const isGlobal = (key === 'global');
        const domId = `tab-item-${c.uid}`;
        validIds.add(domId);

        let preview = "No messages";
        let timeStr = "";
        if (c.lastMsg) {
            if (c.lastMsg.type === 'image') preview = "[Image]";
            else if (c.lastMsg.type === 'video') preview = "[Video]";
            else preview = c.lastMsg.content.substring(0, 15) + (c.lastMsg.content.length>15 ? "..." : "");

            if (c.lastMsg.timestamp) {
                const parts = c.lastMsg.timestamp.split(' ');
                if(parts.length > 1) {
                    const timeParts = parts[1].split(':');
                    timeStr = `${timeParts[0]}:${timeParts[1]}`;
                }
            }
        } else if (key === 'ADMIN') preview = "System Administrator";

        let div = document.getElementById(domId);
        if (div) {
            const targetClass = `tab-item ${isActive ? 'active' : ''} ${c.unread ? 'unread' : ''}`;
            if (div.className !== targetClass) div.className = targetClass;
            div.querySelector('.tab-preview').innerText = preview;
            div.querySelector('.tab-time').innerText = timeStr;
            div.querySelector('.tab-name').innerText = c.username;
            container.appendChild(div);
        } else {
            div = document.createElement('div');
            div.id = domId;
            div.className = `tab-item ${isActive ? 'active' : ''} ${c.unread ? 'unread' : ''}`;
            div.onclick = () => switchChat(c.uid, c.username);
            let avaSrc = "";
            if (isGlobal) avaSrc = "https://ui-avatars.com/api/?name=Global&background=0084ff&color=fff";
            else if (c.uid === 'ADMIN') avaSrc = "https://ui-avatars.com/api/?name=Admin&background=000&color=fff";
            else avaSrc = `${SERVER_URL}/api/avatar/${c.uid}`;

            div.innerHTML = `<div class="tab-avatar-container"><img src="${avaSrc}" class="tab-avatar" onerror="this.src='https://ui-avatars.com/api/?name=${c.username}'"><div class="unread-dot"></div></div><div class="tab-content"><div class="tab-top"><span class="tab-name">${c.username}</span><span class="tab-time">${timeStr}</span></div><div class="tab-preview">${preview}</div></div>`;
            container.appendChild(div);
        }
    });

    Array.from(container.children).forEach(child => {
        if (!validIds.has(child.id)) container.removeChild(child);
    });
}

function renderMessages(msgs, clearMode = false) {
    const list = document.getElementById('msg-list');
    let tempUploads = [];
    if (clearMode) {
        list.querySelectorAll('[id^="t_"]').forEach(el => tempUploads.push(el));
        list.innerHTML = '';
        renderedFingerprints.clear();
    }
    let hasNewContent = false;
    const hideAdminGlobal = localStorage.getItem('hide_admin_global') === 'true';
    const cleanServerUrl = SERVER_URL.replace(/\/$/, "");

    msgs.forEach(msg => {
        if (currentTarget === 'global' && msg.uid === 'ADMIN' && hideAdminGlobal) return;
        const msgFingerprint = `${msg.uid}-${msg.timestamp}-${msg.content}`;
        if (renderedFingerprints.has(msgFingerprint)) return;
        renderedFingerprints.add(msgFingerprint);
        hasNewContent = true;

        if (msg.reactions) reactionDataCache[msg.id] = msg.reactions;
        else if (!reactionDataCache[msg.id]) reactionDataCache[msg.id] = {};

        const isSelf = (msg.uid === myInfo.uid);
        const row = document.createElement('div');
        row.className = `message-row ${isSelf ? 'self' : 'other'}`;
        const domId = `msg-${msg.id || msg.temp_id || Date.now()}`;
        row.id = domId;

        let fullMediaUrl = msg.content;
        if (msg.content && typeof msg.content === 'string' && msg.content.startsWith('/uploads')) {
            fullMediaUrl = `/api/media_proxy?path=${encodeURIComponent(msg.content)}`;
        }

        let avatarSrc = "";
        if (isSelf) {
            if (myInfo.avatar) {
                if (myInfo.avatar.startsWith('/local_storage')) avatarSrc = myInfo.avatar;
                else if (myInfo.avatar.startsWith('/')) avatarSrc = cleanServerUrl + myInfo.avatar;
                else avatarSrc = myInfo.avatar;
            } else avatarSrc = `https://ui-avatars.com/api/?name=${myInfo.username}`;
        } else {
            avatarSrc = `${cleanServerUrl}/api/avatar/${msg.uid}`;
        }

        const safeSender = msg.sender.replace(/'/g, "\\'");
        let avatarHtml = isSelf
            ? `<img src="${avatarSrc}" class="chat-avatar" onerror="this.src='https://ui-avatars.com/api/?name=${myInfo.username}'">`
            : `<img src="${avatarSrc}" class="chat-avatar" onclick="showUserCard('${msg.uid}', '${safeSender}', '${avatarSrc.replace(/'/g, "\\'")}')" onerror="this.src='https://ui-avatars.com/api/?name=${safeSender}'">`;

        let quoteHtml = '';
        if (msg.quote) {
            let qContent = msg.quote.content;
            if (msg.quote.type === 'image') qContent = '[Image]';
            else if (msg.quote.type === 'video') qContent = '[Video]';
            quoteHtml = `<div class="embedded-quote" onclick="jumpToMessage('${msg.quote.id}')"><div class="quote-sender"><i class="fas fa-quote-left"></i> ${msg.quote.sender}</div><div style="white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${qContent}</div></div>`;
        }

        let contentHtml = '';
        const safeContent = msg.content ? msg.content.replace(/'/g, "\\'") : "";
        const showMenu = !isSelf;
        const menuAction = showMenu ? `onclick="toggleReactionMenu('${domId}', '${msg.id}', '${safeSender}', '${safeContent}')"` : "";
        const cursorStyle = showMenu ? 'cursor:pointer' : '';

        if (msg.type === 'image') contentHtml = `<img src="${fullMediaUrl}" class="chat-media" style="${cursorStyle}" ${isSelf ? `onclick="window.open(this.src)"` : menuAction} loading="lazy">`;
        else if (msg.type === 'video') contentHtml = `<video src="${fullMediaUrl}" class="chat-media" controls preload="metadata"></video>`;
        else contentHtml = `<div class="msg-bubble" style="${cursorStyle}" ${menuAction}>${msg.content}</div>`;

        let menuHtml = '';
        if (showMenu) {
            menuHtml = `<div id="menu-${domId}" class="reaction-menu"><button class="menu-btn quote-btn" title="Reply" onclick="event.stopPropagation(); startQuote('${msg.id}', '${safeSender}', '${safeContent}', '${msg.type}')"><i class="fas fa-reply"></i></button><button class="menu-btn" onclick="event.stopPropagation(); sendReaction('${msg.id}', 'like')">👍</button><button class="menu-btn" onclick="event.stopPropagation(); sendReaction('${msg.id}', 'love')">❤️</button><button class="menu-btn" onclick="event.stopPropagation(); sendReaction('${msg.id}', 'question')">❓</button><button class="menu-btn" onclick="event.stopPropagation(); sendReaction('${msg.id}', 'dislike')">👎</button></div>`;
        }

        let reactionBarHtml = `<div id="reaction-bar-${msg.id}" class="reaction-bar"></div>`;
        const cachedReactions = reactionDataCache[msg.id] || {};
        if (Object.keys(cachedReactions).length > 0) reactionBarHtml = `<div id="reaction-bar-${msg.id}" class="reaction-bar">${buildReactionHtml(cachedReactions)}</div>`;

        row.innerHTML = `${avatarHtml}<div class="msg-content-group"><div class="msg-timestamp">${msg.timestamp || ''}</div><div class="msg-bubble-container">${quoteHtml}${contentHtml}${menuHtml}</div>${reactionBarHtml}</div>`;
        list.appendChild(row);
    });

    if (clearMode) tempUploads.forEach(el => list.appendChild(el));
    if (hasNewContent || clearMode) list.scrollTop = list.scrollHeight;
}

function buildReactionHtml(reactions) {
    const icons = { 'like': '👍', 'love': '❤️', 'question': '❓', 'dislike': '👎' };
    let html = '';
    for (let [type, count] of Object.entries(reactions)) {
        if (count > 0 && icons[type]) html += `<div class="reaction-pill">${icons[type]} ${count}</div>`;
    }
    return html;
}

// --- 用户交互与菜单 ---
function toggleReactionMenu(domId, msgId, sender, content) {
    document.querySelectorAll('.reaction-menu').forEach(el => { if (el.id !== `menu-${domId}`) el.classList.remove('show'); });
    const menu = document.getElementById(`menu-${domId}`);
    if(menu) {
        menu.classList.toggle('show');
        if(menu.classList.contains('show')) {
            setTimeout(() => {
                const closer = (e) => { if(!e.target.closest(`#${domId}`)) { menu.classList.remove('show'); window.removeEventListener('click', closer); }};
                window.addEventListener('click', closer);
            }, 0);
        }
    }
}

function sendReaction(msgId, type) {
    if(!msgId) return;
    if (!reactionDataCache[msgId]) reactionDataCache[msgId] = {};
    reactionDataCache[msgId][type] = (reactionDataCache[msgId][type] || 0) + 1;
    updateMessageReactions(msgId, reactionDataCache[msgId]);

    fetch('/api/send_reaction', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ msg_id: msgId, reaction_type: type, target_uid: currentTarget })
    }).catch(err => console.error(err));
    document.querySelectorAll('.reaction-menu').forEach(el => el.classList.remove('show'));
}

function updateMessageReactions(msgId, reactions) {
    const bar = document.getElementById(`reaction-bar-${msgId}`);
    if(bar) bar.innerHTML = buildReactionHtml(reactions);
}

function startQuote(msgId, sender, content, type = 'text') {
    currentQuote = { id: msgId, sender: sender, content: content, type: type };
    const bar = document.getElementById('quote-preview-bar');
    if (bar) {
        bar.style.display = 'flex';
        document.getElementById('quote-preview-sender').innerText = sender;
        let displayText = content;
        if (type === 'image') displayText = '[Image]';
        else if (type === 'video') displayText = '[Video]';
        document.getElementById('quote-preview-text').innerText = displayText;
        document.getElementById('msg-text').focus();
    }
    document.querySelectorAll('.reaction-menu').forEach(el => el.classList.remove('show'));
}

function cancelQuote() {
    currentQuote = null;
    document.getElementById('quote-preview-bar').style.display = 'none';
}

function jumpToMessage(msgId) {
    const target = document.getElementById(`msg-${msgId}`);
    if (target) {
        target.scrollIntoView({ behavior: 'smooth', block: 'center' });
        target.style.transition = 'background 0.5s';
        target.style.backgroundColor = '#fff3cd';
        setTimeout(() => target.style.backgroundColor = 'transparent', 1500);
    } else showNotification("Message not in current view (too old).");
}

function switchChat(uid, name) {
    currentTarget = uid;
    currentRoomMessages = [];
    const nowTs = Date.now();
    lastReadMap[uid] = nowTs;
    fetch('/api/update_read_status', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ target_uid: uid, timestamp: nowTs }) });

    document.getElementById('current-chat-title').innerText = name || (uid === 'global' ? 'Global Chat Room' : 'Chat');
    const list = document.getElementById('msg-list');
    list.innerHTML = '';
    renderedFingerprints.clear();

    fetch('/api/get_local_history', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ target_uid: uid }) })
    .then(r => r.json())
    .then(resp => { if (resp.status === 'ok' && resp.messages.length > 0) mergeAndRender(resp.messages); })
    .finally(() => fetchServerHistory(uid));
}

function fetchServerHistory(uid) {
    const limit = parseInt(localStorage.getItem('chat_history_limit')) || 128;
    fetch('/api/request_history', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ target_uid: uid, limit: limit }) })
    .then(r => r.json())
    .then(resp => {
        if (resp.status === 'ok') {
            if (resp.messages.length > 0) mergeAndRender(resp.messages);
            else if (currentRoomMessages.length === 0) document.getElementById('msg-list').innerHTML = `<div style="text-align:center; padding-top:50px; color:#ccc;">No history found.</div>`;
        }
    }).catch(err => console.error(err));
}

function mergeAndRender(newMsgs) {
    newMsgs.forEach(msg => {
        const exists = currentRoomMessages.some(m => m.id === msg.id || (m.timestamp === msg.timestamp && m.content === msg.content && m.uid === msg.uid));
        if (!exists) currentRoomMessages.push(msg);
    });
    currentRoomMessages.sort((a, b) => {
        const ta = new Date(a.timestamp.replace(' ', 'T')).getTime();
        const tb = new Date(b.timestamp.replace(' ', 'T')).getTime();
        return ta - tb;
    });
    renderMessages(currentRoomMessages, true);
}

function sendMsg() {
    const input = document.getElementById('msg-text');
    const text = input.value.trim();
    if(!text) return;
    const payload = { content: text, type: 'text', target_uid: currentTarget, quote: currentQuote };
    fetch('/api/send_message', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) });
    input.value = '';
    lastReadMap[currentTarget] = Date.now();
    if(currentQuote) cancelQuote();
}

async function handleMediaUpload(input) {
    if (!input.files[0]) return;
    let file = input.files[0];
    const targetLimitKB = 4096;

    if (file.size / 1024 > targetLimitKB && file.type.startsWith('image/')) {
        showNotification(`Image too large. Compressing...`);
        file = await compressImage(file, targetLimitKB);
    }

    if (file.size / 1024 > targetLimitKB) {
        showNotification(`Unable to compress under 4MB.`);
        input.value = '';
        return;
    }

    const tempId = "t_" + Date.now();
    const list = document.getElementById('msg-list');
    const row = document.createElement('div');
    row.className = "message-row self";
    row.id = tempId;
    row.innerHTML = `<div class="msg-content-group"><div class="upload-progress-container"><div style="font-size:11px; font-weight:bold;">Uploading...</div><progress id="p-${tempId}" value="0" max="100"></progress></div></div>`;
    list.appendChild(row);
    list.scrollTop = list.scrollHeight;

    const fd = new FormData();
    const fileName = (file.name && file.name !== 'image.jpg') ? file.name : "compressed_image.jpg";
    fd.append('file', file, fileName);

    const xhr = new XMLHttpRequest();
    xhr.open('POST', SERVER_URL + '/api/upload_media', true);
    xhr.upload.onprogress = (e) => { if(e.lengthComputable) { const prog = document.getElementById(`p-${tempId}`); if(prog) prog.value = (e.loaded / e.total) * 100; }};
    xhr.onload = () => {
        if (xhr.status === 200) {
            const r = JSON.parse(xhr.responseText);
            fetch('/api/send_message', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ content: r.url, type: file.type.startsWith('video') ? 'video' : 'image', target_uid: currentTarget }) });
            row.remove();
        } else {
            showNotification("Upload failed");
            row.remove();
        }
    };
    xhr.send(fd);
    input.value = '';
}

async function compressImage(file, targetSizeKB) {
    return new Promise((resolve) => {
        const reader = new FileReader();
        reader.readAsDataURL(file);
        reader.onload = (event) => {
            const img = new Image();
            img.src = event.target.result;
            img.onload = () => {
                const canvas = document.createElement('canvas');
                const ctx = canvas.getContext('2d');
                canvas.width = img.width;
                canvas.height = img.height;
                ctx.drawImage(img, 0, 0);
                let quality = 0.95;
                const getBlob = (q) => {
                    const dataUrl = canvas.toDataURL('image/jpeg', q);
                    const byteString = atob(dataUrl.split(',')[1]);
                    const ab = new ArrayBuffer(byteString.length);
                    const ia = new Uint8Array(ab);
                    for (let i = 0; i < byteString.length; i++) ia[i] = byteString.charCodeAt(i);
                    return new Blob([ab], { type: 'image/jpeg' });
                };
                let resultBlob = getBlob(quality);
                while (resultBlob.size / 1024 > targetSizeKB && quality > 0.1) {
                    quality -= 0.05;
                    resultBlob = getBlob(quality);
                }
                resolve(resultBlob);
            };
        };
    });
}

// --- 工具与 UI 辅助 ---
function fetchFriends() {
    fetch('/api/get_friends').then(r => r.json()).then(data => {
        friendsList = data;
        const modal = document.getElementById('friends-modal');
        if (modal && modal.style.display === 'flex') openFriendsModal();
    });
}

function showUserCard(uid, username, avatarSrc) {
    if (uid === myInfo.uid) return;
    currentCardUser = { uid, username, avatar: avatarSrc };
    document.getElementById('uc-avatar').src = avatarSrc;
    document.getElementById('uc-username').innerText = username;
    document.getElementById('uc-uid').innerText = "UID: " + uid;
    document.getElementById('user-card-overlay').style.display = 'flex';
}
function closeUserCard() { document.getElementById('user-card-overlay').style.display = 'none'; }

function startPrivateChatFromCard() {
    if(currentCardUser.uid) { switchChat(currentCardUser.uid, currentCardUser.username); closeUserCard(); }
}

function addFriendAction() {
    fetch('/api/add_friend', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(currentCardUser) })
    .then(r => r.json()).then(d => {
        if(d.status === 'ok') { showNotification("Friend Added!"); fetchFriends(); }
        else if (d.status === 'exists') showNotification("Already Friends");
    });
    closeUserCard();
}

function openFriendsModal() {
    const container = document.getElementById('friends-list-container');
    container.innerHTML = '';
    const displayList = [{uid: 'ADMIN', username: 'System Admin', avatar: ''}, ...friendsList];
    if (displayList.length === 0) container.innerHTML = '<p style="color:#999; text-align:center;">No friends found.</p>';
    else {
        displayList.forEach(f => {
            const div = document.createElement('div');
            div.className = 'friend-item';
            div.onclick = () => { switchChat(f.uid, f.username); closeFriendsModal(); };
            const ava = f.uid === 'ADMIN' ? "https://ui-avatars.com/api/?name=Admin&background=000&color=fff" : `${SERVER_URL}/api/avatar/${f.uid}`;
            let menuHtml = '';
            if (f.uid !== 'ADMIN') {
                menuHtml = `<div class="friend-actions" onclick="event.stopPropagation()"><button class="friend-menu-btn" onclick="toggleFriendMenu(this, '${f.uid}')"><i class="fas fa-ellipsis-v"></i></button><div id="f-menu-${f.uid}" class="friend-dropdown"><div class="friend-dropdown-item" onclick="deleteFriend('${f.uid}')">Delete</div></div></div>`;
            }
            div.innerHTML = `<img src="${ava}" class="friend-avatar" onerror="this.src='https://ui-avatars.com/api/?name=${f.username}'"><div style="flex:1;"><div style="font-weight:bold;">${f.username}</div><div style="font-size:0.8rem; color:#888;">UID: ${f.uid}</div></div>${menuHtml}`;
            container.appendChild(div);
        });
    }
    document.getElementById('friends-modal').style.display = 'flex';
}

function toggleFriendMenu(btn, uid) {
    document.querySelectorAll('.friend-dropdown').forEach(el => el.classList.remove('show'));
    const menu = document.getElementById(`f-menu-${uid}`);
    if (menu) menu.classList.toggle('show');
}

function deleteFriend(uid) {
    if(!confirm("Are you sure?")) return;
    fetch('/api/delete_friend', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ uid: uid }) })
    .then(r => r.json()).then(data => {
        if(data.status === 'ok') { friendsList = friendsList.filter(f => f.uid !== uid); openFriendsModal(); showNotification("Friend deleted"); }
    });
}

function openSettings() {
    const prev = document.getElementById('settings-avatar-preview');
    if(myInfo.avatar) prev.src = SERVER_URL + myInfo.avatar;
    else prev.src = `https://ui-avatars.com/api/?name=${myInfo.username}`;
    document.getElementById('st-username').value = '';
    document.getElementById('st-password').value = '';
    document.getElementById('st-code').value = '';
    document.getElementById('st-history-limit').value = localStorage.getItem('chat_history_limit') || 128;
    document.getElementById('settings-modal').style.display='flex';
}
function closeSettings() { document.getElementById('settings-modal').style.display='none'; }
function closeFriendsModal() { document.getElementById('friends-modal').style.display = 'none'; }

function checkPasswordInput() {
    document.getElementById('btn-settings-code').disabled = (document.getElementById('st-password').value.length === 0);
}

function uploadAvatarImmediately(input) {
    if(input.files[0]) {
        const r = new FileReader();
        r.onload = e => {
            const b64 = e.target.result;
            document.getElementById('settings-avatar-preview').src = b64;
            document.getElementById('my-avatar-img').src = b64;
            fetch('/api/update_profile', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ new_avatar: b64 }) })
            .then(() => showNotification("Avatar Updated!"));
        };
        r.readAsDataURL(input.files[0]);
    }
}

function saveProfile() {
    const u = document.getElementById('st-username').value.trim();
    const p = document.getElementById('st-password').value.trim();
    const c = document.getElementById('st-code').value.trim();
    const l = document.getElementById('st-history-limit').value.trim();
    if(l) localStorage.setItem('chat_history_limit', l);
    if(p && !c) { document.getElementById('st-error-msg').style.display='block'; return; }

    const data = {};
    if(u) data.new_username = u;
    if(p) { data.new_password = p; data.code = c; }

    if(Object.keys(data).length > 0) {
        fetch('/api/update_profile', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data) })
        .then(() => { showNotification("Settings Saved"); if(u) { myInfo.username = u; document.getElementById('my-name-display').innerText = u; } closeSettings(); });
    } else { showNotification("Settings Saved"); closeSettings(); }
}

function showNotification(msg) {
    const bar = document.getElementById('notification-bar');
    document.getElementById('notify-content').innerHTML = msg;
    bar.classList.add('show');
    setTimeout(() => bar.classList.remove('show'), 4000);
}

function requestCode() { fetch('/api/request_code', {method:'POST'}); }
function doLogin() {
    const data = { username: document.getElementById('username').value, password: document.getElementById('password').value, code: document.getElementById('code').value };
    fetch('/api/login', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data) });
}
function doLogout() {
    fetch('/api/logout', {method:'POST'}).then(() => window.location.reload());
}

let userCheckTimer;
const usernameInput = document.getElementById('username');
const halo = document.getElementById('login-halo');

usernameInput.addEventListener('input', function(e) {
    const val = e.target.value.trim();
    if (!isServerConnected || val.length === 0) { if (!halo.querySelector('i')) halo.innerHTML = '<i class="fas fa-comments"></i>'; return; }
    clearTimeout(userCheckTimer);
    userCheckTimer = setTimeout(() => {
        fetch('/api/check_user', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({username: val}) })
        .then(r => r.json()).then(data => {
            if (data.exists) {
                let avatarUrl = '';
                if (data.avatar) {
                    if (data.avatar.startsWith('/local_storage') || data.avatar.startsWith('data:')) {
                        // 本地路径 (由 Client Flask 5001 托管) 或 Base64，直接使用
                        avatarUrl = data.avatar;
                    } else {
                        // 服务器路径 (由 Server Flask 5005 托管)，需要拼接 SERVER_URL
                        avatarUrl = SERVER_URL + data.avatar;
                    }
                } else {
                    // 无头像，使用默认生成
                    avatarUrl = `https://ui-avatars.com/api/?name=${val}&background=random`;
                }
                halo.innerHTML = `<img src="${avatarUrl}" onerror="this.src='https://ui-avatars.com/api/?name=${val}'">`;
            } else if (!halo.querySelector('i')) halo.innerHTML = '<i class="fas fa-comments"></i>';
        });
    }, 500);
});

window.addEventListener('load', initRandomBackground);
window.addEventListener('resize', () => { setTimeout(initRandomBackground, 300); });
window.addEventListener('click', function(e) { if (!e.target.closest('.friend-actions')) document.querySelectorAll('.friend-dropdown').forEach(el => el.classList.remove('show')); });