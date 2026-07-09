import asyncio
import sys
import logging
from boundier.config import load_config
from boundier.logger import setup_logging
from boundier.gemini.driver import PlaywrightDriver

async def check_elements():
    logger = setup_logging()
    
    try:
        config = load_config("config.yaml")
        config.playwright.headless = False
        
        driver = PlaywrightDriver(config)
        await driver.start()
        
        page = driver.page
        await page.goto("https://gemini.google.com/app", wait_until="domcontentloaded")
        await page.wait_for_selector(driver.selectors.chat_input)
        
        # Count only 'message-content'
        tag_count = await page.locator("message-content").count()
        logger.info(f"Number of 'message-content' elements on new chat page: {tag_count}")
        
        # Type and send a prompt
        input_loc = page.locator(driver.selectors.chat_input).first
        await input_loc.click()
        await input_loc.fill("Say 'VERIFIED' and nothing else.")
        await page.locator(driver.selectors.submit_button).first.click()
        
        # Poll for 8 seconds
        for i in range(8):
            await asyncio.sleep(1)
            mc_count = await page.locator("message-content").count()
            logger.info(f"Second {i+1} -> 'message-content' count: {mc_count}")
            if mc_count > 0:
                for idx in range(mc_count):
                    text = await page.locator("message-content").nth(idx).text_content()
                    logger.info(f"  [{idx}]: text_content = '{text.strip()}'")
                    
        await driver.stop()
        
    except Exception as e:
        logger.error(f"Error checking elements: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(check_elements())
