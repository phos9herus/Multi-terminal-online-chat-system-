import os
import csv
import json
import base64
import uuid
import datetime
import mimetypes
import random
import string

# 配置存储路径
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STORAGE_ROOT = os.path.join(BASE_DIR, 'server_storage')
MEDIA_DIR = os.path.join(STORAGE_ROOT, 'media')
AVATAR_DIR = os.path.join(STORAGE_ROOT, 'avatars')
LOGS_DIR = os.path.join(STORAGE_ROOT, 'chat_logs')
REACTIONS_FILE = os.path.join(LOGS_DIR, 'reactions.csv')
CSV_FILE = os.path.join(STORAGE_ROOT, 'users.csv')

# 确保目录存在
for d in [STORAGE_ROOT, MEDIA_DIR, AVATAR_DIR, LOGS_DIR]:
    os.makedirs(d, exist_ok=True)

csv.field_size_limit(100 * 1024 * 1024)


def init_db():
    headers = ['uid', 'username', 'password', 'avatar', 'last_ip', 'last_seen']
    if not os.path.exists(CSV_FILE):
        with open(CSV_FILE, mode='w', newline='', encoding='utf-8') as f:
            csv.writer(f).writerow(headers)
    else:
        # 简单迁移检查 (可选)
        pass


def get_all_users():
    if not os.path.exists(CSV_FILE): return []
    try:
        with open(CSV_FILE, mode='r', encoding='utf-8') as f:
            return list(csv.DictReader(f))
    except:
        return []


def save_all_users(users):
    fieldnames = ['uid', 'username', 'password', 'avatar', 'last_ip', 'last_seen']
    with open(CSV_FILE, mode='w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for user in users:
            if 'last_ip' not in user: user['last_ip'] = ''
            if 'last_seen' not in user: user['last_seen'] = ''
        writer.writerows(users)


def update_user_status_in_csv(uid, ip=None, last_seen=None):
    users = get_all_users()
    updated = False
    for row in users:
        if row['uid'] == uid:
            if ip: row['last_ip'] = ip
            if last_seen: row['last_seen'] = last_seen
            updated = True
            break
    if updated: save_all_users(users)
    return users


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
        csv.writer(f).writerow([new_uid, username, password, '', '', ''])
    return True, new_uid


def save_base64_file(base64_str, folder, prefix='file'):
    try:
        header, encoded = base64_str.split(',', 1) if ',' in base64_str else (None, None)
        if not encoded: return None, "Invalid Base64"

        extension = '.bin'
        if 'image/' in header:
            extension = mimetypes.guess_extension(header.split(';')[0].split(':')[1]) or '.png'
        elif 'video/' in header:
            extension = mimetypes.guess_extension(header.split(';')[0].split(':')[1]) or '.mp4'

        file_name = f"{prefix}_{uuid.uuid4().hex}{extension}"
        file_path = os.path.join(folder, file_name)
        with open(file_path, "wb") as f:
            f.write(base64.b64decode(encoded))

        return (f"/uploads/avatars/{file_name}" if folder == AVATAR_DIR else f"/uploads/media/{file_name}"), None
    except Exception as e:
        return None, str(e)


def get_log_file_path(target_uid=None, sender_uid=None):
    date_str = datetime.datetime.now().strftime("%Y-%m-%d")
    if target_uid is None:
        folder = os.path.join(LOGS_DIR, "global_chat")
    else:
        u1, u2 = sorted([str(sender_uid), str(target_uid)])
        folder = os.path.join(LOGS_DIR, f"{u1}_{u2}")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, f"{date_str}.log")


def append_to_chat_log(sender, sender_uid, target_uid, content, msg_type, timestamp_str):
    log_file = get_log_file_path(target_uid, sender_uid)
    entry = {"sender": sender, "uid": sender_uid, "target_uid": target_uid, "content": content, "type": msg_type,
             "timestamp": timestamp_str}
    try:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        print(f"[LOG ERROR] {e}")


def load_all_reactions():
    reactions = {}
    if os.path.exists(LOGS_DIR):
        for root, dirs, files in os.walk(LOGS_DIR):
            if 'reactions.csv' in files:
                path = os.path.join(root, 'reactions.csv')
                try:
                    with open(path, 'r', encoding='utf-8') as f:
                        for row in csv.DictReader(f):
                            mid, rtype = row['msg_id'], row['reaction_type']
                            if mid not in reactions: reactions[mid] = {}
                            reactions[mid][rtype] = reactions[mid].get(rtype, 0) + 1
                except:
                    pass
    return reactions


def append_reaction_to_file(sender_uid, target_uid, msg_id, reaction_type):
    folder = None
    if not target_uid or target_uid == 'global':
        folder = os.path.join(LOGS_DIR, "global_chat")
    else:
        u1, u2 = sorted([str(sender_uid), str(target_uid)])
        folder = os.path.join(LOGS_DIR, f"{u1}_{u2}")

    os.makedirs(folder, exist_ok=True)
    file_path = os.path.join(folder, 'reactions.csv')
    if not os.path.exists(file_path):
        with open(file_path, 'w', newline='', encoding='utf-8') as f: csv.writer(f).writerow(
            ['msg_id', 'reaction_type', 'timestamp'])

    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(file_path, 'a', newline='', encoding='utf-8') as f:
        csv.writer(f).writerow([msg_id, reaction_type, ts])


def read_recent_logs(folder, limit=128, reactions_cache=None):
    if not os.path.exists(folder): return []
    files = sorted([f for f in os.listdir(folder) if f.endswith('.log')], reverse=True)
    messages = []
    for filename in files:
        try:
            day_msgs = []
            with open(os.path.join(folder, filename), 'r', encoding='utf-8') as f:
                for line in f:
                    try:
                        entry = json.loads(line)
                        try:
                            inner = json.loads(entry['content'])
                            if isinstance(inner, dict) and 'id' in inner: entry = inner
                        except:
                            pass
                        if reactions_cache and entry.get('id') in reactions_cache:
                            entry['reactions'] = reactions_cache[entry.get('id')]
                        day_msgs.append(entry)
                    except:
                        pass
            messages = day_msgs + messages
            if len(messages) >= limit: return messages[-limit:]
        except:
            pass
    return messages