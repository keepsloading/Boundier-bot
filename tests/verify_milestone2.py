import asyncio
import sys
import logging
from boundier.config import load_config
from boundier.logger import setup_logging
from boundier.chatgpt.driver import PlaywrightDriver

async def test_driver():
    logger = setup_logging()
    logger.info("Starting Milestone 2 Verification Test (ChatGPT)...")
    
    try:
        config = load_config("config.yaml")
        
        # Override headless to False for verification if it is headless to let user interact with login window
        if config.playwright.headless:
            logger.info("Temporarily overriding headless to False for manual login verification...")
            config.playwright.headless = False
            
        driver = PlaywrightDriver(config)
        await driver.start()
        
        is_logged_in = await driver.check_session_active()
        if not is_logged_in:
            logger.warning("No active session detected. Initiating manual login loop (300 second timeout)...")
            logger.warning("Please log in to your ChatGPT Account in the Chromium window that opened.")
            logged_in_successfully = await driver.wait_for_manual_login(timeout_seconds=300)
            if logged_in_successfully:
                logger.info("Login verified! Running check again to verify persistence...")
                is_logged_in = await driver.check_session_active()
                if is_logged_in:
                    logger.info("Persistence verified successfully.")
            else:
                logger.error("Failed to authenticate within the verification time limit.")
        else:
            logger.info("Active login session detected immediately! (Profile loading verified)")
            
        logger.info("Shutting down browser driver...")
        await driver.stop()
        logger.info("Milestone 2 Verification Completed.")
        
    except Exception as e:
        logger.error(f"Verification script crashed with error: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(test_driver())
