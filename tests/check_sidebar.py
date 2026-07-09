import asyncio
import sys
import logging
from boundier.config import load_config
from boundier.logger import setup_logging
from boundier.gemini.driver import PlaywrightDriver

async def check_sidebar():
    logger = setup_logging()
    
    try:
        config = load_config("config.yaml")
        config.playwright.headless = False
        
        driver = PlaywrightDriver(config)
        await driver.start()
        
        page = driver.page
        await page.goto("https://gemini.google.com/app", wait_until="domcontentloaded")
        await page.wait_for_selector(driver.selectors.chat_input)
        
        # Log all links
        links = await page.locator("a").all()
        logger.info(f"Total links on page: {len(links)}")
        for i, link in enumerate(links):
            href = await link.get_attribute("href")
            text = await link.text_content()
            text_str = text.strip().replace("\n", " ") if text else ""
            if href and ("/app/" in href or "c/" in href):
                logger.info(f"Link [{i}]: href='{href}', text='{text_str}'")
                
        # Send prompt
        input_loc = page.locator(driver.selectors.chat_input).first
        await input_loc.click()
        await input_loc.fill("Say 'HELLO' and nothing else.")
        await page.locator(driver.selectors.submit_button).first.click()
        
        # Wait 5 seconds
        await asyncio.sleep(5)
        
        # Log all links again
        links = await page.locator("a").all()
        logger.info("After prompt, checking links...")
        for i, link in enumerate(links):
            href = await link.get_attribute("href")
            text = await link.text_content()
            text_str = text.strip().replace("\n", " ") if text else ""
            if href and ("/app/" in href or "c/" in href):
                logger.info(f"Link [{i}]: href='{href}', text='{text_str}'")
                
        # Log current page URL
        logger.info(f"Final Page URL: {page.url}")
        
        await driver.stop()
        
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(check_sidebar())
