const socket = io();
let currentRoomId = 'Global Chat';
let chatLogs = {};
const renderedFingerprints = new Set();

socket.on('connect', () => {
    console.log("Admin Connected");
    socket.emit('admin_join');
});

// --- 用户列表更新 ---
socket.on('admin_user_list_update', (users) => {
    renderUserList(users);
});

function renderUserList(users) {
    const container = document.getElementById('user-list');
    container.innerHTML = '';
    // 排序：在线在前
    users.sort((a, b) => {
        if (a.status === 'online' && b.status !== 'online') return -1;
        if (a.status !== 'online' && b.status === 'online') return 1;
        return 0;
    });

    users.forEach(u => {
        const isOnline = (u.status === 'online');
        const avatarSrc = u.avatar ? u.avatar : `https://ui-avatars.com/api/?name=${u.username}&background=random`;

        const card = document.createElement('div');
        card.className = 'user-card';
        card.innerHTML = `
            <div class="uc-top">
                <img src="${avatarSrc}" class="uc-avatar" onerror="this.src='https://ui-avatars.com/api/?name=${u.username}'">
                <div class="uc-info">
                    <div class="uc-name">
                        ${u.username}
                        <span class="status-dot ${isOnline ? 'status-online' : 'status-offline'}" title="${u.status}"></span>
                    </div>
                    <div class="uc-uid">UID: ${u.uid}</div>
                </div>
            </div>
            <div class="uc-details">
                <div class="uc-row"><span>IP:</span> <span style="color:${isOnline?'#fff':'#888'}">${u.ip}</span></div>
                <div class="uc-row"><span>Last Seen:</span> <span style="color:#888">${isOnline ? 'Now' : u.last_seen}</span></div>
            </div>
        `;
        // 点击跳转到私聊监控
        card.onclick = () => {
           const roomKey = `ADMIN <-> ${u.uid}`;
           if(!chatLogs[roomKey]) chatLogs[roomKey] = [];
           switchRoom(roomKey);
        };
        container.appendChild(card);
    });
}

// --- 消息处理 ---
socket.on('receive_message', (msg) => {
    let key = 'Global Chat';
    if (msg.target_uid && msg.target_uid !== 'global') {
        if (msg.uid === 'ADMIN' || msg.target_uid === 'ADMIN') {
            const partner = (msg.uid === 'ADMIN') ? msg.target_uid : msg.uid;
            key = `ADMIN <-> ${partner}`;
        } else {
            const ids = [msg.uid, msg.target_uid].sort();
            key = `${ids[0]} <-> ${ids[1]}`;
        }
    }
    if (!chatLogs[key]) chatLogs[key] = [];
    chatLogs[key].push(msg);
    renderRoomList();
    if (currentRoomId === key) renderMessages(key, false);
});

socket.on('admin_history_loaded', (data) => {
    const key = data.room_id;
    const msgs = data.messages;
    // 如果已经有数据，拼接到前面（简化处理，直接覆盖或拼接需视需求而定，这里使用覆盖更新策略以保证顺序）
    chatLogs[key] = msgs;
    if (currentRoomId === key) renderMessages(key, true);
});

socket.on('admin_history_load', (msgs) => {
    chatLogs['Global Chat'] = msgs;
    if (currentRoomId === 'Global Chat') renderMessages('Global Chat', true);
});

function renderRoomList() {
    const list = document.getElementById('room-list');
    list.innerHTML = '';
    addCard(list, 'Global Chat', 'Global Chat');
    for (let key in chatLogs) {
        if (key === 'Global Chat') continue;
        addCard(list, key, key);
    }
}

function addCard(parent, name, id) {
    const div = document.createElement('div');
    div.className = `room-card ${currentRoomId === id ? 'active' : ''}`;
    div.onclick = () => switchRoom(id);
    div.innerHTML = `<div class="room-title">${name}</div>`;
    parent.appendChild(div);
}

function switchRoom(id) {
    currentRoomId = id;
    document.getElementById('chat-header').innerText = id;
    renderRoomList();
    // 请求历史记录
    if (!chatLogs[id] || chatLogs[id].length < 1) socket.emit('admin_request_history', { room_id: id });
    renderMessages(id, true);
}

function renderMessages(key, clearMode = false) {
    const box = document.getElementById('chat-box');
    const msgs = chatLogs[key] || [];
    if (clearMode) {
        box.innerHTML = '';
        renderedFingerprints.clear();
    }
    let hasNew = false;

    msgs.forEach(msg => {
        const fingerprint = `${msg.uid}-${msg.timestamp}-${msg.content}`;
        if (renderedFingerprints.has(fingerprint)) return;
        renderedFingerprints.add(fingerprint);
        hasNew = true;

        const div = document.createElement('div');
        div.className = 'msg-row';
        let content = msg.content;

        if(msg.type === 'image') content = `<img src="${msg.content}" class="media-preview" onclick="window.open(this.src)">`;
        else if(msg.type === 'video') content = `<video src="${msg.content}" class="media-preview" controls></video>`;

        div.innerHTML = `
            <div class="msg-meta">${msg.timestamp} - ${msg.sender} (${msg.uid})</div>
            <div class="msg-content">${content}</div>
        `;
        box.appendChild(div);
    });
    if (hasNew || clearMode) box.scrollTop = box.scrollHeight;
}

function sendAdminMsg() {
    const input = document.getElementById('admin-input');
    const rawText = input.value.trim();
    if (!rawText) return;

    let target = null;
    let contentToSend = rawText;
    const match = rawText.match(/^@(\w+)\s+(.+)/);

    if (match) {
        target = match[1];
        contentToSend = match[2];
    } else {
        if (currentRoomId === 'Global Chat') target = 'global';
        else if (currentRoomId.includes('<->')) {
            const parts = currentRoomId.split(' <-> ');
            if (parts[0] === 'ADMIN') target = parts[1];
            else if (parts[1] === 'ADMIN') target = parts[0];
            else { alert("Private chat view: use @UID to whisper."); return; }
        }
    }
    if (target && contentToSend) {
        socket.emit('admin_send_message', { target_uid: target, content: contentToSend });
        input.value = '';
    }
}