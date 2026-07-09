import os
import json
import asyncio
from playwright.async_api import async_playwright
from boundier.config import load_config

async def export():
    config = load_config("config.yaml")
    paths_to_try = [
        os.path.abspath(config.playwright.user_data_dir),
        os.path.abspath("browser_profile"),
        os.path.abspath(".chrome_profile")
    ]
    
    # Remove duplicates while preserving order
    seen = set()
    paths_to_try = [p for p in paths_to_try if not (p in seen or seen.add(p))]
    
    cookies = []
    active_path = ""
    
    async with async_playwright() as p:
        for path in paths_to_try:
            if not os.path.exists(path):
                continue
            print(f"Checking browser profile: {path}")
            try:
                context = await p.chromium.launch_persistent_context(
                    user_data_dir=path,
                    headless=True
                )
                found_cookies = await context.cookies()
                await context.close()
                # Check if we found cookies for chatgpt
                has_chatgpt = any("chatgpt" in c.get("domain", "") for c in found_cookies)
                if found_cookies and (has_chatgpt or not cookies):
                    cookies = found_cookies
                    active_path = path
                    if has_chatgpt:
                        break
            except Exception as e:
                print(f"Error checking profile {path}: {e}")
        
    if not cookies:
        print("No active login cookies found. Please run the bot locally first and ensure you are logged into ChatGPT!")
        return
        
    print(f"Found active session cookies in: {active_path}")
    
    # Format as JSON string
    session_json = json.dumps(cookies)
    print("\n" + "="*80)
    print("SUCCESS: Session cookies exported!")
    print("Copy the entire line below and set it as the CHATGPT_STORAGE_STATE environment variable in Render:")
    print("="*80)
    print(session_json)
    print("="*80 + "\n")

if __name__ == "__main__":
    asyncio.run(export())
