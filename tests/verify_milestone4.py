import os
import sys
import time
import shutil
import logging
from boundier.logger import setup_logging
from boundier.storage.sqlite_store import SQLiteStore

def test_storage():
    logger = setup_logging()
    logger.info("Starting Milestone 4 Verification Test...")
    
    db_file = "test_boundier.db"
    schema_file = "schema.sql"
    mem_dir = "test_memory"
    
    # Clean up test leftovers
    if os.path.exists(db_file):
        os.remove(db_file)
    if os.path.exists(mem_dir):
        import shutil
        shutil.rmtree(mem_dir)
        
    try:
        # 1. Initialize store
        store = SQLiteStore(db_path=db_file, schema_path=schema_file, memory_dir=mem_dir)
        logger.info("SQLiteStore initialized.")
        
        # 2. Save a test channel
        test_channel_id = 9999
        test_channel_name = "pytest-channel"
        original_summary = "This is the original channel summary in DB."
        
        store.save_channel(
            channel_id=test_channel_id,
            channel_name=test_channel_name,
            category_id=111,
            category_name="Test Category",
            summary=original_summary
        )
        logger.info(f"Test channel '{test_channel_name}' saved to SQLite.")
        
        # Verify markdown file was written
        md_file_path = os.path.join(mem_dir, f"{test_channel_name}.md")
        if not os.path.exists(md_file_path):
            raise FileNotFoundError(f"Markdown file was not created at: {md_file_path}")
            
        with open(md_file_path, "r", encoding="utf-8") as f:
            md_content = f.read()
        logger.info(f"Verified Markdown file exists. Content: '{md_content}'")
        
        # 3. Simulate user editing the markdown file manually
        new_summary_text = "This is the updated summary edited manually in the MD file!"
        
        # Sleep briefly to ensure timestamps have ticked forward
        time.sleep(1.1)
        
        with open(md_file_path, "w", encoding="utf-8") as f:
            f.write(new_summary_text)
            
        # Manually force the file modify time to be in the future to guarantee mtime > DB updated_at
        future_time = time.time() + 60
        os.utime(md_file_path, (future_time, future_time))
        logger.info("Markdown file edited manually on disk. Timestamp set to future.")
        
        # 4. Trigger bi-directional sync
        store.sync_markdown_files()
        logger.info("Markdown synchronization executed.")
        
        # 5. Fetch channel from DB and verify it ingested the markdown edits
        channel = store.get_channel(test_channel_id)
        if not channel:
            raise ValueError("Channel record not found in DB after sync.")
            
        db_summary = channel["channel_summary"]
        logger.info(f"Fetched channel from DB. Summary content: '{db_summary}'")
        
        if db_summary == new_summary_text:
            logger.info("Success! SQLite database successfully ingested the manually edited markdown summary!")
        else:
            raise ValueError(f"Sync failed. Expected: '{new_summary_text}', Got: '{db_summary}'")
            
        # 6. Test thread repository methods
        test_thread_id = 8888
        test_chat_id = "chatgpt-uuid-1234"
        store.save_thread(
            thread_id=test_thread_id,
            channel_id=test_channel_id,
            chatgpt_chat_id=test_chat_id,
            title="Sample Conversation Title",
            summary="Running thread summary context.",
            message_count=3
        )
        logger.info("Test thread saved.")
        
        thread = store.get_thread(test_thread_id)
        if not thread or thread["chatgpt_chat_id"] != test_chat_id:
            raise ValueError("Thread mapping mismatch or not found.")
        logger.info(f"Verified thread mapping: Thread ID {test_thread_id} maps to ChatGPT Chat ID '{thread['chatgpt_chat_id']}'")
        
        # Cleanup
        os.remove(db_file)
        shutil.rmtree(mem_dir)
        logger.info("Test database and directories cleared.")
        
        logger.info("Milestone 4 Verification Completed successfully.")
        
    except Exception as e:
        logger.error(f"Milestone 4 test failed: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    test_storage()
