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
from boundier.discord_bot.cogs import BoundierCog, ResponseView
from boundier.core.models import Session

async def test_phase3():
    logger = setup_logging()
    logger.info("Starting Phase 3 Mock Verification Test (White Embeds, Copy/Retry & Speed Optimizations)...")
    
    db_file = "phase3_test.db"
    schema_file = "schema.sql"
    mem_dir = "memory"
    
    if os.path.exists(db_file):
        os.remove(db_file)
        
    config = load_config("config.yaml")
    store = SQLiteStore(db_path=db_file, schema_path=schema_file, memory_dir=mem_dir)
    
    # Mock Playwright service and page
    mock_page = MagicMock()
    mock_page.url = "https://chatgpt.com/c/active-chat-id-123"
    
    mock_service = MagicMock()
    mock_service.page = mock_page
    mock_service.driver = MagicMock()
    mock_service.driver.lease_page = AsyncMock(return_value=mock_page)
    mock_service.driver.release_page = AsyncMock()
    mock_service.driver.ensure_authenticated = AsyncMock(return_value=True)
    mock_service.open_conversation = AsyncMock(return_value=True)
    mock_service.create_new_conversation = AsyncMock(return_value=True)
    
    # Mock send_prompt_stream to inspect parameters passed
    passed_skip_settle = None
    async def mock_send_prompt_stream(*args, **kwargs):
        nonlocal passed_skip_settle
        passed_skip_settle = kwargs.get("skip_settle", False)
        yield "LMAOO GTA SA"
    mock_service.send_prompt_stream = mock_send_prompt_stream
    mock_service.extract_chat_id = MagicMock(return_value="mock-chat-id-xyz")
    
    manager = ConversationManager(config, mock_service, store)
    
    # Mock Bot client
    mock_bot = MagicMock()
    mock_bot.config = config
    mock_bot.store = store
    mock_bot.manager = manager
    
    # Instantiate Cog
    cog = BoundierCog(mock_bot)
    
    # 1. VERIFY SPEED OPTIMIZATION (Redundant navigation bypass & skip_settle flag)
    logger.info("Verifying speed optimization pipeline...")
    session = Session(thread_id=111, chatgpt_chat_id="active-chat-id-123", channel_id=222)
    manager._active_sessions[111] = session
    
    # Run stream
    generator = manager.execute_prompt_stream(
        thread_id=111,
        channel_id=222,
        channel_name="gta-sa",
        user_message="Test prompt speed optimization"
    )
    async for chunk in generator:
        pass
        
    # Assertions
    mock_service.open_conversation.assert_not_called()
    assert passed_skip_settle is True, "skip_settle was not passed as True for active URL!"
    logger.info("Verified: open_conversation was skipped and skip_settle=True was passed to stream generator!")
    
    # 2. VERIFY CONDITIONAL CHANNEL RENAMING (rename_parent check)
    logger.info("Verifying conditional parent channel renaming...")
    
    # CASE A: /new type:new_channel -> rename_parent is True
    session_new = Session(thread_id=333, chatgpt_chat_id="NEW", channel_id=444)
    session_new.rename_parent = True
    session_new.conversation_title = "GTA SA Adventures"
    
    mock_thread = MagicMock(spec=discord.Thread)
    mock_thread.edit = AsyncMock()
    mock_parent = MagicMock(spec=discord.TextChannel)
    mock_parent.id = 444
    mock_parent.edit = AsyncMock()
    
    def mock_get_channel(cid):
        if cid == 333:
            return mock_thread
        elif cid == 444:
            return mock_parent
        return None
    mock_bot.get_channel = mock_get_channel
    
    # Trigger background renaming callback with rename_parent = True
    await cog.on_manager_thread_rename(333, 444, "GTA SA Adventures")
    mock_thread.edit.assert_called_once_with(name="GTA SA Adventures")
    mock_parent.edit.assert_called_once_with(name="gta-sa-adventures")
    logger.info("Verified: Parent channel is renamed when rename_parent is True.")
    
    # CASE B: /ask -> rename_parent is False
    mock_thread.edit.reset_mock()
    mock_parent.edit.reset_mock()
    
    # Trigger background renaming callback with channel_id = 0 (rename_parent = False)
    await cog.on_manager_thread_rename(333, 0, "GTA SA Adventures")
    mock_thread.edit.assert_called_once_with(name="GTA SA Adventures")
    mock_parent.edit.assert_not_called()
    logger.info("Verified: Parent channel remains untouched when rename_parent is False.")
    
    # 2.5 VERIFY 403 FORBIDDEN FALLBACK (On-the-spot response)
    logger.info("Verifying 403 Forbidden thread permission fallback...")
    
    mock_channel_403 = MagicMock(spec=discord.TextChannel)
    mock_channel_403.id = 55555
    mock_channel_403.name = "general"
    
    # Mock create_thread to raise discord.Forbidden
    mock_response_forbidden = MagicMock()
    mock_response_forbidden.status = 403
    mock_response_forbidden.reason = "Forbidden"
    mock_channel_403.create_thread = AsyncMock(side_effect=discord.Forbidden(mock_response_forbidden, "Forbidden thread creation"))
    
    mock_guild_403 = MagicMock(spec=discord.Guild)
    mock_guild_403.text_channels = [mock_channel_403]
    
    mock_interaction_403 = MagicMock(spec=discord.Interaction)
    mock_interaction_403.guild = mock_guild_403
    mock_interaction_403.user = MagicMock()
    mock_interaction_403.user.id = 12345
    mock_interaction_403.user.name = "Sujay"
    mock_interaction_403.user.display_name = "Sujay"
    mock_interaction_403.channel = mock_channel_403
    mock_interaction_403.response = MagicMock()
    mock_interaction_403.response.defer = AsyncMock()
    mock_interaction_403.followup = MagicMock()
    mock_interaction_403.followup.send = AsyncMock()
    
    with patch.object(cog, '_process_message_stream', new=AsyncMock()) as mock_stream:
        await cog.new_chat.callback(
            cog,
            interaction=mock_interaction_403,
            type="new_thread",
            prompt="GTA SA secrets",
            channel=mock_channel_403
        )
        
        mock_stream.assert_called_once()
        args, kwargs = mock_stream.call_args
        assert args[0] == mock_channel_403, f"Expected fallback target to be TextChannel, got {args[0]}"
        logger.info("Verified: 403 Forbidden safely caught and successfully fell back to direct channel streaming!")
        
    # 3. VERIFY EMBED & BUTTONS UI
    logger.info("Verifying embed formatting and UI components...")
    
    mock_thread_ui = MagicMock(spec=discord.Thread)
    mock_thread_ui.id = 88888
    mock_thread_ui.name = "gta-sa-adventures"
    mock_thread_ui.parent = MagicMock()
    mock_thread_ui.parent.name = "gta-sa"
    mock_reply_msg = MagicMock(spec=discord.Message)
    mock_reply_msg.edit = AsyncMock()
    mock_thread_ui.send = AsyncMock(return_value=mock_reply_msg)
    
    # Execute _process_message_stream
    await cog._process_message_stream(
        thread=mock_thread_ui,
        channel_id=444,
        channel_name="gta-sa",
        user_message="Test prompt",
        file_paths=[],
        is_first_response=True,
        rename_parent=False
    )
    
    # Assert thread.send called with white embed
    args, kwargs = mock_thread_ui.send.call_args
    assert "embed" in kwargs, "Response was not sent inside an embed!"
    sent_embed = kwargs["embed"]
    assert sent_embed.color.value == 0xFFFFFF, f"Expected white embed color (16777215), got {sent_embed.color.value}"
    
    # Assert reply_message.edit attached the ResponseView with Copy/Retry buttons
    edit_args, edit_kwargs = mock_reply_msg.edit.call_args
    assert "view" in edit_kwargs, "ResponseView was not attached to final response!"
    attached_view = edit_kwargs["view"]
    assert isinstance(attached_view, ResponseView), "Attached view is not ResponseView instance!"
    
    # Verify buttons present
    buttons = attached_view.children
    button_ids = [btn.custom_id for btn in buttons]
    assert "copy_response" in button_ids, "Copy response button missing!"
    assert "retry_response" in button_ids, "Retry prompt button missing!"
    
    # 3.5 VERIFY CITATION PARSING & CITATIONS BUTTON
    logger.info("Verifying citation parsing and dynamic button attachment...")
    from boundier.discord_bot.cogs import parse_citations
    sample_text = "According to [official wiki](https://gta.fandom.com/wiki/Grand_Theft_Auto:_San_Andreas), the game was released in 2004 [source](https://rockstargames.com)."
    cleaned_txt, citation_urls = parse_citations(sample_text)
    
    assert cleaned_txt == "According to official wiki [1], the game was released in 2004 [2].", f"Citations cleaned incorrectly: '{cleaned_txt}'"
    assert citation_urls == [
        "https://gta.fandom.com/wiki/Grand_Theft_Auto:_San_Andreas",
        "https://rockstargames.com"
    ], f"Citation URLs extracted incorrectly: {citation_urls}"
    logger.info("Verified: Citations parsed, cleaned, and extracted correctly from response markdown!")
    
    # 4. VERIFY RESTART PERSISTENCE (SQLite loading)
    logger.info("Verifying session load persistence upon restart...")
    # Save a fake session to SQLite directly
    store.save_thread(
        thread_id=999,
        channel_id=222,
        chatgpt_chat_id="old-persisted-chat-id",
        title="Old GTA Topic",
        summary="Old summary",
        message_count=5
    )
    
    # Clear active sessions in memory to simulate a restart
    manager._active_sessions.clear()
    
    # Attempt to load it again
    loaded_session = await manager.get_or_create_session(
        thread_id=999,
        channel_id=222,
        channel_name="gta-sa"
    )
    
    # Verify loaded values match SQLite records
    assert loaded_session.chatgpt_chat_id == "old-persisted-chat-id", f"Expected 'old-persisted-chat-id', got '{loaded_session.chatgpt_chat_id}'"
    assert loaded_session.conversation_title == "Old GTA Topic", f"Expected 'Old GTA Topic', got '{loaded_session.conversation_title}'"
    assert loaded_session.cached_summary == "Old summary", f"Expected 'Old summary', got '{loaded_session.cached_summary}'"
    assert loaded_session.message_count == 5, f"Expected 5, got {loaded_session.message_count}"
    # 4.5 VERIFY RECENT HISTORY CONTEXT PROPAGATION
    logger.info("Verifying recent thread history propagation context...")
    
    mock_history_msg = MagicMock(spec=discord.Message)
    mock_history_msg.id = 7777
    mock_history_msg.content = "What mission are you playing?"
    mock_history_msg.author = MagicMock()
    mock_history_msg.author.bot = False
    mock_history_msg.author.display_name = "Sujay"
    mock_history_msg.embeds = []
    
    async def mock_history(*args, **kwargs):
        yield mock_history_msg
        
    mock_thread_ui.history = mock_history
    
    # Trigger /ask inside thread
    mock_interaction_thread = MagicMock(spec=discord.Interaction)
    mock_interaction_thread.guild = mock_guild_403
    mock_interaction_thread.channel = mock_thread_ui
    mock_interaction_thread.user = MagicMock()
    mock_interaction_thread.user.id = 67890
    mock_interaction_thread.user.name = "John"
    mock_interaction_thread.user.display_name = "John"
    mock_interaction_thread.response = MagicMock()
    mock_interaction_thread.response.defer = AsyncMock()
    mock_interaction_thread.followup = MagicMock()
    mock_interaction_thread.followup.send = AsyncMock()
    
    with patch.object(store, 'get_thread', return_value={"channel_id": 222, "chatgpt_chat_id": "active-chat-id-123", "thread_title": "Title", "thread_summary": "Summary", "message_count": 3}):
        with patch.object(cog, '_process_message_stream', new=AsyncMock()) as mock_stream:
            await cog.ask.callback(
                cog,
                interaction=mock_interaction_thread,
                prompt="Wrong side of the tracks"
            )
            
            # Verify stream NOT called because /ask fails inside active ChatGPT thread
            mock_stream.assert_not_called()
            mock_interaction_thread.followup.send.assert_called_once()
            args, kwargs = mock_interaction_thread.followup.send.call_args
            assert "linked to an active ChatGPT conversation" in args[0]
    # 4.6 VERIFY MESSAGE EDIT EVENT DETECTION & CHATGPT EDIT PROPAGATION
    logger.info("Verifying message edit detection and propagation pipeline...")
    
    mock_msg_before = MagicMock(spec=discord.Message)
    mock_msg_before.id = 8888
    mock_msg_before.content = "Wrong side of the tracks"
    
    mock_msg_after = MagicMock(spec=discord.Message)
    mock_msg_after.id = 8888
    mock_msg_after.content = "Correct side of the tracks"
    mock_msg_after.author = MagicMock()
    mock_msg_after.author.bot = False
    mock_msg_after.author.id = 12345
    mock_msg_after.author.name = "Sujay"
    mock_msg_after.author.display_name = "Sujay"
    mock_msg_after.channel = mock_thread_ui
    
    # Mock bot response message
    mock_bot_msg = MagicMock(spec=discord.Message)
    mock_bot_msg.id = 8889
    mock_bot_msg.author = MagicMock()
    mock_bot_msg.author.id = mock_bot.user.id
    mock_bot_msg.delete = AsyncMock()
    
    # Setup history of the thread
    async def mock_history_edit(*args, **kwargs):
        yield mock_bot_msg
        yield mock_msg_after
        
    mock_thread_ui.history = mock_history_edit
    
    # Mock bot user ID for matching
    mock_bot.user = MagicMock()
    mock_bot.user.id = 12345
    mock_bot_msg.author.id = 12345
    
    with patch.object(store, 'get_thread', return_value={"channel_id": 222, "chatgpt_chat_id": "active-chat-id-123", "thread_title": "Title", "thread_summary": "Summary", "message_count": 3}):
        with patch.object(cog, '_process_message_stream', new=AsyncMock()) as mock_stream:
            await cog.on_message_edit(mock_msg_before, mock_msg_after)
            
            # Assert bot's previous response was deleted
            mock_bot_msg.delete.assert_called_once()
            
            # Assert process stream was called with is_edit=True
            mock_stream.assert_called_once()
            args, kwargs = mock_stream.call_args
            assert kwargs.get("is_edit") is True, "is_edit flag was not passed as True for edited message stream!"
            logger.info("Verified: User message edits are successfully detected, bot stale responses are deleted, and generation is re-triggered with is_edit=True!")

    # 4. VERIFY /read SLASH COMMAND AND YES/SKIP PROMPT
    logger.info("Verifying /read slash command and Yes/Skip interactive prompting...")

    # Mock normal messages history
    mock_msg_normal = MagicMock(spec=discord.Message)
    mock_msg_normal.clean_content = "Hello there"
    mock_msg_normal.author = MagicMock()
    mock_msg_normal.author.id = 555
    mock_msg_normal.author.display_name = "UserA"
    mock_msg_normal.interaction = None

    # Mock long message history
    mock_msg_long = MagicMock(spec=discord.Message)
    mock_msg_long.clean_content = "A" * 1500  # > 1000 characters
    mock_msg_long.author = MagicMock()
    mock_msg_long.author.id = 666
    mock_msg_long.author.display_name = "UserB"
    mock_msg_long.interaction = None

    async def mock_history_read(*args, **kwargs):
        yield mock_msg_normal
        yield mock_msg_long

    mock_channel_read = MagicMock(spec=discord.TextChannel)
    mock_channel_read.id = 77777
    mock_channel_read.name = "general"
    mock_channel_read.history = mock_history_read
    mock_channel_read.permissions_for = MagicMock(return_value=MagicMock(create_public_threads=True))

    mock_thread_read = MagicMock(spec=discord.Thread)
    mock_thread_read.id = 99999
    mock_thread_read.name = "Read History Context"
    mock_channel_read.create_thread = AsyncMock(return_value=mock_thread_read)

    mock_interaction_read = MagicMock(spec=discord.Interaction)
    mock_interaction_read.guild = MagicMock()
    mock_interaction_read.user = MagicMock()
    mock_interaction_read.user.id = 12345
    mock_interaction_read.user.name = "Sujay"
    mock_interaction_read.user.display_name = "Sujay"
    mock_interaction_read.channel = mock_channel_read
    mock_interaction_read.response = MagicMock()
    mock_interaction_read.response.defer = AsyncMock()
    mock_interaction_read.followup = MagicMock()
    mock_interaction_read.followup.send = AsyncMock()

    # We mock YesSkipPrompt to simulate the user clicking "Yes" (confirm)
    with patch("boundier.discord_bot.cogs.YesSkipPrompt") as mock_view_class:
        mock_view_instance = MagicMock()
        mock_view_instance.wait = AsyncMock()
        mock_view_instance.value = True # User clicks Yes
        mock_view_class.return_value = mock_view_instance

        with patch.object(cog, '_process_message_stream', new=AsyncMock()) as mock_stream:
            await cog.read.callback(
                cog,
                interaction=mock_interaction_read,
                prompt="Summarize this chat"
            )
            
            # Assert process stream was called
            mock_stream.assert_called_once()
            args, kwargs = mock_stream.call_args
            # User message contains history + prompt
            user_message = kwargs.get("user_message")
            assert "Hello there" in user_message
            assert "A" * 1500 in user_message
            assert "Summarize this chat" in user_message
            logger.info("Verified: /read slash command interactively prompts and compiles context correctly!")

    logger.info("Phase 3 Mock Verification Completed successfully!")
    if os.path.exists(db_file):
        os.remove(db_file)

if __name__ == "__main__":
    asyncio.run(test_phase3())
