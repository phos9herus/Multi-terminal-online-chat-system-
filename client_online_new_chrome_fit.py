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
from flask import Flask, render_template, request, send_from_directory, redirect, jsonify, Response, stream_with_context
from threading import *

log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CLIENT_DATA_DIR = os.path.join(BASE_DIR, 'client_data')
LOCAL_AVATAR_PATH = os.path.join(CLIENT_DATA_DIR, 'my_avatar.png')
MEDIA_CACHE_DIR = os.path.join(CLIENT_DATA_DIR, 'media_cache')

if not os.path.exists(CLIENT_DATA_DIR):
    os.makedirs(CLIENT_DATA_DIR)


if not os.path.exists(MEDIA_CACHE_DIR):
    os.makedirs(MEDIA_CACHE_DIR)



JSON_BIN_URL = "https://api.npoint.io/b45083904e075c083709"
CLIENT_PORT = 5001
SERVER_URL = 'http://127.0.0.1:5005'


history_sync = {
    'event': Event(),
    'data': []
}

check_user_sync = {
    'event': Event(),
    'data': {}
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
    保存聊天记录到本地文件 (JSONL格式)。
    修复：写入前检查 ID 是否已存在，防止重复。
    """
    my_uid = str(client_state.get('uid', ''))
    if not my_uid: return

    # 确定对话对象
    msg_uid = str(data.get('uid', ''))
    msg_target = str(data.get('target_uid', ''))

    # 简单的 ID 校验，防止空数据
    msg_id = data.get('id')
    if not msg_id: return

    if msg_target == 'global':
        partner_uid = 'global'
    elif msg_uid == my_uid:
        partner_uid = msg_target
    else:
        partner_uid = msg_uid

    # 构建保存路径
    log_dir = os.path.join(CLIENT_DATA_DIR, my_uid, 'chat_logs', partner_uid)
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    # 按天存储
    date_str = datetime.now().strftime("%Y-%m-%d")
    log_file = os.path.join(log_dir, f"{date_str}.json")

    # 去重逻辑
    existing_ids = set()
    if os.path.exists(log_file):
        try:
            with open(log_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            record = json.loads(line)
                            if 'id' in record:
                                existing_ids.add(record['id'])
                        except:
                            pass
        except Exception as e:
            print(f"[LOCAL READ ERROR] {e}")

    # 只有当 ID 不存在时才写入
    if msg_id not in existing_ids:
        try:
            with open(log_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(data, ensure_ascii=False) + "\n")
        except Exception as e:
            print(f"[LOCAL SAVE ERROR] {e}")


def read_local_history_logic(target_uid, limit=200):
    """读取本地存储的最近聊天记录"""
    my_uid = str(client_state.get('uid', ''))
    if not my_uid: return []

    log_dir = os.path.join(CLIENT_DATA_DIR, my_uid, 'chat_logs', str(target_uid))
    if not os.path.exists(log_dir):
        return []

    messages = []
    # 获取该目录下所有 json 文件，按文件名(日期)排序
    files = sorted([f for f in os.listdir(log_dir) if f.endswith('.json')])

    # 从最新的文件开始读，直到凑够 limit 条
    for filename in reversed(files):
        path = os.path.join(log_dir, filename)
        day_msgs = []
        try:
            with open(path, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.strip():
                        try:
                            day_msgs.append(json.loads(line))
                        except:
                            pass
            messages = day_msgs + messages  # 保持时间顺序拼接到前面
            if len(messages) >= limit:
                break
        except:
            pass

    return messages[-limit:]  # 返回最后 limit 条


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


def download_and_save_avatar(uid, server_path):
    """
    后台任务：从服务器下载头像并覆盖本地缓存
    """
    if not server_path or not uid: return

    # 构造完整 URL
    if server_path.startswith('http'):
        url = server_path
    else:
        # 拼接 SERVER_URL，注意处理斜杠
        base = SERVER_URL.rstrip('/')
        path = server_path.lstrip('/')
        url = f"{base}/{path}"

    try:
        # print(f"[SYNC] Downloading avatar for {uid} from {url}...")
        headers = {"ngrok-skip-browser-warning": "true"}
        response = requests.get(url, stream=True, timeout=20, headers=headers)
        if response.status_code == 200:
            user_dir = os.path.join(CLIENT_DATA_DIR, str(uid))
            if not os.path.exists(user_dir):
                os.makedirs(user_dir)

            # 覆盖写入 avatar.png
            file_path = os.path.join(user_dir, 'avatar.png')
            with open(file_path, 'wb') as f:
                f.write(response.content)
            # print(f"[SYNC] Avatar saved locally for {uid}")
    except Exception as e:
        print(f"[SYNC] Download failed: {e}")


def save_avatar_to_uid_folder(uid, b64_str):
    """将 Base64 头像保存为 client_data/<uid>/avatar.png"""
    if not uid: return
    user_dir = os.path.join(CLIENT_DATA_DIR, str(uid))
    if not os.path.exists(user_dir):
        os.makedirs(user_dir)

    try:
        if ',' in b64_str:
            _, encoded = b64_str.split(',', 1)
        else:
            encoded = b64_str

        file_path = os.path.join(user_dir, 'avatar.png')
        with open(file_path, "wb") as f:
            f.write(base64.b64decode(encoded))
    except Exception as e:
        print(f"[LOCAL SAVE] Avatar save failed: {e}")


def save_profile_locally(data):
    """
    登录成功后保存用户基本信息。
    逻辑增强：
    1. 如果头像是 Base64 (注册/更新时)，直接保存文件。
    2. 如果头像是 URL (登录时)，启动线程下载并保存文件。
    """
    uid = str(data.get('uid', ''))
    if not uid: return

    user_dir = os.path.join(CLIENT_DATA_DIR, uid)
    if not os.path.exists(user_dir):
        os.makedirs(user_dir)

    profile_path = os.path.join(user_dir, 'profile.json')

    avatar_val = data.get('avatar', '')

    # 保存 JSON 资料
    profile_data = {
        'uid': uid,
        'username': data.get('username', ''),
        'avatar': avatar_val,
        'last_login': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

    try:
        with open(profile_path, 'w', encoding='utf-8') as f:
            json.dump(profile_data, f, ensure_ascii=False)

        # 处理头像文件同步
        if avatar_val:
            if avatar_val.startswith('data:image'):
                # 情况 A: Base64 (本地直接保存)
                save_avatar_to_uid_folder(uid, avatar_val)
            else:
                # 情况 B: 服务器路径 (启动线程下载)
                # 使用线程防止阻塞登录过程
                Thread(target=download_and_save_avatar, args=(uid, avatar_val)).start()

    except Exception as e:
        print(f"[LOCAL SAVE] Profile save failed: {e}")


def get_read_status_file_path():
    uid = str(client_state.get('uid', ''))
    if not uid: return None
    user_dir = os.path.join(CLIENT_DATA_DIR, uid)
    if not os.path.exists(user_dir): os.makedirs(user_dir)
    return os.path.join(user_dir, 'read_status.json')


def cache_media_background(relative_path):
    """
    后台线程：下载媒体文件到本地缓存
    relative_path: 例如 "/uploads/media/xxx.jpg"
    """
    if not relative_path or not relative_path.startswith('/uploads/'):
        return

    # 提取文件名作为本地存储名
    filename = os.path.basename(relative_path)
    local_path = os.path.join(MEDIA_CACHE_DIR, filename)

    # 如果已存在且大小不为0，跳过
    if os.path.exists(local_path) and os.path.getsize(local_path) > 0:
        return

    def download_task():
        try:
            # 构造下载链接 (即使 SERVER_URL 变了，这里使用当前的也是对的)
            # 注意去除重复斜杠
            base = SERVER_URL.rstrip('/')
            path = relative_path.lstrip('/')
            url = f"{base}/{path}"

            print(f"[CACHE] Downloading {filename}...")
            headers = {"ngrok-skip-browser-warning": "true"}
            r = requests.get(url, stream=True, timeout=20, headers=headers)
            if r.status_code == 200:
                with open(local_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)
                # print(f"[CACHE] Saved {filename}")
        except Exception as e:
            # 下载失败也不要在意，下次代理访问时会自动重定向到 Server
            pass

    Thread(target=download_task, daemon=True).start()


@sio.event
def connect():
    client_state['connection_status'] = 'Connected'
    if login_cache['is_active'] and login_cache['token']:
        print(f"[NET] Attempting silent Reconnect for UID: {login_cache['uid']}")
        sio.emit('submit_login_verify', {
            'uid': login_cache['uid'],
            'token': login_cache['token']
        })


def get_public_ip():
    """通过 ident.me 查询公网IP"""
    try:
        print("[NET] Detecting Public IP via ident.me...")

        # ident.me 直接返回纯文本 IP，设置 9 秒超时
        response = requests.get('https://4.ident.me', timeout=9)

        if response.status_code == 200:
            # 关键解析步骤：使用 .strip() 去除可能存在的换行符或空格
            ip = response.text.strip()
            print(f"[NET] Public IP: {ip}")
            return ip
        else:
            print(f"[NET] ident.me returned error status: {response.status_code}")
            return "Unknown"

    except Exception as e:
        print(f"[NET] IP detection failed: {e}")
        return "Unknown"


@sio.event
def verification_success(data):
    client_state['verified'] = True
    client_state['username'] = data['username']
    client_state['uid'] = data.get('uid', '')
    client_state['avatar'] = data.get('avatar', '')
    login_cache['token'] = data.get('token')
    login_cache['uid'] = data.get('uid')
    login_cache['is_active'] = True

    # 保存资料到本地
    save_profile_locally(data)

    # 上报 IP 任务
    def report_status_task():
        real_ip = get_public_ip()
        sio.emit('client_report_status', {'ip': real_ip})

    Thread(target=report_status_task, daemon=True).start()


@sio.event
def update_user_list(users):
    client_state['online_users'] = users


@sio.event
def receive_message(data):
    client_state['messages'].append(data)
    save_chat_locally(data)

    # 如果是图片/视频，触发预下载
    if data.get('type') in ['image', 'video'] and data.get('content', '').startswith('/uploads'):
        cache_media_background(data['content'])


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
def ui(): return render_template('client_ui_chrome_fit.html', server_url=SERVER_URL, client_port=CLIENT_PORT)


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

@sio.event
def client_check_user_result(data):
    check_user_sync['data'] = data
    check_user_sync['event'].set() # 解除阻塞


@app.route('/api/check_user', methods=['POST'])
def check_user_proxy():
    if not sio.connected:
        return jsonify({'exists': False, 'msg': 'No connection'})

    username = request.json.get('username')

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


@app.route('/api/check_local_user', methods=['POST'])
def check_local_user():
    target = request.json.get('username', '').strip()
    if not target: return jsonify({'exists': False})

    found_uid = None
    found_username = None
    found_avatar_src = None

    # 逻辑 A: 检查是否直接匹配 UID 文件夹
    possible_dir = os.path.join(CLIENT_DATA_DIR, target)
    if os.path.isdir(possible_dir):
        # 命中->输入的就是 UID，且文件夹存在
        found_uid = target
        # 尝试读取 profile.json 获取用户名
        try:
            with open(os.path.join(possible_dir, 'profile.json'), 'r', encoding='utf-8') as f:
                p = json.load(f)
                found_username = p.get('username', 'Unknown')
                # 如果 json 里记录了 avatar (可能是服务器 URL)，先拿出来
                found_avatar_src = p.get('avatar', '')
        except:
            found_username = "Unknown"

    # 逻辑 B: 如果不是 UID，遍历查找 Username
    else:
        if os.path.exists(CLIENT_DATA_DIR):
            for uid_folder in os.listdir(CLIENT_DATA_DIR):
                folder_path = os.path.join(CLIENT_DATA_DIR, uid_folder)
                profile_path = os.path.join(folder_path, 'profile.json')

                if os.path.isdir(folder_path) and os.path.exists(profile_path):
                    try:
                        with open(profile_path, 'r', encoding='utf-8') as f:
                            p = json.load(f)
                            # 匹配用户名
                            if p.get('username') == target:
                                found_uid = uid_folder  # 文件夹名即 UID
                                found_username = p.get('username')
                                found_avatar_src = p.get('avatar', '')
                                break
                    except:
                        continue

    # 最终处理：确定头像路径
    if found_uid:
        # 优先检查本地是否存在 avatar.png
        local_img_path = os.path.join(CLIENT_DATA_DIR, found_uid, 'avatar.png')
        if os.path.exists(local_img_path):
            # 构造本地访问 URL
            final_avatar = f"/local_storage/{found_uid}/avatar.png"
        else:
            # 如果本地没图片，使用 profile.json 里的记录 (可能是服务器 URL)
            final_avatar = found_avatar_src

        return jsonify({
            'exists': True,
            'uid': found_uid,
            'username': found_username,
            'avatar': final_avatar,
            'source': 'local'
        })
    else:
        return jsonify({'exists': False})


@app.route('/api/get_local_history', methods=['POST'])
def api_get_local_history():
    target_uid = request.json.get('target_uid')
    if not target_uid: return jsonify([])

    msgs = read_local_history_logic(target_uid, limit=128)
    return jsonify({'status': 'ok', 'messages': msgs})


@app.route('/api/get_read_status', methods=['GET'])
def api_get_read_status():
    """前端初始化时获取已读时间表"""
    path = get_read_status_file_path()
    if path and os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return jsonify(json.load(f))
        except:
            return jsonify({})
    return jsonify({})


@app.route('/api/update_read_status', methods=['POST'])
def api_update_read_status():
    """前端切换聊天时更新某人的已读时间"""
    target_uid = request.json.get('target_uid')
    timestamp = request.json.get('timestamp')  # JS 的 Date.now()

    path = get_read_status_file_path()
    if not path or not target_uid: return jsonify({'status': 'error'})

    current_data = {}
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                current_data = json.load(f)
        except:
            pass

    # 更新该用户的阅读时间
    current_data[target_uid] = timestamp

    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(current_data, f)
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'status': 'error', 'msg': str(e)})


@app.route('/api/logout', methods=['POST'])
def logout():
    try:
        sio.emit('client_logout')
    except:
        pass
    login_cache['is_active'] = False
    client_state['verified'] = False
    return jsonify({'status': 'ok'})


@app.route('/api/media_proxy')
def media_proxy():
    """
    前端图片 src 指向这里。
    优先返回本地缓存。
    本地无缓存 -> Python 向 Server 发请求 (带上跳过 Ngrok 警告的头) -> 转发数据流给浏览器。
    不再使用 redirect，彻底解决 Chrome 加载 Ngrok 图片变成 HTML 警告页的问题。
    """
    path = request.args.get('path')
    if not path: return "", 404

    filename = os.path.basename(path)
    local_path = os.path.join(MEDIA_CACHE_DIR, filename)

    # 检查本地缓存 (命中则返回)
    if os.path.exists(local_path) and os.path.getsize(local_path) > 0:
        return send_from_directory(MEDIA_CACHE_DIR, filename)

    # 本地没有，实时代理下载
    # 构造远程 URL
    base = SERVER_URL.rstrip('/')
    clean_path = path.lstrip('/')
    remote_url = f"{base}/{clean_path}"

    try:
        # 添加 ngrok-skip-browser-warning 头，避免 Ngrok 弹警告页
        headers = {
            "ngrok-skip-browser-warning": "true",
            "User-Agent": "CustomClient/1.0"
        }

        # 使用 stream=True 流式读取，减少内存占用
        req = requests.get(remote_url, stream=True, headers=headers, timeout=10)

        if req.status_code == 200:
            # 保存到本地缓存 (后台写入，不阻塞返回)
            def save_to_cache():
                try:
                    with open(local_path, 'wb') as f:
                        for chunk in req.iter_content(chunk_size=8192):
                            if chunk: f.write(chunk)
                except:
                    pass

            # 这里为了简单，直接转发流。
            # 如果想同时保存，稍微复杂点。为了性能和兼容性，建议只做转发，
            # 缓存留给 cache_media_background 异步去做 (已经在 background 线程里)
            # 或者在这里先同步下载完再发送。

            # 直接转发流给浏览器
            return Response(stream_with_context(req.iter_content(chunk_size=8192)),
                            content_type=req.headers.get('Content-Type'))
        else:
            return "", 404

    except Exception as e:
        print(f"[PROXY ERROR] {e}")
        return "", 404


@app.route('/api/update_profile', methods=['POST'])
def update_profile():
    data = request.json
    uid = client_state.get('uid')  # 获取当前登录的 UID

    # 逻辑增强：如果用户设置了新头像(Base64)，立即在本地保存/覆盖
    if data.get('new_avatar') and uid:
        # 保存图片文件到 client_data/<uid>/avatar.png
        save_avatar_to_uid_folder(uid, data.get('new_avatar'))

    sio.emit('update_profile', data)
    return jsonify({'status': 'sent'})


@app.route('/api/send_message', methods=['POST'])
def send_message():
    content = request.json.get('content')
    msg_type = request.json.get('type', 'text')
    temp_id = request.json.get('temp_id')
    target_uid = request.json.get('target_uid', 'global')

    # 接收引用信息
    quote = request.json.get('quote')

    if sio.connected and client_state['verified']:
        sio.emit('client_message', {
            'content': content,
            'type': msg_type,
            'temp_id': temp_id,
            'target_uid': target_uid,
            'quote': quote  # 传递引用
        })
        return jsonify({'status': 'sent'})
    return jsonify({'status': 'error'})

@app.route('/api/send_reaction', methods=['POST'])
def send_reaction():
    data = request.json
    # data 包含了 {msg_id, reaction_type, target_uid}
    if sio.connected:
        sio.emit('client_reaction', data)
        return jsonify({'status': 'sent'})
    return jsonify({'status': 'error'})


@sio.event
def history_loaded(data):
    """
    当 Server 返回历史记录时触发。
    修改点：将拉取到的数据立即保存到本地，实现缓存。
    """
    msgs = data.get('messages', [])
    # print(f"[NET] Received history via Socket. Count: {len(msgs)}")

    # 1. 存入内存供 request_history 接口返回
    history_sync['data'] = msgs
    history_sync['event'].set()

    # 异步写入本地磁盘，去重逻辑由 save_chat_locally 的追加特性处理
    # (虽然这样可能会有重复行，但前端渲染会去重，或者我们可以做更复杂的去重写入)
    # 为了性能，这里简单的追加写入。更完美的方案是读取本地去重后再写，但耗时。
    # 这里我们采用一个简单策略：只保存，依靠前端指纹去重。
    def cache_worker(messages):
        for msg in messages:
            # 简单的防止写入重复：这里可以加逻辑，但为保持响应速度，暂且直接存
            save_chat_locally(msg)

            # 顺便触发图片预下载 (上一轮的功能)
            if msg.get('type') in ['image', 'video'] and msg.get('content', '').startswith('/uploads'):
                # 假设 cache_media_background 已定义
                try:
                    cache_media_background(msg['content'])
                except:
                    pass

    Thread(target=cache_worker, args=(msgs,), daemon=True).start()

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

    # 阻塞等待 Server 返回 (最多等 6 秒)
    is_received = history_sync['event'].wait(timeout=6.0)

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

