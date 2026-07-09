import asyncio
import sys
import os
import logging
from boundier.config import load_config
from boundier.logger import setup_logging
from boundier.gemini.driver import PlaywrightDriver

async def debug_gemini():
    logger = setup_logging()
    logger.info("Starting Gemini DOM Debugger...")
    
    try:
        config = load_config("config.yaml")
        config.playwright.headless = False # Visual mode
        
        driver = PlaywrightDriver(config)
        await driver.start()
        
        is_logged_in = await driver.check_session_active()
        if not is_logged_in:
            logger.error("Not logged in. Please run verify_milestone2 first.")
            await driver.stop()
            return
            
        page = driver.page
        selectors = driver.selectors
        
        # Capture initial screen
        os.makedirs("logs/screenshots", exist_ok=True)
        await page.screenshot(path="logs/screenshots/debug_0_start.png")
        logger.info("Saved start screenshot.")
        
        # Navigate to /app to make sure we are clean
        await page.goto("https://gemini.google.com/app", wait_until="domcontentloaded")
        await page.wait_for_selector(selectors.chat_input)
        await page.screenshot(path="logs/screenshots/debug_1_app_loaded.png")
        
        # Focus and fill prompt
        input_loc = page.locator(selectors.chat_input).first
        await input_loc.click()
        await input_loc.fill("Tell me a 3-word joke.")
        await asyncio.sleep(1)
        await page.screenshot(path="logs/screenshots/debug_2_prompt_filled.png")
        
        # Log submit button state
        submit_btn = page.locator(selectors.submit_button).first
        is_disabled = await submit_btn.is_disabled()
        logger.info(f"Submit button disabled status: {is_disabled}")
        
        # Click submit
        await submit_btn.click()
        logger.info("Clicked submit.")
        
        # Capture screenshots over 10 seconds
        for i in range(1, 11):
            await asyncio.sleep(1)
            await page.screenshot(path=f"logs/screenshots/debug_3_after_{i}.png")
            # Log count of response containers
            count = await page.locator(selectors.response_containers).count()
            logger.info(f"Second {i} -> Response container count: {count}")
            
            # If we found elements, log their tags and classes
            if count > 0:
                html = await page.locator(selectors.response_containers).last.inner_html()
                logger.info(f"Second {i} -> Last container HTML length: {len(html)}")
                
        await driver.stop()
        logger.info("Debugging finished.")
        
    except Exception as e:
        logger.error(f"Debugger crashed: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(debug_gemini())
