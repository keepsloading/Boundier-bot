import os
import asyncio
import sys
import logging
from boundier.config import load_config
from boundier.logger import setup_logging
from boundier.chatgpt.driver import PlaywrightDriver
from boundier.chatgpt.service import ChatGPTService
from boundier.storage.sqlite_store import SQLiteStore
from boundier.core.manager import ConversationManager

# Flag variables to verify event dispatches
event_chat_created = False
event_thread_renamed = False
event_summary_updated = False

async def on_chat_created(thread_id, chat_id):
    global event_chat_created
    event_chat_created = True
    logging.getLogger("boundier.test").info(f"EVENT RECEIVED: ConversationCreated for thread {thread_id} -> {chat_id}")

async def on_thread_renamed(thread_id, title):
    global event_thread_renamed
    event_thread_renamed = True
    logging.getLogger("boundier.test").info(f"EVENT RECEIVED: ThreadRenamed for thread {thread_id} -> '{title}'")

async def on_summary_updated(channel_id, summary):
    global event_summary_updated
    event_summary_updated = True
    logging.getLogger("boundier.test").info(f"EVENT RECEIVED: SummaryUpdated for channel {channel_id} -> '{summary}'")

async def test_manager():
    logger = setup_logging()
    logger.info("Starting Milestone 5 Verification Test (ChatGPT)...")
    
    db_file = "milestone5_test.db"
    schema_file = "schema.sql"
    mem_dir = "memory"
    
    # Initialize components
    config = load_config("config.yaml")
    
    # Override headless to False for verification
    if config.playwright.headless:
        logger.info("Overriding headless to False for visual validation...")
        config.playwright.headless = False
        
    driver = PlaywrightDriver(config)
    await driver.start()
    
    # Verify authentication
    is_logged_in = await driver.check_session_active()
    if not is_logged_in:
        logger.error("ChatGPT session is unauthenticated. Run tests.verify_milestone2 first.")
        await driver.stop()
        sys.exit(1)
        
    service = ChatGPTService(driver)
    store = SQLiteStore(db_path=db_file, schema_path=schema_file, memory_dir=mem_dir)
    manager = ConversationManager(config, service, store)
    
    # Register event listeners
    manager.events.register("ConversationCreated", on_chat_created)
    manager.events.register("ThreadRenamed", on_thread_renamed)
    manager.events.register("SummaryUpdated", on_summary_updated)
    
    # Define test parameters
    test_thread_id = 9911
    test_channel_id = 8822
    test_channel_name = "test-manager-help"
    test_message = "Say only the word 'BOUNDIER_MANAGER_OK' in uppercase."
    
    try:
        # Pre-populate channel summary to verify memory injection rules
        store.save_channel(
            channel_id=test_channel_id,
            channel_name=test_channel_name,
            summary="Channel rule: Always write in concise blocks."
        )
        
        logger.info("Submitting prompt stream through Manager...")
        print("\n--- Stream Output ---")
        accumulated_response = ""
        async for chunk in manager.execute_prompt_stream(
            thread_id=test_thread_id,
            channel_id=test_channel_id,
            channel_name=test_channel_name,
            user_message=test_message
        ):
            print(chunk, end="", flush=True)
            accumulated_response += chunk
        print("\n--- End of Stream ---\n")
        
        # Verify response matches
        if "BOUNDIER_MANAGER_OK" in accumulated_response:
            logger.info("Response verification matched expected output!")
        else:
            logger.warning("Response did not contain validation keyword.")
            
        # Give background rename task a moment to execute
        logger.info("Waiting 6 seconds for background auto-rename task...")
        await asyncio.sleep(6.0)
        
        # Verify event creation was captured
        if event_chat_created:
            logger.info("Chat creation event verified!")
        else:
            logger.warning("Chat creation event was not received.")
            
        if event_thread_renamed:
            logger.info("Thread rename event verified!")
        else:
            logger.warning("Thread rename event was not received (or ChatGPT sidebar took too long).")
            
        # 6. Test Archive Flow (Memory flow aggregation)
        # Mock thread summary update before archiving
        session = await manager.get_or_create_session(test_thread_id, test_channel_id, test_channel_name)
        session.cached_summary = "Thread resolved checking manager functionality."
        store.save_thread(
            thread_id=test_thread_id,
            channel_id=test_channel_id,
            chatgpt_chat_id=session.chatgpt_chat_id,
            summary=session.cached_summary
        )
        
        logger.info("Triggering thread archival context merge...")
        await manager.archive_thread(test_thread_id)
        
        # Verify channel summary update event and file synchronization
        if event_summary_updated:
            logger.info("Archival summary update event verified!")
        else:
            logger.warning("Archival summary update event was not received.")
            
        # Verify Markdown file content
        md_file = os.path.join(mem_dir, f"{test_channel_name}.md")
        if os.path.exists(md_file):
            with open(md_file, "r", encoding="utf-8") as f:
                content = f.read()
            logger.info(f"Verified memory/{test_channel_name}.md exists. Content:\n{content}")
        else:
            logger.error("Markdown summary file was not found!")
            
        logger.info("Tearing down test database...")
        # Clear database
        import shutil
        if os.path.exists(db_file):
            os.remove(db_file)
        if os.path.exists(md_file):
            os.remove(md_file)
            
        logger.info("Shutting down driver...")
        await driver.stop()
        logger.info("Milestone 5 Verification Completed.")
        
    except Exception as e:
        logger.error(f"Manager verification failed: {e}", exc_info=True)
        if os.path.exists(db_file):
            os.remove(db_file)
        await driver.stop()
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(test_manager())
