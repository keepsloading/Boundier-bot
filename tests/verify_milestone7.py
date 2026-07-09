import asyncio
import os
import sys
import logging
from boundier.config import load_config
from boundier.logger import setup_logging
from boundier.chatgpt.driver import PlaywrightDriver
from boundier.chatgpt.service import ChatGPTService

async def test_diagnostics():
    logger = setup_logging()
    logger.info("Starting Milestone 7 Verification Test (Diagnostics & Crash Recovery)...")
    
    # 1. Clean old diagnostics logs for the test
    diag_dir = "logs/diagnostics"
    if os.path.exists(diag_dir):
        for f in os.listdir(diag_dir):
            if f.endswith(".png"):
                os.remove(os.path.join(diag_dir, f))
                
    config = load_config("config.yaml")
    
    # Run headless for silent verification
    config.playwright.headless = True
    config.playwright.timeout_ms = 4000 # Shorten timeout for quick test
    
    driver = PlaywrightDriver(config)
    await driver.start()
    
    service = ChatGPTService(driver)
    
    try:
        logger.info("Simulating a page interaction failure by attempting to wait for a non-existent element...")
        # Override selector to simulate a DOM interaction failure
        service.selectors.chat_input = "#non_existent_element_id_to_trigger_exception"
        
        # This will fail and throw an exception because the selector is non-existent and will timeout
        logger.info("Calling open_conversation with invalid selectors...")
        success = await service.open_conversation("test-dummy-chat-id")
        
        if success:
            raise ValueError("Expected open_conversation to fail but it succeeded!")
            
        logger.info("Checking if diagnostics screenshot was generated on failure...")
        
        # Verify the logs/diagnostics folder contains a new png
        if not os.path.exists(diag_dir):
            raise FileNotFoundError(f"Diagnostics directory '{diag_dir}' was not created!")
            
        png_files = [f for f in os.listdir(diag_dir) if f.endswith(".png")]
        if len(png_files) > 0:
            logger.info(f"Verified! Diagnostics screenshot captured: {png_files[0]}")
            logger.info("Milestone 7 Verification Completed successfully.")
        else:
            raise FileNotFoundError("No diagnostics screenshot file (.png) was found on failure!")
            
    except Exception as e:
        logger.error(f"Milestone 7 test failed: {e}", exc_info=True)
        sys.exit(1)
    finally:
        await driver.stop()

if __name__ == "__main__":
    asyncio.run(test_diagnostics())
