import asyncio
import sys
import os
import logging
from unittest.mock import AsyncMock, MagicMock, patch
import discord
from boundier.config import load_config
from boundier.logger import setup_logging
from boundier.storage.sqlite_store import SQLiteStore
from boundier.core.manager import ConversationManager
from boundier.discord_bot.cogs import BoundierCog

async def test_discord_routing():
    logger = setup_logging()
    logger.info("Starting Milestone 6 Mock Verification Test (Routing & Slash Commands)...")
    
    db_file = "milestone6_test.db"
    schema_file = "schema.sql"
    mem_dir = "memory"
    
    # Clean up test leftovers
    if os.path.exists(db_file):
        os.remove(db_file)
        
    config = load_config("config.yaml")
    store = SQLiteStore(db_path=db_file, schema_path=schema_file, memory_dir=mem_dir)
    
    # Mock ChatGPT Service and Manager
    mock_service = MagicMock()
    manager = ConversationManager(config, mock_service, store)
    
    # Mock classify method to return classified target channel name
    manager.classify_prompt_channel = AsyncMock(return_value="python-help")
    
    # Mock thread stream process to avoid executing real browser loops in unit test
    process_mock = AsyncMock()
    
    # Mock Bot client
    mock_bot = MagicMock()
    mock_bot.config = config
    mock_bot.store = store
    mock_bot.manager = manager
    
    # Instantiate Cog
    cog = BoundierCog(mock_bot)
    cog._process_message_stream = process_mock
    
    # ----------------------------------------------------
    # Case 1: Inside a watched channel
    # ----------------------------------------------------
    logger.info("Case 1: User invokes /new inside a registered watched channel.")
    
    # Pre-register watched channel
    watched_channel_id = 112233
    store.save_channel(channel_id=watched_channel_id, channel_name="python", summary="")
    
    # Mock Discord Interaction elements
    mock_channel = MagicMock(spec=discord.TextChannel)
    mock_channel.id = watched_channel_id
    mock_channel.name = "python"
    
    # Mock thread creation inside channel
    mock_thread = MagicMock(spec=discord.Thread)
    mock_thread.id = 445566
    mock_channel.create_thread = AsyncMock(return_value=mock_thread)
    
    mock_interaction_1 = MagicMock(spec=discord.Interaction)
    mock_interaction_1.guild = MagicMock(spec=discord.Guild)
    mock_interaction_1.channel = mock_channel
    mock_interaction_1.response = MagicMock()
    mock_interaction_1.response.defer = AsyncMock()
    mock_interaction_1.user = MagicMock()
    mock_interaction_1.user.mention = "@User123"
    
    # Invoke handle
    await cog._handle_new_conversation(mock_interaction_1, "Help me write a Python sorting algorithm.")
    
    # Verify: Direct creation, no routing classification
    mock_interaction_1.response.defer.assert_called_once_with(ephemeral=False)
    manager.classify_prompt_channel.assert_not_called()
    mock_channel.create_thread.assert_called_once()
    process_mock.assert_called_once()
    
    logger.info("Case 1 passed: Thread created directly inside watched channel without routing.")
    
    # Reset mocks
    process_mock.reset_mock()
    manager.classify_prompt_channel.reset_mock()
    
    # ----------------------------------------------------
    # Case 2: Outside watched channel (e.g. #general or system channel)
    # ----------------------------------------------------
    logger.info("Case 2: User invokes /new outside a watched channel (requires routing classification).")
    
    unwatched_channel_id = 99999
    mock_unwatched_channel = MagicMock(spec=discord.TextChannel)
    mock_unwatched_channel.id = unwatched_channel_id
    mock_unwatched_channel.name = "general"
    
    mock_guild = MagicMock(spec=discord.Guild)
    mock_guild.text_channels = []
    
    # Mock channel creation in category
    mock_new_channel = MagicMock(spec=discord.TextChannel)
    mock_new_channel.id = 555777
    mock_new_channel.name = "python-help"
    mock_new_channel.create_thread = AsyncMock(return_value=mock_thread)
    mock_guild.create_text_channel = AsyncMock(return_value=mock_new_channel)
    
    mock_interaction_2 = MagicMock(spec=discord.Interaction)
    mock_interaction_2.guild = mock_guild
    mock_interaction_2.channel = mock_unwatched_channel
    mock_interaction_2.response = MagicMock()
    mock_interaction_2.response.defer = AsyncMock()
    mock_interaction_2.followup = MagicMock()
    mock_interaction_2.followup.send = AsyncMock()
    mock_interaction_2.user = MagicMock()
    mock_interaction_2.user.mention = "@User123"
    
    # Invoke handle
    await cog._handle_new_conversation(mock_interaction_2, "Explain list comprehensions in Python.")
    
    # Verify:
    # 1. Routing classification called
    manager.classify_prompt_channel.assert_called_once_with("Explain list comprehensions in Python.")
    # 2. Text channel created
    mock_guild.create_text_channel.assert_called_once()
    # 3. New channel registered in SQLite
    registered_channel = store.get_channel(555777)
    assert registered_channel is not None, "Routed target channel not registered in database!"
    assert registered_channel["channel_name"] == "python-help"
    # 4. Thread created in new channel
    mock_new_channel.create_thread.assert_called_once()
    # 5. Processor stream initiated
    process_mock.assert_called_once()
    
    logger.info("Case 2 passed: Prompt classified, new workspace channel created, registered in DB, and thread started!")
    
    # Cleanup
    if os.path.exists(db_file):
        os.remove(db_file)
        
    md_file = os.path.join(mem_dir, "python.md")
    if os.path.exists(md_file):
        os.remove(md_file)
        
    md_file2 = os.path.join(mem_dir, "python-help.md")
    if os.path.exists(md_file2):
        os.remove(md_file2)
        
    logger.info("Milestone 6 Mock Verification Completed successfully.")

if __name__ == "__main__":
    asyncio.run(test_discord_routing())
