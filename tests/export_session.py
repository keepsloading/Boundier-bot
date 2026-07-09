import os
import json
import asyncio
from playwright.async_api import async_playwright
from boundier.config import load_config

async def export():
    config = load_config("config.yaml")
    user_data_dir = os.path.abspath(config.playwright.user_data_dir)
    
    print(f"Loading local browser profile from: {user_data_dir}")
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=True
        )
        cookies = await context.cookies("https://chatgpt.com")
        await context.close()
        
    if not cookies:
        print("No cookies found for chatgpt.com. Please run the bot locally first and make sure you are logged in!")
        return
        
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
