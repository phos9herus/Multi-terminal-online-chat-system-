import os
import json
import base64
from datetime import datetime
from threading import Thread
import requests

# 常量与路径配置
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CLIENT_DATA_DIR = os.path.join(BASE_DIR, 'client_data')
MEDIA_CACHE_DIR = os.path.join(CLIENT_DATA_DIR, 'media_cache')

if not os.path.exists(CLIENT_DATA_DIR): os.makedirs(CLIENT_DATA_DIR)
if not os.path.exists(MEDIA_CACHE_DIR): os.makedirs(MEDIA_CACHE_DIR)


def save_chat_locally(client_state, data):
    """
    保存聊天记录到本地 JSONL 文件
    修复：严格区分 Global 和 私聊，防止 Global 消息混入私聊文件夹
    """
    my_uid = str(client_state.get('uid', ''))
    if not my_uid: return

    msg_uid = str(data.get('uid', ''))
    raw_target = data.get('target_uid')  # 获取原始数据，不急着转 string
    msg_id = data.get('id')

    if not msg_id: return

    # --- 核心修复逻辑开始 ---
    # 判定归档文件夹名称 (partner_uid)

    # 1. 判定是否为群聊 (Global)
    # 服务端可能传回 'global'，也可能传回 None (表示广播)，或者空字符串
    if raw_target == 'global' or raw_target is None or str(raw_target).strip() == '':
        partner_uid = 'global'

    # 2. 判定私聊 (Private)
    else:
        # 如果消息是我发的 -> 存入对方 ID (raw_target) 的文件夹
        if msg_uid == my_uid:
            partner_uid = str(raw_target)
        # 如果消息是别人发给我的 -> 存入发送方 ID (msg_uid) 的文件夹
        else:
            partner_uid = str(msg_uid)
    # --- 核心修复逻辑结束 ---

    # 确保路径存在
    log_dir = os.path.join(CLIENT_DATA_DIR, my_uid, 'chat_logs', partner_uid)
    if not os.path.exists(log_dir): os.makedirs(log_dir)

    # 按日期分割日志文件
    date_str = datetime.now().strftime("%Y-%m-%d")
    log_file = os.path.join(log_dir, f"{date_str}.json")

    # 简单去重 (防止同一条消息因网络波动重复写入)
    existing_ids = set()
    if os.path.exists(log_file):
        try:
            with open(log_file, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.strip():
                        try:
                            record = json.loads(line)
                            if 'id' in record: existing_ids.add(record['id'])
                        except:
                            pass
        except:
            pass

    if msg_id not in existing_ids:
        try:
            with open(log_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(data, ensure_ascii=False) + "\n")
        except Exception as e:
            print(f"[LOCAL SAVE ERROR] {e}")


def read_local_history_logic(client_state, target_uid, limit=200):
    """读取本地历史消息"""
    my_uid = str(client_state.get('uid', ''))
    if not my_uid: return []
    log_dir = os.path.join(CLIENT_DATA_DIR, my_uid, 'chat_logs', str(target_uid))
    if not os.path.exists(log_dir): return []

    messages = []
    files = sorted([f for f in os.listdir(log_dir) if f.endswith('.json')])
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
            messages = day_msgs + messages
            if len(messages) >= limit: break
        except:
            pass
    return messages[-limit:]


def cache_media_background(server_url, relative_path):
    """后台下载并缓存媒体文件"""
    if not relative_path or not relative_path.startswith('/uploads/'): return
    filename = os.path.basename(relative_path)
    local_path = os.path.join(MEDIA_CACHE_DIR, filename)
    if os.path.exists(local_path) and os.path.getsize(local_path) > 0: return

    def download_task():
        try:
            base = server_url.rstrip('/')
            path = relative_path.lstrip('/')
            url = f"{base}/{path}"
            r = requests.get(url, stream=True, timeout=20)
            if r.status_code == 200:
                with open(local_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192): f.write(chunk)
        except:
            pass

    Thread(target=download_task, daemon=True).start()


def save_avatar_to_uid_folder(uid, b64_str):
    """保存头像到本地"""
    if not uid: return
    user_dir = os.path.join(CLIENT_DATA_DIR, str(uid))
    if not os.path.exists(user_dir): os.makedirs(user_dir)
    try:
        encoded = b64_str.split(',', 1)[1] if ',' in b64_str else b64_str
        file_path = os.path.join(user_dir, 'avatar.png')
        with open(file_path, "wb") as f:
            f.write(base64.b64decode(encoded))
    except Exception as e:
        print(f"[LOCAL SAVE] Avatar save failed: {e}")


def save_profile_locally(data, server_url):
    """保存用户资料到本地"""
    uid = str(data.get('uid', ''))
    if not uid: return
    user_dir = os.path.join(CLIENT_DATA_DIR, uid)
    if not os.path.exists(user_dir): os.makedirs(user_dir)

    avatar_val = data.get('avatar', '')
    profile_data = {
        'uid': uid, 'username': data.get('username', ''),
        'avatar': avatar_val, 'last_login': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

    try:
        with open(os.path.join(user_dir, 'profile.json'), 'w', encoding='utf-8') as f:
            json.dump(profile_data, f, ensure_ascii=False)
        if avatar_val:
            if avatar_val.startswith('data:image'):
                save_avatar_to_uid_folder(uid, avatar_val)
            else:
                def download_avatar(u, p):
                    try:
                        url = u if u.startswith('http') else f"{server_url.rstrip('/')}/{u.lstrip('/')}"
                        r = requests.get(url, timeout=10)
                        if r.status_code == 200:
                            with open(os.path.join(user_dir, 'avatar.png'), 'wb') as f: f.write(r.content)
                    except:
                        pass

                Thread(target=download_avatar, args=(avatar_val, os.path.join(user_dir, 'avatar.png'))).start()
    except Exception as e:
        print(f"[LOCAL SAVE] Profile failed: {e}")


def check_local_user_logic(target):
    """在本地检查用户是否存在 (UID 或 用户名)"""
    found_uid, found_username, found_avatar_src = None, None, None
    possible_dir = os.path.join(CLIENT_DATA_DIR, target)

    # 检查是否为 UID
    if os.path.isdir(possible_dir):
        found_uid = target
        try:
            with open(os.path.join(possible_dir, 'profile.json'), 'r') as f:
                p = json.load(f)
                found_username = p.get('username', 'Unknown')
                found_avatar_src = p.get('avatar', '')
        except:
            found_username = "Unknown"
    else:
        # 检查是否为用户名 (遍历查找)
        if os.path.exists(CLIENT_DATA_DIR):
            for uid_folder in os.listdir(CLIENT_DATA_DIR):
                folder_path = os.path.join(CLIENT_DATA_DIR, uid_folder)
                profile_path = os.path.join(folder_path, 'profile.json')
                if os.path.isdir(folder_path) and os.path.exists(profile_path):
                    try:
                        with open(profile_path, 'r') as f:
                            p = json.load(f)
                            if p.get('username') == target:
                                found_uid = uid_folder
                                found_username = p.get('username')
                                found_avatar_src = p.get('avatar', '')
                                break
                    except:
                        continue

    if found_uid:
        local_img = os.path.join(CLIENT_DATA_DIR, found_uid, 'avatar.png')
        final_avatar = f"/local_storage/{found_uid}/avatar.png" if os.path.exists(local_img) else found_avatar_src
        return {'exists': True, 'uid': found_uid, 'username': found_username, 'avatar': final_avatar}
    return {'exists': False}