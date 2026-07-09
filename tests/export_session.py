import os
import sys
import glob
import json
import sqlite3
import shutil
import base64
import ctypes
from ctypes import wintypes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

class DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ('cbData', wintypes.DWORD),
        ('pbData', ctypes.POINTER(ctypes.c_ubyte))
    ]

def decrypt_dpapi(data: bytes) -> bytes:
    in_blob = DATA_BLOB(len(data), (ctypes.c_ubyte * len(data)).from_buffer_copy(data))
    out_blob = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)):
        return None
    res = bytes(out_blob.pbData[:out_blob.cbData])
    ctypes.windll.kernel32.LocalFree(out_blob.pbData)
    return res

def get_master_key(user_data_path: str) -> bytes:
    local_state_path = os.path.join(user_data_path, 'Local State')
    if not os.path.exists(local_state_path):
        return None
    try:
        with open(local_state_path, 'r', encoding='utf-8') as f:
            local_state = json.load(f)
        enc_key = base64.b64decode(local_state['os_crypt']['encrypted_key'])
        return decrypt_dpapi(enc_key[5:])
    except Exception:
        return None

def decrypt_cookie(val: bytes, key: bytes) -> str:
    if val.startswith(b'v10') or val.startswith(b'v11'):
        try:
            return AESGCM(key).decrypt(val[3:15], val[15:], None).decode('utf-8')
        except Exception:
            return None
    if not val.startswith(b'v10') and not val.startswith(b'v11') and not val.startswith(b'v20'):
        try:
            return val.decode('utf-8')
        except Exception:
            return None
    return None

def extract_cookies_from_db(db_path: str, key: bytes):
    temp_db = "temp_export_cookies.db"
    shutil.copyfile(db_path, temp_db)
    
    conn = sqlite3.connect(temp_db)
    cursor = conn.cursor()
    
    cursor.execute("PRAGMA table_info(cookies)")
    columns = [col[1] for col in cursor.fetchall()]
    
    query = "SELECT host_key, name, path, is_secure, is_httponly, expires_utc, encrypted_value"
    if "samesite" in columns:
        query += ", samesite"
    else:
        query += ", -1"
    query += " FROM cookies"
    
    cursor.execute(query)
    cookies_list = []
    
    same_site_map = {-1: "None", 0: "None", 1: "Lax", 2: "Strict"}
    
    for row in cursor.fetchall():
        host_key, name, path, is_secure, is_httponly, expires_utc, encrypted_value = row[:7]
        samesite = row[7]
        try:
            val = decrypt_cookie(encrypted_value, key)
            if not val:
                continue
                
            cookie_dict = {
                "name": name,
                "value": val,
                "domain": host_key,
                "path": path,
                "secure": bool(is_secure),
                "httpOnly": bool(is_httponly),
                "sameSite": same_site_map.get(samesite, "None")
            }
            
            if expires_utc > 0:
                unix_expires = (expires_utc - 11644473600000000) / 1000000.0
                cookie_dict["expires"] = unix_expires
                
            cookies_list.append(cookie_dict)
        except Exception:
            continue
            
    conn.close()
    try:
        os.remove(temp_db)
    except:
        pass
    return cookies_list

def check_for_v20_cookies(db_path: str) -> bool:
    try:
        temp_db = "temp_check_v20.db"
        shutil.copyfile(db_path, temp_db)
        conn = sqlite3.connect(temp_db)
        cursor = conn.cursor()
        cursor.execute("SELECT encrypted_value FROM cookies WHERE host_key LIKE '%chatgpt.com%'")
        rows = cursor.fetchall()
        conn.close()
        os.remove(temp_db)
        return any(row[0].startswith(b'v20') for row in rows)
    except Exception:
        return False

def main():
    if sys.platform != "win32":
        print("This exporter is designed to run on Windows where your local browser session is stored.")
        return

    print("Scanning browser profiles for ChatGPT login session cookies...")
    
    appdata = os.environ.get('LOCALAPPDATA', '')
    paths_to_check = {
        'Bot browser_profile': os.path.abspath('browser_profile'),
        'Bot .chrome_profile': os.path.abspath('.chrome_profile'),
        'Google Chrome': os.path.join(appdata, 'Google', 'Chrome', 'User Data'),
        'Microsoft Edge': os.path.join(appdata, 'Microsoft', 'Edge', 'User Data'),
        'Brave Browser': os.path.join(appdata, 'BraveSoftware', 'Brave-Browser', 'User Data')
    }
    
    found_any = False
    v20_detected = False
    
    for name, user_data in paths_to_check.items():
        if not os.path.exists(user_data):
            continue
            
        if name.startswith('Bot'):
            key = get_master_key(user_data)
            profile_dirs = [user_data]
        else:
            key = get_master_key(user_data)
            profile_dirs = glob.glob(os.path.join(user_data, 'Default')) + glob.glob(os.path.join(user_data, 'Profile *'))
            
        if not key:
            continue
            
        for prof in profile_dirs:
            if name.startswith('Bot'):
                db_path = os.path.join(prof, 'Default', 'Network', 'Cookies')
            else:
                db_path = os.path.join(prof, 'Network', 'Cookies')
                
            if not os.path.exists(db_path):
                continue
                
            if check_for_v20_cookies(db_path):
                v20_detected = True
                
            try:
                cookies = extract_cookies_from_db(db_path, key)
                chatgpt_cookies = [c for c in cookies if 'chatgpt' in c.get('domain', '')]
                
                if chatgpt_cookies:
                    has_session = any("session-token" in c["name"] for c in chatgpt_cookies)
                    found_any = True
                    
                    print("\n" + "="*80)
                    print(f"PROFILE SOURCE: {name} ({os.path.basename(prof)})")
                    print(f"Total ChatGPT Cookies: {len(chatgpt_cookies)} | Has Session Token: {has_session}")
                    if has_session:
                        print("⭐ (RECOMMENDED: This profile contains active login session tokens!)")
                    print("Copy the entire line below and set it as the CHATGPT_STORAGE_STATE environment variable in Render:")
                    print("="*80)
                    print(json.dumps(chatgpt_cookies))
                    print("="*80 + "\n")
            except PermissionError:
                print(f"\n⚠️  [Locked] {name} ({os.path.basename(prof)}) is currently open. Please close the browser to extract its cookies.")
            except Exception as e:
                pass
                
    if not found_any:
        print("\nNo active login cookies found.")
        if v20_detected:
            print("\n" + "!"*80)
            print("ATTENTION: APP-BOUND ENCRYPTION (v20) DETECTED")
            print("Your personal browsers (Chrome/Edge/Brave) protect cookies using Windows App-Bound Encryption.")
            print("External scripts cannot decrypt these cookies. You must extract them from the bot's own browser.")
            print("Please follow these steps to log in directly inside the bot:")
            print("1. Start the bot locally in headed mode:")
            print("   python -m boundier.main")
            print("2. Log in manually inside the browser window that opens.")
            print("3. Once logged in, close the bot and run this exporter again:")
            print("   python -m tests.export_session")
            print("This will successfully export decryptable (v10) cookies from the bot's own browser profile!")
            print("!"*80 + "\n")
        else:
            print("Please make sure you are logged into ChatGPT in one of your browsers (Chrome, Edge, Brave, or the bot browser) first!")

if __name__ == "__main__":
    main()
