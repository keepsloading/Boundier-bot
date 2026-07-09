import asyncio
import sys
import os
import logging
from boundier.config import load_config
from boundier.logger import setup_logging
from boundier.chatgpt.driver import PlaywrightDriver

async def check_active_profile():
    logger = setup_logging()
    logger.info("Checking persistent profile session status...")
    
    try:
        config = load_config("config.yaml")
        config.playwright.headless = True # Run headless to not bother user
        
        driver = PlaywrightDriver(config)
        await driver.start()
        
        page = driver.page
        logger.info("Loading chatgpt.com...")
        await page.goto("https://chatgpt.com", wait_until="domcontentloaded")
        await asyncio.sleep(5.0)
        
        # Log URL and save screenshot
        current_url = page.url
        logger.info(f"Loaded Page URL: {current_url}")
        
        os.makedirs("logs", exist_ok=True)
        await page.screenshot(path="logs/chatgpt_check_active.png")
        logger.info("Screenshot saved to logs/chatgpt_check_active.png")
        
        # Check selectors
        profile_menu = page.locator(driver.selectors.profile_menu_button)
        profile_count = await profile_menu.count()
        profile_visible = await profile_menu.first.is_visible() if profile_count > 0 else False
        logger.info(f"Profile button selector '{driver.selectors.profile_menu_button}' count: {profile_count}, visible: {profile_visible}")
        
        # Log all buttons visible on the page to see if there is login or user menu
        buttons = await page.locator("button").all()
        logger.info(f"Total buttons on page: {len(buttons)}")
        for idx, btn in enumerate(buttons):
            if await btn.is_visible():
                label = await btn.get_attribute("aria-label")
                testid = await btn.get_attribute("data-testid")
                text = await btn.text_content()
                text_clean = text.strip() if text else ""
                if label or testid or text_clean:
                    logger.info(f"  Button [{idx}]: text='{text_clean}', label='{label}', testid='{testid}'")
        
        await driver.stop()
        
    except Exception as e:
        logger.error(f"Error checking active profile: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(check_active_profile())
