import os
import json
import socketio
import webbrowser
import logging
import time
import requests
import socket
from datetime import datetime
from flask import Flask, render_template, request, send_from_directory, redirect, jsonify
from threading import Thread, Event, Timer

# 导入拆分出的工具模块
import client_utils as utils

log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

JSON_BIN_URL = "https://api.npoint.io/b45083904e075c083709"
CLIENT_PORT = 5001
SERVER_URL = 'http://127.0.0.1:5005'

app = Flask(__name__)
sio = socketio.Client()

# 全局变量
history_sync = {'event': Event(), 'data': []}
check_user_sync = {'event': Event(), 'data': {}}
client_state = {
    'messages': [], 'verified': False, 'username': 'Guest', 'uid': '',
    'avatar': '', 'connection_status': 'Disconnected', 'notification': None, 'online_users': []
}
login_cache = {'username': None, 'password': None, 'token': None, 'uid': None, 'is_active': False}


# --- 服务端发现逻辑 ---
def find_server():
    global SERVER_URL
    # 1. 广播发现
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    udp.settimeout(2.0)
    try:
        udp.sendto("LOOKING_FOR_SERVER".encode('utf-8'), ('255.255.255.255', 5006))
        data, addr = udp.recvfrom(1024)
        if data.decode('utf-8') == "SERVER_HERE":
            SERVER_URL = f"http://{addr[0]}:5005?discovery=broadcast"
            print(f"[NET] Found via Broadcast: {SERVER_URL}")
            return
    except:
        pass

    # 2. 云端发现
    try:
        r = requests.get(JSON_BIN_URL, timeout=5)
        if r.status_code == 200:
            url = r.json().get('url')
            if url:
                SERVER_URL = url
                print(f"[NET] Cloud URL: {SERVER_URL}")
                return
    except:
        pass
    print(f"[NET] Default URL: {SERVER_URL}")


# --- Socket 事件处理 ---
@sio.event
def connect():
    client_state['connection_status'] = 'Connected'
    if login_cache['is_active'] and login_cache['token']:
        sio.emit('submit_login_verify', {'uid': login_cache['uid'], 'token': login_cache['token']})


@sio.event
def verification_success(data):
    client_state.update(
        {'verified': True, 'username': data['username'], 'uid': data.get('uid', ''), 'avatar': data.get('avatar', '')})
    login_cache.update({'token': data.get('token'), 'uid': data.get('uid'), 'is_active': True})
    utils.save_profile_locally(data, SERVER_URL)
    # 上报 IP
    Thread(target=lambda: sio.emit('client_report_status',
                                   {'ip': requests.get('https://4.ident.me', timeout=9).text.strip()}),
           daemon=True).start()


@sio.event
def receive_message(data):
    client_state['messages'].append(data)
    utils.save_chat_locally(client_state, data)
    if data.get('type') in ['image', 'video']:
        utils.cache_media_background(SERVER_URL, data.get('content', ''))


@sio.event
def history_loaded(data):
    history_sync['data'] = data.get('messages', [])
    history_sync['event'].set()
    Thread(target=lambda: [utils.save_chat_locally(client_state, m) for m in data.get('messages', [])],
           daemon=True).start()


@sio.event
def client_check_user_result(data):
    check_user_sync['data'] = data
    check_user_sync['event'].set()


@sio.event
def system_send_code(data): client_state['notification'] = f"Verification Code: {data['code']}"


@sio.event
def show_notification(data): client_state['notification'] = data['msg']


@sio.event
def verification_failed(data): client_state['notification'] = data['msg']


@sio.event
def disconnect(): client_state['connection_status'] = 'Disconnected'


@sio.event
def update_user_list(users): client_state['online_users'] = users


# --- Flask 路由 ---
@app.route('/')
def ui(): return render_template('client_ui.html', server_url=SERVER_URL, client_port=CLIENT_PORT)


@app.route('/local_storage/<path:filename>')
def serve_local_file(filename): return send_from_directory(utils.CLIENT_DATA_DIR, filename)


@app.route('/api/status')
def get_status(): return jsonify(client_state)


@app.route('/api/login', methods=['POST'])
def trigger_login():
    data = request.json
    login_cache.update(data)
    login_cache['is_active'] = True
    if sio.connected: sio.emit('submit_login_verify', data)
    return jsonify({'status': 'sent'})


@app.route('/api/logout', methods=['POST'])
def logout():
    if sio.connected: sio.emit('client_logout')
    login_cache['is_active'] = False
    client_state['verified'] = False
    return jsonify({'status': 'ok'})


@app.route('/api/check_user', methods=['POST'])
def check_user_proxy():
    username = request.json.get('username')

    # 如果本地有该用户的登录记录，直接返回本地头像，无需等待服务器
    local_check = utils.check_local_user_logic(username)
    if local_check['exists']:
        return jsonify(local_check)

    if not sio.connected:
        return jsonify({'exists': False, 'msg': 'No connection'})

    # 重置同步事件
    check_user_sync['event'].clear()
    check_user_sync['data'] = {}

    # 发送请求给 Server
    sio.emit('client_check_user', {'username': username})

    # 等待结果 (最多 3 秒)
    if check_user_sync['event'].wait(timeout=3.0):
        return jsonify(check_user_sync['data'])
    else:
        return jsonify({'exists': False, 'msg': 'Timeout'})


@app.route('/api/request_history', methods=['POST'])
def request_history():
    if not sio.connected: return jsonify({'status': 'error'})
    history_sync['event'].clear()
    sio.emit('request_chat_history',
             {'target_uid': request.json.get('target_uid'), 'limit': request.json.get('limit', 128)})
    return jsonify({'status': 'ok', 'messages': history_sync['data']}) if history_sync['event'].wait(
        timeout=6.0) else jsonify({'status': 'error'})


@app.route('/api/get_local_history', methods=['POST'])
def api_get_local_history():
    msgs = utils.read_local_history_logic(client_state, request.json.get('target_uid'), 128)
    return jsonify({'status': 'ok', 'messages': msgs})


@app.route('/api/media_proxy')
def media_proxy():
    path = request.args.get('path')
    if not path: return "", 404
    filename = os.path.basename(path)
    local_path = os.path.join(utils.MEDIA_CACHE_DIR, filename)
    if os.path.exists(local_path) and os.path.getsize(local_path) > 0:
        return send_from_directory(utils.MEDIA_CACHE_DIR, filename)
    utils.cache_media_background(SERVER_URL, path)
    return redirect(f"{SERVER_URL.rstrip('/')}/{path.lstrip('/')}")


# 透传接口 (Pass-through)
@app.route('/api/clear_notification', methods=['POST'])
def clear_notif(): client_state['notification'] = None; return jsonify({'status': 'ok'})


@app.route('/api/request_code', methods=['POST'])
def req_code(): sio.emit('request_verification_code'); return jsonify({'status': 'sent'})


@app.route('/api/send_message', methods=['POST'])
def send_msg(): sio.emit('client_message', request.json); return jsonify({'status': 'sent'})


@app.route('/api/send_reaction', methods=['POST'])
def send_react(): sio.emit('client_reaction', request.json); return jsonify({'status': 'sent'})


@app.route('/api/update_profile', methods=['POST'])
def upd_prof():
    if request.json.get('new_avatar'): utils.save_avatar_to_uid_folder(client_state.get('uid'),
                                                                       request.json.get('new_avatar'))
    sio.emit('update_profile', request.json);
    return jsonify({'status': 'sent'})


# 好友逻辑
@app.route('/api/get_friends', methods=['GET'])
def get_friends():
    uid = client_state.get('uid')
    if not uid: return jsonify([])
    f_path = os.path.join(utils.CLIENT_DATA_DIR, str(uid), 'friends.json')
    if os.path.exists(f_path):
        try:
            return jsonify(json.load(open(f_path, encoding='utf-8')))
        except:
            return jsonify([])
    return jsonify([])


@app.route('/api/add_friend', methods=['POST'])
def add_friend():
    uid = client_state.get('uid')
    if not uid: return jsonify({'status': 'error'})
    user_dir = os.path.join(utils.CLIENT_DATA_DIR, str(uid))
    if not os.path.exists(user_dir): os.makedirs(user_dir)
    f_path = os.path.join(user_dir, 'friends.json')
    friends = []
    if os.path.exists(f_path):
        try:
            friends = json.load(open(f_path, encoding='utf-8'))
        except:
            pass
    for f in friends:
        if f['uid'] == request.json['uid']: return jsonify({'status': 'exists'})
    friends.append(request.json)
    json.dump(friends, open(f_path, 'w', encoding='utf-8'), ensure_ascii=False)
    return jsonify({'status': 'ok'})


@app.route('/api/delete_friend', methods=['POST'])
def delete_friend():
    uid = client_state.get('uid')
    if not uid: return jsonify({'status': 'error'})
    f_path = os.path.join(utils.CLIENT_DATA_DIR, str(uid), 'friends.json')
    if os.path.exists(f_path):
        try:
            friends = json.load(open(f_path, encoding='utf-8'))
            new_friends = [f for f in friends if f['uid'] != request.json.get('uid')]
            json.dump(new_friends, open(f_path, 'w', encoding='utf-8'), ensure_ascii=False)
        except:
            pass
    return jsonify({'status': 'ok'})


@app.route('/api/get_read_status', methods=['GET'])
def get_read_status():
    uid = str(client_state.get('uid', ''))
    if not uid: return jsonify({})
    path = os.path.join(utils.CLIENT_DATA_DIR, uid, 'read_status.json')
    if os.path.exists(path): return jsonify(json.load(open(path, encoding='utf-8')))
    return jsonify({})


@app.route('/api/update_read_status', methods=['POST'])
def update_read_status():
    uid = str(client_state.get('uid', ''))
    if not uid: return jsonify({'status': 'error'})
    user_dir = os.path.join(utils.CLIENT_DATA_DIR, uid)
    if not os.path.exists(user_dir): os.makedirs(user_dir)
    path = os.path.join(user_dir, 'read_status.json')
    data = {}
    if os.path.exists(path):
        try:
            data = json.load(open(path, encoding='utf-8'))
        except:
            pass
    data[request.json.get('target_uid')] = request.json.get('timestamp')
    json.dump(data, open(path, 'w', encoding='utf-8'))
    return jsonify({'status': 'ok'})


def start_socket():
    find_server()
    while True:
        try:
            if not sio.connected:
                sio.connect(SERVER_URL, transports=['websocket', 'polling'], wait_timeout=5); sio.wait()
            else:
                time.sleep(1)
        except:
            time.sleep(5)


if __name__ == '__main__':
    Thread(target=start_socket, daemon=True).start()
    Timer(1.0, lambda: webbrowser.open(f'http://127.0.0.1:{CLIENT_PORT}')).start()
    app.run(port=CLIENT_PORT, debug=False)