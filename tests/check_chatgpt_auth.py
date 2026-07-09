import asyncio
import sys
import logging
from boundier.config import load_config
from boundier.logger import setup_logging
from boundier.chatgpt.driver import PlaywrightDriver

async def check_chatgpt_auth():
    logger = setup_logging()
    
    try:
        config = load_config("config.yaml")
        config.playwright.headless = False
        
        driver = PlaywrightDriver(config)
        await driver.start()
        
        page = driver.page
        await page.goto("https://chatgpt.com", wait_until="domcontentloaded")
        await asyncio.sleep(4.0) # Wait for login state to settle
        
        # Check profile menu button
        profile_btn = page.locator('[data-testid="profile-menu-button"]')
        profile_exists = await profile_btn.count() > 0
        profile_visible = await profile_btn.first.is_visible() if profile_exists else False
        
        logger.info(f"Profile menu button exists: {profile_exists}, visible: {profile_visible}")
        
        # Check login/signup buttons which are present when logged out
        login_btn = page.locator('button[data-testid="login-button"], a[href*="login"]')
        login_exists = await login_btn.count() > 0
        login_visible = await login_btn.first.is_visible() if login_exists else False
        logger.info(f"Login button exists: {login_exists}, visible: {login_visible}")
        
        # Save screenshot
        import os
        os.makedirs("logs", exist_ok=True)
        await page.screenshot(path="logs/chatgpt_auth_check.png")
        logger.info("Saved screen capture to logs/chatgpt_auth_check.png")
        
        await driver.stop()
        
    except Exception as e:
        logger.error(f"Error checking ChatGPT auth: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(check_chatgpt_auth())
