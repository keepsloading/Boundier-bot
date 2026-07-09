import asyncio
import sys
import logging
from boundier.config import load_config
from boundier.logger import setup_logging
from boundier.chatgpt.driver import PlaywrightDriver
from boundier.chatgpt.service import ChatGPTService

async def test_service():
    logger = setup_logging()
    logger.info("Starting Milestone 3 Verification Test (ChatGPT)...")
    
    try:
        config = load_config("config.yaml")
        
        # Override headless to False so the user can watch the automation execute
        if config.playwright.headless:
            logger.info("Overriding headless to False for visual validation...")
            config.playwright.headless = False
            
        driver = PlaywrightDriver(config)
        await driver.start()
        
        # Verify active session before sending prompt
        is_logged_in = await driver.check_session_active()
        if not is_logged_in:
            logger.error("No active session detected. Run verify_milestone2 first to authenticate.")
            await driver.stop()
            sys.exit(1)
            
        service = ChatGPTService(driver)
        
        # Create a new conversation
        success = await service.create_new_conversation()
        if not success:
            logger.error("Failed to load a new conversation page.")
            await driver.stop()
            sys.exit(1)
            
        # Send a prompt and read the stream
        test_prompt = "Say only the word 'BOUNDIER_VERIFIED' in uppercase, followed by a newline, then tell me a 5-word joke."
        logger.info(f"Submitting test prompt: '{test_prompt}'")
        
        print("\n--- ChatGPT Output Stream ---")
        accumulated_text = ""
        async for chunk in service.send_prompt_stream(test_prompt):
            print(chunk, end="", flush=True)
            accumulated_text += chunk
        print("\n--- End of Stream ---\n")
        
        # Extract metadata (with retry wait as URL changes asynchronously)
        chat_id = None
        for _ in range(8):
            chat_id = service.extract_chat_id()
            if chat_id:
                break
            await asyncio.sleep(1.0)
            
        logger.info(f"Extracted Chat ID: {chat_id}")
        
        # Get sidebar title
        sidebar_title = await service.get_sidebar_title()
        logger.info(f"Sidebar Title: {sidebar_title}")
        
        # Verify the target keyword is present
        if "BOUNDIER_VERIFIED" in accumulated_text:
            logger.info("Prompt validation matched expected keyword! Milestone 3 Success.")
        else:
            logger.warning("Target verification keyword was not returned by ChatGPT.")
            
        logger.info("Shutting down driver...")
        await driver.stop()
        logger.info("Milestone 3 Verification Completed.")
        
    except Exception as e:
        logger.error(f"Verification script crashed with error: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(test_service())
