import string
import random
import webbrowser
import csv
import os
import uuid
import time
import socket
import requests
import json
import base64
import uuid
import datetime
import mimetypes
from threading import Timer, Thread
from flask import Flask, render_template, request, redirect, send_from_directory, jsonify
from flask_socketio import SocketIO, emit, join_room, leave_room
from pyngrok import ngrok, conf

# 配置存储路径
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STORAGE_ROOT = os.path.join(BASE_DIR, 'server_storage')
MEDIA_DIR = os.path.join(STORAGE_ROOT, 'media')
AVATAR_DIR = os.path.join(STORAGE_ROOT, 'avatars')
LOGS_DIR = os.path.join(STORAGE_ROOT, 'chat_logs')
REACTIONS_FILE = os.path.join(LOGS_DIR, 'reactions.csv')

# 确保目录存在
for d in [STORAGE_ROOT, MEDIA_DIR, AVATAR_DIR, LOGS_DIR]:
    os.makedirs(d, exist_ok=True)


#   配置区域
NGROK_TOKEN = "39EjyqSfTr8pL1SSMvoc9qAOBuu_2eYPco4xpuGYFYiiHYXNW"
JSON_BIN_ID = "b45083904e075c083709"
JSON_BIN_URL = f"https://api.npoint.io/{JSON_BIN_ID}"

# 扩大 CSV 字段限制
csv.field_size_limit(100 * 1024 * 1024)

app = Flask(__name__)
app.config['SECRET_KEY'] = 'real_server_secret_key'
app.config['MAX_CONTENT_LENGTH'] = 130 * 1024 * 1024  # Flask上传限制

socketio = SocketIO(app,
                    cors_allowed_origins="*",
                    async_mode='threading',
                    max_http_buffer_size=209715200,
                    ping_timeout=60,
                    ping_interval=25
                    )

clients = {}  # sid -> client_info
uid_to_sid = {}  # uid -> sid
verification_store = {}
CSV_FILE = 'users.csv'
# uid -> {token: "xxx", expiry: timestamp}
user_tokens = {}
message_reactions = {}


#   辅助函数：文件与日志
def save_base64_file(base64_str, folder, prefix='file'):
    """将Base64字符串解码并保存为实体文件"""
    try:
        if ',' in base64_str:
            header, encoded = base64_str.split(',', 1)
        else:
            return None, "Invalid Base64"

        extension = '.bin'
        if 'image/' in header:
            extension = mimetypes.guess_extension(header.split(';')[0].split(':')[1]) or '.png'
        elif 'video/' in header:
            extension = mimetypes.guess_extension(header.split(';')[0].split(':')[1]) or '.mp4'

        if len(encoded) * 0.75 > 128 * 1024 * 1024:
            return None, "File too large (Max 128MB)"

        file_name = f"{prefix}_{uuid.uuid4().hex}{extension}"
        file_path = os.path.join(folder, file_name)

        with open(file_path, "wb") as f:
            f.write(base64.b64decode(encoded))

        if folder == AVATAR_DIR:
            return f"/uploads/avatars/{file_name}", None
        else:
            return f"/uploads/media/{file_name}", None
    except Exception as e:
        return None, str(e)


def get_log_file_path(target_uid=None, sender_uid=None):
    """
    获取日志文件路径。
    - target_uid 为 None -> 公共聊天室 (global_chat)
    - 否则 -> 私聊，文件夹名为 '较小UID_较大UID'，确保双方对话在同一个文件夹。
    """
    date_str = datetime.datetime.now().strftime("%Y-%m-%d")

    if target_uid is None:
        folder = os.path.join(LOGS_DIR, "global_chat")
    else:
        # 确保 A和B私聊 与 B和A私聊 指向同一个文件夹
        u1, u2 = sorted([str(sender_uid), str(target_uid)])
        folder_name = f"{u1}_{u2}"
        folder = os.path.join(LOGS_DIR, folder_name)

    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, f"{date_str}.log")


def append_to_chat_log(sender, sender_uid, target_uid, content, msg_type, timestamp_str):
    """写入日志，支持私聊和群聊"""
    log_file = get_log_file_path(target_uid, sender_uid)

    # 使用 JSON 格式存储，方便读取解析
    entry = {
        "sender": sender,
        "uid": sender_uid,
        "target_uid": target_uid,  # None 表示 global
        "content": content,
        "type": msg_type,
        "timestamp": timestamp_str
    }

    try:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        print(f"[LOG ERROR] {e}")


def read_recent_logs(folder, limit=128):
    if not os.path.exists(folder): return []
    files = [f for f in os.listdir(folder) if f.endswith('.log')]
    files.sort(reverse=True)
    messages = []

    for filename in files:
        file_path = os.path.join(folder, filename)
        day_msgs = []
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                for line in f:
                    try:

                        entry = json.loads(line)

                        real_msg = entry
                        try:
                            # 尝试解析 content 内部是否嵌套了完整 payload
                            inner = json.loads(entry['content'])
                            if isinstance(inner, dict) and 'id' in inner:
                                real_msg = inner  # 替换为新结构
                        except:
                            pass  # 是普通文本消息

                        # [关键] 合并内存中的互动数据
                        mid = real_msg.get('id')
                        if mid and mid in message_reactions:
                            real_msg['reactions'] = message_reactions[mid]

                        day_msgs.append(real_msg)
                    except:
                        pass
            messages = day_msgs + messages
            if len(messages) >= limit:
                messages = messages[-limit:]
                return messages
        except Exception as e:
            print(f"Log read error: {e}")
    return messages


#   数据库简易操作
def init_db():
    if not os.path.exists(CSV_FILE):
        with open(CSV_FILE, mode='w', newline='', encoding='utf-8') as f:
            csv.writer(f).writerow(['uid', 'username', 'password', 'avatar'])


def get_all_users():
    if not os.path.exists(CSV_FILE): return []
    with open(CSV_FILE, mode='r', encoding='utf-8') as f:
        return list(csv.DictReader(f))


def save_all_users(users):
    with open(CSV_FILE, mode='w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['uid', 'username', 'password', 'avatar'])
        writer.writeheader()
        writer.writerows(users)


def check_user_login(login_input, password):
    init_db()
    for row in get_all_users():
        if (row['username'] == login_input or row['uid'] == login_input) and row['password'] == password:
            return 2, row['username'], row['uid'], row.get('avatar', '')
    return 0, None, None, None


def add_user_to_csv(username, password):
    init_db()
    users = get_all_users()
    for row in users:
        if row['username'] == username: return False, None
    new_uid = ''.join(random.choices(string.digits, k=6))
    with open(CSV_FILE, mode='a', newline='', encoding='utf-8') as f:
        csv.writer(f).writerow([new_uid, username, password, ''])
    return True, new_uid


def init_reactions_db():
    if not os.path.exists(REACTIONS_FILE):
        with open(REACTIONS_FILE, mode='w', newline='', encoding='utf-8') as f:
            csv.writer(f).writerow(['msg_id', 'reaction_type', 'timestamp'])
    else:
        # 启动时加载现有互动
        try:
            with open(REACTIONS_FILE, mode='r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    mid = row['msg_id']
                    rtype = row['reaction_type']
                    if mid not in message_reactions: message_reactions[mid] = {}
                    message_reactions[mid][rtype] = message_reactions[mid].get(rtype, 0) + 1
        except Exception as e:
            print(f"[REACTION LOAD ERROR] {e}")

#初始化数据库
init_db()

init_reactions_db()

def save_reaction_to_csv(msg_id, reaction_type):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(REACTIONS_FILE, mode='a', newline='', encoding='utf-8') as f:
            csv.writer(f).writerow([msg_id, reaction_type, ts])
    except Exception as e:
        print(f"[REACTION SAVE ERROR] {e}")

#   Flask 路由
@app.route('/')
def index(): return "Server is running."


@app.route('/admin')
def admin_ui(): return render_template('server_ui.html')


@app.route('/uploads/media/<path:filename>')
def serve_media(filename):
    return send_from_directory(MEDIA_DIR, filename)


@app.route('/uploads/avatars/<path:filename>')
def serve_avatar(filename):
    return send_from_directory(AVATAR_DIR, filename)


@app.route('/api/avatar/<uid>')
def serve_avatar_by_uid(uid):
    """UID -> 头像文件重定向"""
    users = get_all_users()
    for row in users:
        if row['uid'] == uid:
            if row['avatar']:
                return redirect(row['avatar'])
            else:
                break
    return "No Avatar", 404


@app.route('/api/upload_media', methods=['POST', 'OPTIONS'])
def upload_media_http():
    """
    HTTP 文件上传接口，支持 CORS。
    """
    if request.method == 'OPTIONS':
        response = jsonify({'status': 'ok'})
        response.headers.add('Access-Control-Allow-Origin', '*')
        response.headers.add('Access-Control-Allow-Headers', '*')
        return response

    if 'file' not in request.files:
        return jsonify({'status': 'error', 'msg': 'No file part'}), 400


    file = request.files['file']
    if file.filename == '':
        return jsonify({'status': 'error', 'msg': 'No selected file'}), 400

    content_type = file.content_type
    extension = mimetypes.guess_extension(content_type.split(';')[0]) or '.bin'
    file_name = f"http_msg_{uuid.uuid4().hex}{extension}"
    file_path = os.path.join(MEDIA_DIR, file_name)

    file = request.files['file']

    # 服务端强校验：300KB = 300 * 1024 bytes
    file.seek(0, os.SEEK_END)
    size = file.tell()
    file.seek(0)  # 检查完大小把指针移回开头

    if size > 300 * 1024:
        return jsonify({'status': 'error', 'msg': 'Server Reject: File too large (>300KB)'}), 400

    try:
        file.save(file_path)
        file_url = f"/uploads/media/{file_name}"
        response = jsonify({'status': 'ok', 'url': file_url})
        response.headers.add('Access-Control-Allow-Origin', '*')
        return response

    except Exception as e:
        print(f"[UPLOAD ERROR] {e}")
        return jsonify({'status': 'error', 'msg': str(e)}), 500


def start_ngrok_and_upload():
    print("\n[BOOT] Starting Ngrok...")
    #local_ngrok = "./ngrok.exe"
    #if os.path.exists(local_ngrok): conf.get_default().ngrok_path = local_ngrok
    if NGROK_TOKEN: conf.get_default().auth_token = NGROK_TOKEN
    try:
        url = ngrok.connect(5005).public_url
        print(f"[NGROK] {url}")
        requests.post(JSON_BIN_URL, json={"url": url})
    except Exception as e:
        print(f"[NGROK ERROR] {e}")



#   SocketIO 事件
def broadcast_user_list():
    """向所有在线用户广播当前的在线列表 (侧边栏使用)"""
    safe_list = []
    for sid, info in clients.items():
        if info.get('verified'):
            safe_list.append({
                'username': info['username'],
                'uid': info['uid'],
                'avatar': info.get('avatar', '')
            })

    # 始终加入 Admin
    safe_list.append({'username': 'Admin', 'uid': 'ADMIN', 'avatar': ''})

    emit('update_user_list', safe_list, to='global_chat')
    emit('admin_update_client_list', clients, to='admin_room')


@socketio.on('connect')
def handle_connect():
    clients[request.sid] = {'ip': request.remote_addr, 'verified': False}
    emit('admin_update_client_list', clients, to='admin_room')


@socketio.on('disconnect')
def handle_disconnect():
    if request.sid in clients:
        uid = clients[request.sid].get('uid')
        if uid and uid in uid_to_sid: del uid_to_sid[uid]
        del clients[request.sid]
    broadcast_user_list()


@socketio.on('admin_join')
def handle_admin_join():
    join_room('admin_room')
    # 管理员连接时，读取 256 条全局历史
    global_folder = os.path.join(LOGS_DIR, "global_chat")
    history = read_recent_logs(global_folder, limit=256)
    emit('admin_history_load', history, to='admin_room')
    broadcast_user_list()



@socketio.on('request_verification_code')
def generate_code():
    sid = request.sid;
    ip = clients[sid]['ip']
    code = ''.join(random.choices(string.digits, k=6))
    verification_store[ip] = code
    print(f"\n[SEC] Code for {ip}: {code}\n")
    emit('system_send_code', {'code': code}, room=sid)


@socketio.on('submit_login_verify')
def handle_login_verify(data):
    sid = request.sid
    ip = clients[sid]['ip']

    # 逻辑 A：通过 Token 静默重连
    if data.get('token') and data.get('uid'):
        uid = data['uid']
        saved_info = user_tokens.get(uid)
        if saved_info and saved_info['token'] == data['token']:
            # Token 匹配，找回身份
            users = get_all_users()
            user_row = next((r for r in users if r['uid'] == uid), None)
            if user_row:
                clients[sid].update({
                    'verified': True,
                    'username': user_row['username'],
                    'uid': uid,
                    'avatar': user_row.get('avatar', '')
                })
                uid_to_sid[uid] = sid
                join_room('global_chat')
                emit('verification_success', {
                    'username': user_row['username'],
                    'uid': uid,
                    'avatar': user_row.get('avatar', ''),
                    'token': data['token']  # 确认 Token 依然有效
                })
                broadcast_user_list()
                return

    # 逻辑 B：原有的验证码登录逻辑
    real = verification_store.get(ip)
    if not real or data.get('code') != real:
        emit('verification_failed', {'msg': 'Invalid Code'})
        return

    st, user, uid, ava = check_user_login(data.get('username'), data.get('password'))
    if st == 2:
        # 生成新的 Token 并存入服务器内存
        new_token = uuid.uuid4().hex
        user_tokens[uid] = {'token': new_token}

        clients[sid].update({'verified': True, 'username': user, 'uid': uid, 'avatar': ava})
        uid_to_sid[uid] = sid
        verification_store.pop(ip, None)
        join_room('global_chat')
        # 将 Token 发回给客户端保存
        emit('verification_success', {'username': user, 'uid': uid, 'avatar': ava, 'token': new_token})
        broadcast_user_list()

    elif st == 0:
        suc, new_uid = add_user_to_csv(data.get('username'), data.get('password'))
        if suc:
            emit('show_notification', {'msg': f'Registered! UID: {new_uid}'})
        else:
            emit('verification_failed', {'msg': 'Username taken'})
    else:
        emit('verification_failed', {'msg': 'Wrong Password'})


@socketio.on('update_profile')
def handle_update_profile(data):
    sid = request.sid
    cur_user = clients[sid].get('username')
    if not cur_user: return
    users = get_all_users()
    updated = False
    new_avatar = None

    for row in users:
        if row['username'] == cur_user:
            if data.get('new_avatar'):
                url, _ = save_base64_file(data.get('new_avatar'), AVATAR_DIR, prefix=f"user_{row['uid']}")
                if url: row['avatar'] = url; new_avatar = url; updated = True
            if data.get('new_username'): row['username'] = data.get('new_username'); clients[sid]['username'] = row[
                'username']; updated = True
            if data.get('new_password'): row['password'] = data.get('new_password'); updated = True
            break

    if updated:
        save_all_users(users)
        emit('verification_success',
             {'username': clients[sid]['username'], 'uid': clients[sid]['uid'], 'avatar': new_avatar or row['avatar']})
        broadcast_user_list()


@socketio.on('client_message')
def handle_message(data):
    sid = request.sid
    if not clients.get(sid, {}).get('verified'): return

    sender = clients[sid]['username']
    sender_uid = clients[sid]['uid']

    target_uid = data.get('target_uid')
    if target_uid == 'global': target_uid = None

    content = data.get('content')
    msg_type = data.get('type', 'text')
    temp_id = data.get('temp_id')

    # 获取引用数据
    quote = data.get('quote')

    # 生成服务端唯一 Msg ID
    msg_id = uuid.uuid4().hex

    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 构造完整消息包，包含 id 和 quote
    payload = {
        'id': msg_id,
        'sender': sender,
        'uid': sender_uid,
        'content': content,
        'type': msg_type,
        'timestamp': ts,
        'temp_id': temp_id,
        'target_uid': target_uid,
        'quote': quote,  # 保存引用信息
        'reactions': {}  # 初始为空
    }

    # 写入日志 (JSONL 格式天然支持嵌套的 quote 对象，无需改 CSV)
    append_to_chat_log(sender, sender_uid, target_uid, json.dumps(payload), msg_type, ts)

    # 广播消息
    if target_uid:
        # 私聊逻辑
        emit('receive_message', payload, room=sid)
        if target_uid == 'ADMIN':
            emit('receive_message', payload, to='admin_room')
        elif target_uid in uid_to_sid:
            emit('receive_message', payload, room=uid_to_sid[target_uid])
        if target_uid != 'ADMIN': emit('receive_message', payload, to='admin_room')
    else:
        # 群聊逻辑
        emit('receive_message', payload, to='global_chat')
        emit('receive_message', payload, to='admin_room')


@socketio.on('client_reaction')
def handle_reaction(data):
    msg_id = data.get('msg_id')
    reaction_type = data.get('reaction_type')  # like, love, question, dislike

    if not msg_id or not reaction_type: return

    # 更新内存计数
    if msg_id not in message_reactions: message_reactions[msg_id] = {}
    message_reactions[msg_id][reaction_type] = message_reactions[msg_id].get(reaction_type, 0) + 1

    # 保存到 CSV 数据库
    save_reaction_to_csv(msg_id, reaction_type)

    # 广播更新 (只广播变化的这一条)
    emit('reaction_update', {
        'msg_id': msg_id,
        'reactions': message_reactions[msg_id]
    }, to='global_chat')

# 处理互动表情
@socketio.on('client_reaction')
def handle_reaction(data):
    msg_id = data.get('msg_id')
    reaction_type = data.get('reaction_type')  # like, love, question, dislike

    if not msg_id or not reaction_type: return

    # 更新内存计数
    if msg_id not in message_reactions: message_reactions[msg_id] = {}
    message_reactions[msg_id][reaction_type] = message_reactions[msg_id].get(reaction_type, 0) + 1

    # 保存到 CSV 数据库
    save_reaction_to_csv(msg_id, reaction_type)

    # 广播更新 (只广播变化的这一条)
    emit('reaction_update', {
        'msg_id': msg_id,
        'reactions': message_reactions[msg_id]
    }, to='global_chat')


@socketio.on('request_chat_history')
def handle_history_request(data):
    sid = request.sid

    # 增加身份验证检查
    client_info = clients.get(sid, {})
    if not client_info.get('verified'):
        # 如果未验证，通知前端需要重新登录或忽略
        emit('show_notification', {'msg': 'Please login to view history'}, room=sid)
        return

    requester_uid = client_info.get('uid')


    target_uid = data.get('target_uid')
    limit = data.get('limit', 128)

    if target_uid == 'global':
        target_uid = None

    if target_uid is None:
        folder = os.path.join(LOGS_DIR, "global_chat")
    else:
        # 确保 UID 是字符串，防止类型错误
        u1, u2 = sorted([str(requester_uid), str(target_uid)])
        folder = os.path.join(LOGS_DIR, f"{u1}_{u2}")

    # print(f"\n--- [DEBUG: HISTORY REQUEST] ---")
    # print(f"1. Request SID: {sid}")
    # print(f"2. Requester UID (Server view): {requester_uid}")
    # print(f"3. Target UID: {target_uid}")
    # print(f"4. Absolute Path: {folder}")
    # print(f"5. Path Exists?: {os.path.exists(folder)}")
    # print(f"--------------------------------\n")

    history = read_recent_logs(folder, limit=int(limit))

    emit('history_loaded', {'messages': history, 'target_uid': target_uid or 'global'}, room=sid)

@socketio.on('admin_request_history')
def handle_admin_request_history(data):
    """
    管理员请求特定房间的历史记录
    data: {'room_id': 'UID1 <-> UID2'} 或 {'room_id': 'Global Chat'}
    """
    room_id = data.get('room_id')
    limit = 256  # 管理员端默认读取 256 条

    if not room_id:
        return

    folder = None

    if room_id == 'Global Chat':
        folder = os.path.join(LOGS_DIR, "global_chat")
    elif '<->' in room_id:
        # 解析私聊房间名 "UID1 <-> UID2"
        try:
            parts = room_id.split(' <-> ')
            if len(parts) == 2:
                # 重新排序以匹配文件夹命名规则
                u1, u2 = sorted([parts[0], parts[1]])
                folder = os.path.join(LOGS_DIR, f"{u1}_{u2}")
        except:
            pass

    # 如果是 ADMIN 相关的特殊格式 (ADMIN <-> UID)，逻辑类似
    # 但由于 ADMIN 发消息也会存入对应用户的文件夹，通常遵循 UID 排序规则
    # 这里假设 ADMIN 的 UID 就是 "ADMIN"

    if folder:
        history = read_recent_logs(folder, limit)
        # 将历史记录发回给管理员
        emit('admin_history_loaded', {'room_id': room_id, 'messages': history}, room='admin_room')


@socketio.on('admin_send_message')
def handle_admin_message(data):
    target_uid = data.get('target_uid')
    content = data.get('content')
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if not target_uid or not content:
        return

    # 情况 1: Admin 发送 Global 消息
    if target_uid == 'global':
        # 写入 Global 日志 (target_uid=None 表示 global_chat 文件夹)
        append_to_chat_log('Admin', 'ADMIN', None, content, 'text', ts)

        payload = {
            'sender': 'Admin',
            'uid': 'ADMIN',
            'content': content,
            'type': 'text',
            'timestamp': ts,
            'target_uid': 'global'
        }
        # 发送给所有人 + Admin 房间
        emit('receive_message', payload, to='global_chat')
        emit('receive_message', payload, to='admin_room')

    # 情况 2: Admin 发送私聊消息 (或通过 @UID 插话)
    else:
        # 写入私聊日志
        append_to_chat_log('Admin', 'ADMIN', target_uid, content, 'text', ts)

        payload = {
            'sender': 'Admin',
            'uid': 'ADMIN',
            'content': content,
            'type': 'text',
            'timestamp': ts,
            'target_uid': target_uid
        }

        # 尝试发送给目标用户 (如果在线)
        if target_uid in uid_to_sid:
            emit('receive_message', payload, room=uid_to_sid[target_uid])

        # 必须回传给 Admin 房间，否则 Admin 界面上看不到自己刚发的消息
        emit('receive_message', payload, to='admin_room')


@socketio.on('client_check_user')
def handle_check_user(data):
    sid = request.sid
    username = data.get('username')
    users = get_all_users()

    # 遍历查找用户
    for u in users:
        if u['username'] == username:
            # 找到用户，返回存在状态、UID 和头像路径
            emit('client_check_user_result', {
                'exists': True,
                'uid': u['uid'],
                'avatar': u['avatar']
            }, room=sid)
            return

    # 未找到
    emit('client_check_user_result', {'exists': False}, room=sid)

if __name__ == '__main__':
    start_ngrok_and_upload()
    Timer(1.5, lambda: webbrowser.open('http://127.0.0.1:5005/admin')).start()
    print("SERVER STARTED ON 5005")
    socketio.run(app, host='0.0.0.0', port=5005, allow_unsafe_werkzeug=True)