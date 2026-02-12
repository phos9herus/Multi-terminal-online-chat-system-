import os
import uuid
import json
import socket
import requests
import random
import string
import datetime
import mimetypes
import webbrowser
from threading import Timer, Thread
from flask import Flask, render_template, request, send_from_directory, jsonify
from flask_socketio import SocketIO, emit, join_room
from pyngrok import ngrok, conf

import server_db as db

# 配置
NGROK_TOKEN = "39EjyqSfTr8pL1SSMvoc9qAOBuu_2eYPco4xpuGYFYiiHYXNW"
JSON_BIN_URL = "https://api.npoint.io/b45083904e075c083709"

app = Flask(__name__)
app.config['SECRET_KEY'] = 'real_server_secret_key'
app.config['MAX_CONTENT_LENGTH'] = 130 * 1024 * 1024

socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading', max_http_buffer_size=209715200)

# 全局内存状态
clients = {}  # sid -> info
uid_to_sid = {}  # uid -> sid
user_tokens = {}  # uid -> token
verification_store = {}  # ip -> code
message_reactions = {}  # msg_id -> {type: count}


# --- 辅助功能 ---
def broadcast_user_list():
    safe_list = [{'username': c['username'], 'uid': c['uid'], 'avatar': c.get('avatar', '')} for c in clients.values()
                 if c.get('verified')]
    safe_list.append({'username': 'Admin', 'uid': 'ADMIN', 'avatar': ''})
    socketio.emit('update_user_list', safe_list, to='global_chat')
    socketio.emit('admin_update_client_list', clients, to='admin_room')


def broadcast_full_user_list_to_admin():
    users = db.get_all_users()
    admin_view = []
    online_uids = list(uid_to_sid.keys())
    for u in users:
        is_online = u['uid'] in online_uids
        admin_view.append({
            'uid': u['uid'], 'username': u['username'], 'avatar': u.get('avatar', ''),
            'ip': u.get('last_ip', 'Unknown'), 'last_seen': u.get('last_seen', 'Never'),
            'status': 'online' if is_online else 'offline'
        })
    socketio.emit('admin_user_list_update', admin_view, to='admin_room')


def background_admin_refresh_task():
    while True:
        socketio.sleep(10)
        try:
            broadcast_full_user_list_to_admin()
        except:
            pass


# --- Flask 路由 ---
@app.route('/')
def index(): return "Server is running."


@app.route('/admin')
def admin_ui(): return render_template('server_ui.html')


@app.route('/uploads/media/<path:filename>')
def serve_media(filename): return send_from_directory(db.MEDIA_DIR, filename)


@app.route('/uploads/avatars/<path:filename>')
def serve_avatar(filename): return send_from_directory(db.AVATAR_DIR, filename)


@app.route('/api/avatar/<uid>')
def serve_avatar_by_uid(uid):
    users = db.get_all_users()
    for u in users:
        if u['uid'] == uid and u['avatar']:
            return send_from_directory(db.AVATAR_DIR, os.path.basename(u['avatar']))
    return jsonify({'error': 'No Avatar'}), 404


@app.route('/api/upload_media', methods=['POST', 'OPTIONS'])
def upload_media_http():
    if request.method == 'OPTIONS': return jsonify({'status': 'ok'}), 200, {'Access-Control-Allow-Origin': '*'}
    if 'file' not in request.files: return jsonify({'msg': 'No file'}), 400
    file = request.files['file']
    ext = mimetypes.guess_extension(file.content_type.split(';')[0]) or '.bin'
    fname = f"http_msg_{uuid.uuid4().hex}{ext}"
    fpath = os.path.join(db.MEDIA_DIR, fname)
    try:
        file.save(fpath)
        return jsonify({'status': 'ok', 'url': f"/uploads/media/{fname}"}), 200, {'Access-Control-Allow-Origin': '*'}
    except Exception as e:
        return jsonify({'msg': str(e)}), 500


# --- SocketIO 事件 ---
@socketio.on('connect')
def handle_connect():
    clients[request.sid] = {'ip': request.remote_addr, 'verified': False}
    socketio.emit('admin_update_client_list', clients, to='admin_room')


@socketio.on('client_report_status')
def handle_report(data):
    sid = request.sid
    if clients.get(sid, {}).get('verified'):
        uid = clients[sid]['uid']
        clients[sid]['real_ip'] = data.get('ip')
        db.update_user_status_in_csv(uid, ip=data.get('ip'),
                                     last_seen=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        broadcast_full_user_list_to_admin()


@socketio.on('disconnect')
def handle_disconnect():
    if request.sid in clients:
        uid = clients[request.sid].get('uid')
        if uid:
            db.update_user_status_in_csv(uid, last_seen=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            if uid in uid_to_sid: del uid_to_sid[uid]
        del clients[request.sid]
    broadcast_user_list()
    broadcast_full_user_list_to_admin()


@socketio.on('client_logout')
def handle_logout():
    sid = request.sid

    # 检查 sid 是否存在于 clients 中
    if sid in clients:
        uid = clients[sid].get('uid')

        # 1. 如果之前是登录状态，记录最后在线时间并清理 UID 映射
        if uid:
            ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            db.update_user_status_in_csv(uid, last_seen=ts)
            if uid in uid_to_sid:
                del uid_to_sid[uid]

        # 2. 关键修复：不要删除 clients[sid]，而是重置为 Guest 状态
        # 这样连接依然保留，可以进行下一次登录或获取验证码
        clients[sid].update({
            'verified': False,
            'username': 'Guest',
            'uid': '',
            'avatar': ''
        })

        print(f"[LOGOUT] User logged out, session reset for SID: {sid}")

    # 3. 广播更新用户列表
    broadcast_user_list()
    broadcast_full_user_list_to_admin()


@socketio.on('admin_join')
def handle_admin_join():
    join_room('admin_room')
    history = db.read_recent_logs(os.path.join(db.LOGS_DIR, "global_chat"), 256, message_reactions)
    socketio.emit('admin_history_load', history, to='admin_room')
    broadcast_full_user_list_to_admin()


@socketio.on('request_verification_code')
def gen_code():
    code = ''.join(random.choices(string.digits, k=6))
    verification_store[clients[request.sid]['ip']] = code
    print(f"[SEC] Code for {clients[request.sid]['ip']}: {code}")
    emit('system_send_code', {'code': code})


@socketio.on('submit_login_verify')
def handle_login(data):
    sid = request.sid
    # 令牌重连
    if data.get('token') and data.get('uid'):
        uid = data['uid']
        if user_tokens.get(uid, {}).get('token') == data['token']:
            users = db.get_all_users()
            u = next((r for r in users if r['uid'] == uid), None)
            if u:
                clients[sid].update(
                    {'verified': True, 'username': u['username'], 'uid': uid, 'avatar': u.get('avatar', '')})
                uid_to_sid[uid] = sid;
                join_room('global_chat')
                emit('verification_success',
                     {'username': u['username'], 'uid': uid, 'avatar': u.get('avatar', ''), 'token': data['token']})
                broadcast_user_list()
                return

    # 验证码登录
    real_code = verification_store.get(clients[sid]['ip'])
    if not real_code or data.get('code') != real_code: return emit('verification_failed', {'msg': 'Invalid Code'})

    st, user, uid, ava = db.check_user_login(data.get('username'), data.get('password'))
    if st == 2:
        token = uuid.uuid4().hex
        user_tokens[uid] = {'token': token}
        clients[sid].update({'verified': True, 'username': user, 'uid': uid, 'avatar': ava})
        uid_to_sid[uid] = sid;
        join_room('global_chat')
        emit('verification_success', {'username': user, 'uid': uid, 'avatar': ava, 'token': token})
        broadcast_user_list()
    elif st == 0:
        suc, new_uid = db.add_user_to_csv(data.get('username'), data.get('password'))
        if suc:
            emit('show_notification', {'msg': f'Registered! UID: {new_uid}'})
        else:
            emit('verification_failed', {'msg': 'Username taken'})
    else:
        emit('verification_failed', {'msg': 'Wrong Password'})


@socketio.on('client_message')
def handle_msg(data):
    sid = request.sid
    if not clients.get(sid, {}).get('verified'): return

    sender = clients[sid]['username']
    sender_uid = clients[sid]['uid']
    target_uid = data.get('target_uid') if data.get('target_uid') != 'global' else None

    msg_id = uuid.uuid4().hex
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    payload = {
        'id': msg_id, 'sender': sender, 'uid': sender_uid, 'content': data.get('content'),
        'type': data.get('type', 'text'), 'timestamp': ts, 'temp_id': data.get('temp_id'),
        'target_uid': target_uid, 'quote': data.get('quote'), 'reactions': {}
    }

    db.append_to_chat_log(sender, sender_uid, target_uid, json.dumps(payload), data.get('type'), ts)

    if target_uid:
        emit('receive_message', payload, room=sid)
        if target_uid in uid_to_sid: emit('receive_message', payload, room=uid_to_sid[target_uid])
        emit('receive_message', payload, to='admin_room')
    else:
        emit('receive_message', payload, to='global_chat')
        emit('receive_message', payload, to='admin_room')


@socketio.on('client_reaction')
def handle_reaction(data):
    mid = data.get('msg_id')
    rtype = data.get('reaction_type')
    if not mid or not rtype: return

    if mid not in message_reactions: message_reactions[mid] = {}
    message_reactions[mid][rtype] = message_reactions[mid].get(rtype, 0) + 1

    db.append_reaction_to_file(clients[request.sid]['uid'], data.get('target_uid'), mid, rtype)

    payload = {'msg_id': mid, 'reactions': message_reactions[mid]}
    target_uid = data.get('target_uid')

    if not target_uid or target_uid == 'global':
        socketio.emit('reaction_update', payload, to='global_chat')
    else:
        socketio.emit('reaction_update', payload, room=request.sid)
        if target_uid in uid_to_sid: socketio.emit('reaction_update', payload, room=uid_to_sid[target_uid])
    socketio.emit('reaction_update', payload, to='admin_room')


@socketio.on('request_chat_history')
def handle_history(data):
    if not clients.get(request.sid, {}).get('verified'): return
    target = data.get('target_uid')
    if target == 'global': target = None

    u1, u2 = sorted([str(clients[request.sid]['uid']), str(target)]) if target else (None, None)
    folder = os.path.join(db.LOGS_DIR, f"{u1}_{u2}" if target else "global_chat")

    hist = db.read_recent_logs(folder, data.get('limit', 128), message_reactions)
    emit('history_loaded', {'messages': hist, 'target_uid': data.get('target_uid', 'global')})


@socketio.on('update_profile')
def handle_update_profile(data):
    sid = request.sid
    users = db.get_all_users()
    updated = False
    cur_uid = clients[sid]['uid']

    for row in users:
        if row['uid'] == cur_uid:
            if data.get('new_avatar'):
                url, _ = db.save_base64_file(data.get('new_avatar'), db.AVATAR_DIR, prefix=f"user_{cur_uid}")
                if url: row['avatar'] = url; clients[sid]['avatar'] = url; updated = True
            if data.get('new_username'): row['username'] = data.get('new_username'); clients[sid]['username'] = row[
                'username']; updated = True
            if data.get('new_password'): row['password'] = data.get('new_password'); updated = True
            break

    if updated:
        db.save_all_users(users)
        emit('verification_success',
             {'username': clients[sid]['username'], 'uid': cur_uid, 'avatar': clients[sid]['avatar']})
        broadcast_user_list()


# --- 启动逻辑 ---
def start_ngrok():
    print("\n[BOOT] Starting Ngrok...")
    if NGROK_TOKEN: conf.get_default().auth_token = NGROK_TOKEN
    try:
        url = ngrok.connect(5005).public_url
        print(f"[NGROK] {url}")
        requests.post(JSON_BIN_URL, json={"url": url})
    except Exception as e:
        print(f"[NGROK ERROR] {e}")


if __name__ == '__main__':
    db.init_db()
    message_reactions = db.load_all_reactions()  # 加载历史互动
    start_ngrok()

    t = Thread(target=background_admin_refresh_task);
    t.daemon = True;
    t.start()
    Timer(1.5, lambda: webbrowser.open('http://127.0.0.1:5005/admin')).start()

    print("SERVER STARTED ON 5005")
    socketio.run(app, host='0.0.0.0', port=5005, allow_unsafe_werkzeug=True)