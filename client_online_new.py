import os
import json
import socketio
import webbrowser
import logging
import time
import requests
import socket
import base64
from datetime import datetime
from flask import Flask, render_template, request, send_from_directory, redirect, jsonify
from threading import *

log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CLIENT_DATA_DIR = os.path.join(BASE_DIR, 'client_data')
LOCAL_AVATAR_PATH = os.path.join(CLIENT_DATA_DIR, 'my_avatar.png')

if not os.path.exists(CLIENT_DATA_DIR):
    os.makedirs(CLIENT_DATA_DIR)

JSON_BIN_URL = "https://api.npoint.io/b45083904e075c083709"
CLIENT_PORT = 5001
SERVER_URL = 'http://127.0.0.1:5005'


history_sync = {
    'event': Event(),
    'data': []
}

app = Flask(__name__)
sio = socketio.Client()

client_state = {
    'messages': [],
    'verified': False,
    'username': 'Guest',
    'uid': '',
    'avatar': '',
    'connection_status': 'Disconnected',
    'notification': None,
    'online_users': []
}

login_cache = {'username': None, 'password': None, 'token': None, 'uid': None, 'is_active': False}


# 保存私聊记录到本地
def save_chat_locally(data):
    """
    保存聊天记录到本地。
    修改后逻辑：仅保存自己发送的消息。接收到的消息不保存。
    """
    my_uid = str(client_state.get('uid', ''))
    if not my_uid: return

    # 强制转字符串比较
    msg_uid = str(data.get('uid', ''))           # 发送者
    msg_target = str(data.get('target_uid', '')) # 接收者

    # 如果发送者不是自己，直接忽略，不保存
    if msg_uid != my_uid:
        return

    if msg_uid == 'ADMIN' or msg_target == 'ADMIN':
        return

    # 忽略群聊
    if not msg_target or msg_target == 'global':
        return

    # 发送者是自己，partner_uid 必然是 target_uid
    partner_uid = msg_target

    # 构建保存路径
    date_str = datetime.now().strftime("%Y-%m-%d")
    log_dir = os.path.join(CLIENT_DATA_DIR, my_uid, 'chat_logs', partner_uid)

    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    log_file = os.path.join(log_dir, f"{date_str}.json")

    try:
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(data, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[LOCAL SAVE ERROR] {e}")


def find_server_via_broadcast():
    global SERVER_URL
    print("[NET] Broadcasting...")
    udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    udp_socket.settimeout(2.0)
    try:
        udp_socket.sendto("LOOKING_FOR_SERVER".encode('utf-8'), ('255.255.255.255', 5006))
        data, addr = udp_socket.recvfrom(1024)
        if data.decode('utf-8') == "SERVER_HERE":
            SERVER_URL = f"http://{addr[0]}:5005?discovery=broadcast"
            print(f"[NET] Found via Broadcast: {SERVER_URL}")
            return True
    except:
        pass
    return False


def find_server_logic():
    global SERVER_URL
    if find_server_via_broadcast(): return
    print("[NET] Checking Cloud...")
    try:
        r = requests.get(JSON_BIN_URL, timeout=5)
        if r.status_code == 200:
            url = r.json().get('url')
            if url: SERVER_URL = url; print(f"[NET] Cloud URL: {SERVER_URL}"); return
    except:
        pass
    print(f"[NET] Default URL: {SERVER_URL}")


def save_avatar_locally(b64):
    try:
        if ',' in b64:
            _, e = b64.split(',', 1)
        else:
            return
        with open(LOCAL_AVATAR_PATH, "wb") as f:
            f.write(base64.b64decode(e))
    except:
        pass


@sio.event
def connect():
    client_state['connection_status'] = 'Connected'
    if login_cache['is_active'] and login_cache['token']:
        print(f"[NET] Attempting silent Reconnect for UID: {login_cache['uid']}")
        sio.emit('submit_login_verify', {
            'uid': login_cache['uid'],
            'token': login_cache['token']
        })


@sio.event
def verification_success(data):
    client_state['verified'] = True
    client_state['username'] = data['username']
    client_state['uid'] = data.get('uid', '')
    client_state['avatar'] = data.get('avatar', '')
    login_cache['token'] = data.get('token')
    login_cache['uid'] = data.get('uid')
    login_cache['is_active'] = True


@sio.event
def update_user_list(users):
    client_state['online_users'] = users


@sio.event
def receive_message(data):
    client_state['messages'].append(data)
    # --- 新增：调用保存逻辑 ---
    save_chat_locally(data)


@sio.event
def system_send_code(data):
    code = data['code']
    print(f"\n [CODE] {code} \n")
    client_state['notification'] = f"Verification Code: {code}"


@sio.event
def show_notification(data): client_state['notification'] = data['msg']


@sio.event
def verification_failed(data): client_state['notification'] = data['msg']


@app.route('/')
def ui(): return render_template('client_ui.html', server_url=SERVER_URL, client_port=CLIENT_PORT)


@app.route('/local_storage/<path:filename>')
def serve_local_file(filename): return send_from_directory(CLIENT_DATA_DIR, filename)


@app.route('/api/status')
def get_status(): return jsonify(client_state)


@app.route('/api/clear_notification', methods=['POST'])
def clear_notification():
    client_state['notification'] = None
    return jsonify({'status': 'ok'})


@app.route('/api/request_code', methods=['POST'])
def trigger_request_code():
    if not sio.connected: return jsonify({'status': 'error', 'msg': 'Disconnected'})
    sio.emit('request_verification_code')
    return jsonify({'status': 'sent'})


@app.route('/api/login', methods=['POST'])
def trigger_login():
    data = request.json
    login_cache.update(data);
    login_cache['is_active'] = True
    if sio.connected: sio.emit('submit_login_verify', data)
    return jsonify({'status': 'sent'})


@app.route('/api/logout', methods=['POST'])
def logout():
    try:
        sio.emit('client_logout')
    except:
        pass
    login_cache['is_active'] = False
    client_state['verified'] = False
    return jsonify({'status': 'ok'})


@app.route('/api/update_profile', methods=['POST'])
def update_profile():
    data = request.json
    if data.get('new_avatar'): save_avatar_locally(data.get('new_avatar'))
    sio.emit('update_profile', data)
    return jsonify({'status': 'sent'})


@app.route('/api/send_message', methods=['POST'])
def send_message():
    content = request.json.get('content')
    msg_type = request.json.get('type', 'text')
    temp_id = request.json.get('temp_id')
    target_uid = request.json.get('target_uid', 'global')

    if sio.connected and client_state['verified']:
        sio.emit('client_message', {
            'content': content, 'type': msg_type,
            'temp_id': temp_id, 'target_uid': target_uid
        })
        return jsonify({'status': 'sent'})
    return jsonify({'status': 'error'})


@sio.event
def history_loaded(data):
    """
    当 Server 返回历史记录时触发。
    Server 发给 Python 客户端 (SID已验证)，因此可以收到。
    """
    print(f"[NET] Received history via Socket. Count: {len(data.get('messages', []))}")

    # 将数据存入内存
    history_sync['data'] = data.get('messages', [])

    # 解除 API 接口的阻塞
    history_sync['event'].set()

@app.route('/api/request_history', methods=['POST'])
def request_history():
    """
    网页端调用的接口。
    逻辑：网页 -> Python -> Server -> Python -> 网页
    """
    target_uid = request.json.get('target_uid')
    limit = request.json.get('limit', 128)

    # 安全检查
    if not sio.connected or not client_state['verified']:
        return jsonify({'status': 'error', 'msg': 'Backend not connected or verified'})

    # 重置同步事件，准备等待
    history_sync['event'].clear()
    history_sync['data'] = []

    print(f"[API] Proxying history request for target: {target_uid}")

    # Python 客户端向 Server 发送请求
    sio.emit('request_chat_history', {
        'target_uid': target_uid,
        'limit': limit
    })

    # 阻塞等待 Server 返回 (最多等 3 秒)
    is_received = history_sync['event'].wait(timeout=3.0)

    if is_received:
        # 拿到数据，返回给前端
        return jsonify({
            'status': 'ok',
            'messages': history_sync['data']
        })
    else:
        return jsonify({'status': 'error', 'msg': 'Timeout waiting for server'})


@app.route('/api/get_friends', methods=['GET'])
def get_friends():
    current_uid = client_state.get('uid')
    if not current_uid:
        return jsonify([])
    user_dir = os.path.join(CLIENT_DATA_DIR, str(current_uid))
    friend_file = os.path.join(user_dir, 'friends.json')
    if os.path.exists(friend_file):
        try:
            with open(friend_file, 'r', encoding='utf-8') as f:
                return jsonify(json.load(f))
        except:
            return jsonify([])
    return jsonify([])


@app.route('/api/add_friend', methods=['POST'])
def add_friend():
    current_uid = client_state.get('uid')
    if not current_uid:
        return jsonify({'status': 'error', 'msg': 'Not logged in'})
    data = request.json
    user_dir = os.path.join(CLIENT_DATA_DIR, str(current_uid))
    if not os.path.exists(user_dir): os.makedirs(user_dir)
    friend_file = os.path.join(user_dir, 'friends.json')
    friends = []
    if os.path.exists(friend_file):
        try:
            with open(friend_file, 'r', encoding='utf-8') as f:
                friends = json.load(f)
        except:
            pass
    for f in friends:
        if f['uid'] == data['uid']: return jsonify({'status': 'exists'})
    friends.append(data)
    with open(friend_file, 'w', encoding='utf-8') as f:
        json.dump(friends, f, ensure_ascii=False)
    return jsonify({'status': 'ok'})


@app.route('/api/delete_friend', methods=['POST'])
def delete_friend():
    current_uid = client_state.get('uid')
    if not current_uid:
        return jsonify({'status': 'error', 'msg': 'Not logged in'})

    target_uid = request.json.get('uid')
    if not target_uid:
        return jsonify({'status': 'error', 'msg': 'No UID provided'})

    user_dir = os.path.join(CLIENT_DATA_DIR, str(current_uid))
    friend_file = os.path.join(user_dir, 'friends.json')

    if os.path.exists(friend_file):
        try:
            with open(friend_file, 'r', encoding='utf-8') as f:
                friends = json.load(f)

            # 过滤掉要删除的好友
            new_friends = [f for f in friends if f['uid'] != target_uid]

            with open(friend_file, 'w', encoding='utf-8') as f:
                json.dump(new_friends, f, ensure_ascii=False)

            return jsonify({'status': 'ok'})
        except Exception as e:
            print(f"Delete friend error: {e}")
            return jsonify({'status': 'error', 'msg': str(e)})

    return jsonify({'status': 'ok'})  # File didn't exist, effectively deleted

def start_socket_loop():
    find_server_logic()
    while True:
        try:
            if not sio.connected:
                sio.connect(SERVER_URL, transports=['websocket', 'polling'], wait_timeout=5); sio.wait()
            else:
                time.sleep(1)
        except:
            time.sleep(5)


if __name__ == '__main__':
    t = Thread(target=start_socket_loop);
    t.daemon = True;
    t.start()
    Timer(1.0, lambda: webbrowser.open(f'http://127.0.0.1:{CLIENT_PORT}')).start()
    app.run(port=CLIENT_PORT, debug=False)

