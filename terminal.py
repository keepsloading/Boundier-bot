import os
import sys
import asyncio
import logging
import time

# Ensure package directory is in sys.path
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

# Configure minimal/clean logging for terminal interaction
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("terminal.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
# Disable console logging for logger objects to keep terminal output clean, except custom prints
logging.getLogger().handlers[1].setLevel(logging.WARNING)

BANNER = r"""
============================================================
    __                          ___          
   / /_  ____  __  ______  ____/ (_)__  _____
  / __ \/ __ \/ / / / __ \/ __  / / _ \/ ___/
 / /_/ / /_/ / /_/ / / / / /_/ / /  __/ /    
/_.___/\____/\__,_/_/ /_/\__,_/_/\___/_/     
                                            
                  SETUP TERMINAL                 
============================================================
"""

def print_banner():
    print(BANNER)

def load_env_defaults():
    defaults = {"DISCORD_TOKEN": "", "GITHUB_PAT": "", "ENCRYPTION_KEY": ""}
    if os.path.exists(".env"):
        with open(".env", "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    defaults[k.strip()] = v.strip().strip('"').strip("'")
    return defaults

def load_config_defaults():
    defaults = {"admin_channel_id": ""}
    if os.path.exists("config.yaml"):
        try:
            import yaml
            with open("config.yaml", "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
                defaults["admin_channel_id"] = str(cfg.get("discord", {}).get("admin_channel_id", ""))
        except Exception:
            pass
    return defaults

def configure_interactive():
    print("\n--- [1] INTERACTIVE CONFIGURATION ---")
    env_defs = load_env_defaults()
    cfg_defs = load_config_defaults()
    
    print("Press Enter to keep existing values in brackets.")
    
    # 1. Discord Bot Token
    token = input(f"Discord Bot Token [{env_defs['DISCORD_TOKEN'][:15]}...]: ").strip()
    if not token:
        token = env_defs["DISCORD_TOKEN"]
        
    # 2. Admin Channel ID
    channel_str = input(f"Discord Admin Channel ID [{cfg_defs['admin_channel_id']}]: ").strip()
    if not channel_str:
        channel_str = cfg_defs["admin_channel_id"]
    try:
        admin_channel_id = int(channel_str) if channel_str else 0
    except ValueError:
        print("Error: Admin Channel ID must be an integer.")
        return
        
    # 3. GitHub PAT
    pat = input(f"GitHub Personal Access Token (PAT) (Optional for Gist Backup) [{env_defs['GITHUB_PAT'][:15]}...]: ").strip()
    if not pat:
        pat = env_defs["GITHUB_PAT"]
        
    # 4. Encryption Key
    enc_key = input(f"Session Encryption Key (Optional for Gist Backup) [{env_defs['ENCRYPTION_KEY'][:15]}...]: ").strip()
    if not enc_key:
        enc_key = env_defs["ENCRYPTION_KEY"]
        
    # Write to .env
    env_content = {}
    if os.path.exists(".env"):
        with open(".env", "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env_content[k.strip()] = v.strip().strip('"').strip("'")
    
    env_content["DISCORD_TOKEN"] = token
    env_content["GITHUB_PAT"] = pat
    env_content["ENCRYPTION_KEY"] = enc_key
    
    with open(".env", "w", encoding="utf-8") as f:
        for k, v in env_content.items():
            f.write(f'{k}="{v}"\n')
            
    # Write/update config.yaml
    config_content = {}
    if os.path.exists("config.yaml"):
        try:
            import yaml
            with open("config.yaml", "r", encoding="utf-8") as f:
                config_content = yaml.safe_load(f) or {}
        except Exception:
            pass
            
    if "discord" not in config_content:
        config_content["discord"] = {}
    config_content["discord"]["admin_channel_id"] = admin_channel_id
    
    # Save token for compatibility if present
    config_content["discord"]["token"] = token
    
    # Make sure other keys exist
    if "playwright" not in config_content:
        config_content["playwright"] = {
            "headless": True,
            "user_data_dir": "browser_profile/",
            "timeout_ms": 90000,
            "max_pages": 3,
            "viewport": {"width": 1280, "height": 720}
        }
    if "memory" not in config_content:
        config_content["memory"] = {
            "max_thread_messages": 15,
            "channel_summary_limit": 500,
            "system_instructions": "You are Boundier, a helpful coding assistant."
        }
        
    try:
        import yaml
        with open("config.yaml", "w", encoding="utf-8") as f:
            yaml.safe_dump(config_content, f, default_flow_style=False)
        print("\n[SUCCESS] Configuration saved to .env and config.yaml!")
    except Exception as err:
        print(f"Error saving config.yaml: {err}")

async def run_headed_login():
    print("\n--- [2] AUTHORIZE CHATGPT (HEADED LOGIN) ---")
    if not os.path.exists("config.yaml"):
        print("Error: Configuration files not found. Please run Option [1] first.")
        return
        
    from boundier.config import load_config
    from boundier.chatgpt.driver import PlaywrightDriver
    
    config = load_config("config.yaml")
    # Force headed mode and browser context parameters
    config.playwright.headless = False
    config.playwright.user_data_dir = "./browser_profile"
    
    driver = PlaywrightDriver(config)
    await driver.start()
    
    try:
        print("\n" + "="*80)
        print("Launching headed Chromium browser tab...")
        print("Please log in manually on the browser page.")
        print("We will automatically detect when the session is authenticated.")
        print("="*80 + "\n")
        
        # Navigate to ChatGPT home page
        await driver.page.goto("https://chatgpt.com", wait_until="domcontentloaded")
        
        # Wait for authentication up to 5 minutes
        authenticated = await driver.wait_for_manual_login(timeout_seconds=300)
        
        if authenticated:
            print("\n[SUCCESS] Login verified successfully!")
            # Trigger immediate manual sync to Gist
            await driver.save_gist_session_state()
            print("[SUCCESS] Session cookies synced to Gist and exported locally.")
        else:
            print("\n[ERROR] Login wait period timed out or failed.")
            
    except Exception as e:
        print(f"\n[ERROR] An error occurred during browser login: {e}")
    finally:
        await driver.stop()

def bootstrap_database():
    print("\n--- [3] BOOTSTRAP SQLITE DATABASE & MEMORIES ---")
    try:
        from boundier.storage.sqlite_store import SQLiteStore
        print("Initializing SQLite database ('boundier.db')...")
        store = SQLiteStore(db_path="boundier.db", schema_path="schema.sql", memory_dir="memory")
        print("Syncing markdown memory files to database...")
        store.sync_markdown_files()
        print("\n[SUCCESS] SQLite database bootstrapped and synced successfully!")
    except Exception as e:
        print(f"\n[ERROR] Failed to bootstrap database: {e}")

async def run_diagnostics():
    print("\n--- [4] RUN SELF-DIAGNOSTICS ---")
    if not os.path.exists("config.yaml"):
        print("[FAIL] config.yaml not found. Please run Option [1] first.")
        return
        
    from boundier.config import load_config
    from boundier.chatgpt.driver import PlaywrightDriver
    
    config = load_config("config.yaml")
    
    # 1. Test Gist Sync & PAT
    print("\n1. Testing GitHub PAT and Gist connection...")
    github_pat = os.environ.get("GITHUB_PAT") or load_env_defaults().get("GITHUB_PAT")
    if github_pat:
        # Check connection
        import urllib.request
        import json
        url_list = "https://api.github.com/gists"
        headers = {
            "Authorization": f"token {github_pat}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "Boundier-Bot"
        }
        req = urllib.request.Request(url_list, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=8) as res:
                res.read()
            print("   [PASS] GitHub PAT validated. Successfully connected to GitHub Gist API.")
        except Exception as e:
            print(f"   [FAIL] GitHub API connection failed: {e}")
    else:
        print("   [SKIP] GITHUB_PAT not set in environment or .env. Session syncing disabled.")
        
    # 2. Test Discord Token
    print("\n2. Testing Discord Bot Token connection...")
    discord_token = os.environ.get("DISCORD_TOKEN") or load_env_defaults().get("DISCORD_TOKEN")
    if discord_token:
        import urllib.request
        import json
        url = "https://discord.com/api/v10/users/@me"
        headers = {
            "Authorization": f"Bot {discord_token}",
            "User-Agent": "DiscordBot (https://github.com/keepsloading/Boundier, 1.0.0)"
        }
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=8) as res:
                user_info = json.loads(res.read().decode("utf-8"))
            print(f"   [PASS] Discord Token validated. Logged in as: {user_info['username']}")
        except Exception as e:
            print(f"   [FAIL] Discord API validation failed: {e}")
    else:
        print("   [FAIL] DISCORD_TOKEN is not set. Discord Bot will not launch.")
        
    # 3. Test ChatGPT Session Cookie status
    print("\n3. Testing ChatGPT session status (Headless browser check)...")
    driver = PlaywrightDriver(config)
    await driver.start()
    try:
        is_active = await driver.check_session_active(navigate=True)
        if is_active:
            print("   [PASS] ChatGPT browser session is authenticated and active!")
        else:
            print("   [FAIL] ChatGPT browser session is unauthenticated. Please run Option [2].")
    except Exception as e:
        print(f"   [FAIL] Error validating ChatGPT session: {e}")
    finally:
        await driver.stop()

def run_discord_bot():
    print("\n--- [5] LAUNCH BOUNDIER DISCORD BOT ---")
    try:
        print("Initializing bootstrap pipeline...")
        from boundier.main import async_main
        # We run async_main using asyncio.run
        asyncio.run(async_main())
    except (KeyboardInterrupt, SystemExit):
        print("\n[INFO] Bot terminated.")
    except Exception as e:
        print(f"\n[ERROR] Failed to run bot: {e}")

def main():
    while True:
        print_banner()
        print("[1] Interactive Configuration (Set Token, PAT, Keys)")
        print("[2] Authorize ChatGPT (Headed Browser Login)")
        print("[3] Bootstrap SQLite Database & Memories")
        print("[4] Run Self-Diagnostics (Session / Discord Checks)")
        print("[5] Launch Boundier Discord Bot")
        print("[6] Exit")
        print("="*60)
        
        choice = input("Choose an option [1-6]: ").strip()
        if choice == "1":
            configure_interactive()
        elif choice == "2":
            asyncio.run(run_headed_login())
        elif choice == "3":
            bootstrap_database()
        elif choice == "4":
            asyncio.run(run_diagnostics())
        elif choice == "5":
            run_discord_bot()
        elif choice == "6":
            print("\nGoodbye!")
            break
        else:
            print("\nInvalid choice or not implemented yet.")
        
        input("\nPress Enter to return to menu...")
        # Clear screen for next iteration
        if sys.platform == "win32":
            os.system("cls")
        else:
            os.system("clear")

if __name__ == "__main__":
    main()
