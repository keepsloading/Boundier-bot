import asyncio
import os
import sys
import logging
from unittest.mock import AsyncMock, MagicMock, patch
import discord
from boundier.config import load_config
from boundier.logger import setup_logging
from boundier.storage.sqlite_store import SQLiteStore
from boundier.core.manager import ConversationManager
from boundier.discord_bot.cogs import BoundierCog

async def test_phase2():
    logger = setup_logging()
    logger.info("Starting Phase 2 Mock Verification Test (Instant Routing & Attachments)...")
    
    db_file = "phase2_test.db"
    schema_file = "schema.sql"
    mem_dir = "memory"
    
    if os.path.exists(db_file):
        os.remove(db_file)
        
    config = load_config("config.yaml")
    store = SQLiteStore(db_path=db_file, schema_path=schema_file, memory_dir=mem_dir)
    
    # Mock Manager
    mock_service = MagicMock()
    manager = ConversationManager(config, mock_service, store)
    
    # Mock execute_prompt_stream as a real async generator to avoid TypeError
    called_execute = False
    passed_args = []
    passed_kwargs = {}
    
    async def mock_execute_stream(*args, **kwargs):
        nonlocal called_execute, passed_args, passed_kwargs
        called_execute = True
        passed_args = args
        passed_kwargs = kwargs
        yield "LMAOO peak 2000s energy."
        yield " GTA SA is ridiculously fun."
        
    manager.execute_prompt_stream = mock_execute_stream
    
    # Mock Bot client
    mock_bot = MagicMock()
    mock_bot.config = config
    mock_bot.store = store
    mock_bot.manager = manager
    
    # Instantiate Cog
    cog = BoundierCog(mock_bot)
    
    # Mock Attachment Downloading
    mock_attachment = MagicMock(spec=discord.Attachment)
    mock_attachment.filename = "test_image.png"
    
    # Mock att.save to write a dummy file
    async def mock_save(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write("dummy-image-bytes")
    mock_attachment.save = mock_save
    
    # Mock Discord Interaction
    mock_unwatched_channel = MagicMock(spec=discord.TextChannel)
    mock_unwatched_channel.id = 9999
    mock_unwatched_channel.name = "general"
    
    mock_guild = MagicMock(spec=discord.Guild)
    mock_guild.text_channels = []
    
    # Mock new workspace channel creation inside category
    mock_new_channel = MagicMock(spec=discord.TextChannel)
    mock_new_channel.id = 777888
    mock_new_channel.name = "yo-i-am-playing-gta"
    
    mock_thread = MagicMock(spec=discord.Thread)
    mock_thread.id = 111222
    mock_thread.send = AsyncMock(return_value=MagicMock(spec=discord.Message))
    
    mock_new_channel.create_thread = AsyncMock(return_value=mock_thread)
    mock_guild.create_text_channel = AsyncMock(return_value=mock_new_channel)
    
    mock_interaction = MagicMock(spec=discord.Interaction)
    mock_interaction.id = 123456789
    mock_interaction.guild = mock_guild
    mock_interaction.channel = mock_unwatched_channel
    mock_interaction.response = MagicMock()
    mock_interaction.response.defer = AsyncMock()
    mock_interaction.followup = MagicMock()
    mock_interaction.followup.send = AsyncMock()
    mock_interaction.user = MagicMock()
    mock_interaction.user.mention = "@Sujay"
    
    from boundier.core.models import Session
    session = Session(thread_id=111222, chatgpt_chat_id="gta-uuid-99", channel_id=777888)
    session.conversation_title = "GTA SA Adventures"
    manager._active_sessions[111222] = session
    
    try:
        logger.info("Triggering /ask with prompt='yo i am playing gta sa 💀😭' and image attachment...")
        
        await cog.new_chat.callback(
            cog,
            interaction=mock_interaction,
            type="new_channel",
            prompt="yo i am playing gta sa 💀😭",
            attachment=mock_attachment
        )
        
        # Give asyncio tasks a brief moment to run
        await asyncio.sleep(0.5)
        
        # Verify:
        # 1. execute_prompt_stream called with downloaded attachment path
        assert called_execute, "execute_prompt_stream was never called!"
        expected_local_path = os.path.abspath(os.path.join("scratch/attachments", "test_image.png"))
        assert passed_kwargs["file_paths"] == [expected_local_path], "Attachment local path not passed to manager!"
        logger.info(f"Verified: execute_prompt_stream requested to upload local attachment: {expected_local_path}")
        
        # 2. Dynamic temporary channel name created instantly based on prompt
        mock_guild.create_text_channel.assert_called_once()
        created_channel_name = mock_guild.create_text_channel.call_args[1]["name"]
        assert created_channel_name == "yo-i-am-playing-gta", f"Expected channel name 'yo-i-am-playing-gta', got '{created_channel_name}'"
        logger.info(f"Verified: temporary channel name created instantly: #{created_channel_name}")
        
        # 3. Thread created inside temporary channel using derived temp title
        mock_new_channel.create_thread.assert_called_once()
        thread_name = mock_new_channel.create_thread.call_args[1]["name"]
        assert thread_name == "yo i am playing gta...", f"Expected thread name 'yo i am playing gta...', got '{thread_name}'"
        logger.info(f"Verified: Thread spawned inside channel using temp title: '{thread_name}'")
        
        # 4. Cleanup checked (temp downloaded file removed from disk)
        assert not os.path.exists(expected_local_path), "Local temporary attachment file was not cleaned up!"
        logger.info("Verified: Local temporary attachment file was cleaned up successfully.")
        
        # Verify Background Renaming Event Callback
        # Call on_manager_thread_rename directly to verify channel and thread edit calls
        logger.info("Verifying background auto-renaming cog logic...")
        
        mock_thread_to_rename = MagicMock(spec=discord.Thread)
        mock_thread_to_rename.edit = AsyncMock()
        
        mock_parent_to_rename = MagicMock(spec=discord.TextChannel)
        mock_parent_to_rename.id = 777888
        mock_parent_to_rename.edit = AsyncMock()
        
        def mock_get_channel(cid):
            if cid == 111222:
                return mock_thread_to_rename
            elif cid == 777888:
                return mock_parent_to_rename
            return None
        mock_bot.get_channel = mock_get_channel
        
        async def mock_fetch(cid):
            if cid == 111222:
                return mock_thread_to_rename
            return mock_parent_to_rename
            
        mock_bot.fetch_channel = mock_fetch
        
        # Invoke the callback
        await cog.on_manager_thread_rename(111222, 777888, "GTA SA Adventures")
        
        # Assert thread name updated
        mock_thread_to_rename.edit.assert_called_once_with(name="GTA SA Adventures")
        # Assert parent channel name updated to sanitized lowercase name
        mock_parent_to_rename.edit.assert_called_once_with(name="gta-sa-adventures")
        logger.info("Verified: on_manager_thread_rename successfully renames both thread and parent channel!")
        
        logger.info("Phase 2 Mock Verification Completed successfully.")
        
    except Exception as e:
        logger.error(f"Phase 2 test failed: {e}", exc_info=True)
        sys.exit(1)
    finally:
        # Cleanup
        if os.path.exists(db_file):
            os.remove(db_file)

if __name__ == "__main__":
    asyncio.run(test_phase2())
