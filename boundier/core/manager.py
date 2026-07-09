import logging
import asyncio
from datetime import datetime
from typing import AsyncGenerator, Optional, Dict
from boundier.config import BoundierConfig
from boundier.chatgpt.service import ChatGPTService
from boundier.storage.sqlite_store import SQLiteStore
from boundier.core.models import Session, SessionStatus

logger = logging.getLogger("boundier.manager")

class EventDispatcher:
    def __init__(self):
        self._listeners = {}

    def register(self, event_type: str, callback):
        if event_type not in self._listeners:
            self._listeners[event_type] = []
        self._listeners[event_type].append(callback)

    async def dispatch(self, event_type: str, *args, **kwargs):
        if event_type in self._listeners:
            for callback in self._listeners[event_type]:
                try:
                    if asyncio.iscoroutinefunction(callback):
                        await callback(*args, **kwargs)
                    else:
                        callback(*args, **kwargs)
                except Exception as e:
                    logger.error(f"Error in event listener for '{event_type}': {e}", exc_info=True)

class ConversationManager:
    def __init__(self, config: BoundierConfig, service: ChatGPTService, store: SQLiteStore):
        self.config = config
        self.service = service
        self.store = store
        self.events = EventDispatcher()
        self._lock = asyncio.Lock()  # Mutex queue to serialize browser context actions
        self._active_sessions: Dict[int, Session] = {}  # thread_id -> Session
        self._active_generators: Dict[int, tuple] = {}  # temp_id -> (generator, buffered_chunks, session, chat_id)

    async def get_or_create_session(self, thread_id: int, channel_id: int, channel_name: str, rename_parent: bool = False) -> Session:
        """Loads a session from memory cache or SQLite, creating a new mapping if none exists."""
        if thread_id in self._active_sessions:
            return self._active_sessions[thread_id]
            
        thread_record = self.store.get_thread(thread_id)
        if thread_record:
            session = Session(
                thread_id=thread_id,
                chatgpt_chat_id=thread_record["chatgpt_chat_id"],
                channel_id=channel_id
            )
            session.conversation_title = thread_record["thread_title"] or ""
            session.cached_summary = thread_record["thread_summary"] or ""
            session.message_count = thread_record["message_count"] or 0
            self._active_sessions[thread_id] = session
            logger.info(f"Loaded existing session mapping from SQLite for thread {thread_id} -> {session.chatgpt_chat_id}")
            return session
            
        # Register channel if not yet existing
        channel_record = self.store.get_channel(channel_id)
        if not channel_record:
            self.store.save_channel(channel_id=channel_id, channel_name=channel_name, summary="")
            
        session = Session(
            thread_id=thread_id,
            chatgpt_chat_id="NEW",
            channel_id=channel_id
        )
        session.rename_parent = rename_parent
        self._active_sessions[thread_id] = session
        logger.info(f"Initialized new session state stub for thread {thread_id} (rename_parent={rename_parent})")
        return session

    def compile_prompt(self, session: Session, user_message: str, history_context: Optional[str] = None, author_name: Optional[str] = None) -> str:
        """Aggregates prompt context following our memory hierarchy rules."""
        system_instr = self.config.memory.system_instructions or ""
        
        channel_record = self.store.get_channel(session.channel_id)
        channel_summary = channel_record["channel_summary"] if channel_record else ""
        
        prompt_parts = []
        if system_instr:
            prompt_parts.append(f"[System Instructions]\n{system_instr}")
            
        if channel_summary:
            prompt_parts.append(f"[Channel Context & Memory Summary]\n{channel_summary}")
            
        if session.cached_summary:
            prompt_parts.append(f"[Current Conversation Summary]\n{session.cached_summary}")
            
        if history_context:
            prompt_parts.append(f"[Recent Thread History]\n{history_context}")
            
        display_author = author_name if author_name else "User"
        prompt_parts.append(f"[User Message]\n[Speaker: {display_author}]\n{user_message}")
        
        return "\n\n".join(prompt_parts)

    async def execute_prompt_stream(self, thread_id: int, channel_id: int, channel_name: str, user_message: str, file_paths: Optional[list] = None, rename_parent: bool = False, history_context: Optional[str] = None, author_name: Optional[str] = None, is_edit: bool = False) -> AsyncGenerator[str, None]:
        """Locks browser execution, navigates to target ChatGPT chat, submits prompt, and yields response stream."""
        session = await self.get_or_create_session(thread_id, channel_id, channel_name, rename_parent=rename_parent)
        compiled_prompt = self.compile_prompt(session, user_message, history_context=history_context, author_name=author_name)
        
        logger.info(f"Acquiring browser lock for thread {thread_id}...")
        async with self._lock:
            logger.info(f"Browser lock acquired for thread {thread_id}.")
            session.status = SessionStatus.PROCESSING
            session.update_activity()
            
            try:
                # Ensure authentication
                authenticated = await self.service.driver.ensure_authenticated()
                if not authenticated:
                    raise RuntimeError("ChatGPT session is unauthenticated. Actions paused.")
                
                # Navigate to appropriate chat page (or skip if already on it)
                skip_settle = False
                if session.chatgpt_chat_id == "NEW":
                    await self.service.create_new_conversation()
                    skip_settle = True
                else:
                    target_fragment = f"/c/{session.chatgpt_chat_id}"
                    if target_fragment in self.service.page.url:
                        logger.info(f"Speed optimization: Redundant page load skipped. Already on chat page: {session.chatgpt_chat_id}")
                        skip_settle = True
                    else:
                        await self.service.open_conversation(session.chatgpt_chat_id)
                    
                # Submit prompt and stream outputs
                async for chunk in self.service.send_prompt_stream(compiled_prompt, file_paths, skip_settle=skip_settle, is_edit=is_edit):
                    yield chunk
                    
                # Post-response processing
                if session.chatgpt_chat_id == "NEW":
                    chat_id = self.service.extract_chat_id()
                    if chat_id:
                        session.chatgpt_chat_id = chat_id
                        session.chatgpt_url = f"https://chatgpt.com/c/{chat_id}"
                        
                        self.store.save_thread(
                            thread_id=session.discord_thread_id,
                            channel_id=session.channel_id,
                            chatgpt_chat_id=chat_id,
                            title=session.conversation_title,
                            summary=session.cached_summary,
                            message_count=session.message_count
                        )
                        logger.info(f"New ChatGPT conversation mapped: Thread {thread_id} -> Chat ID {chat_id}")
                        await self.events.dispatch("ConversationCreated", thread_id, chat_id)
                        
                        # Trigger background thread rename request
                        asyncio.create_task(self._auto_rename_thread(session))
                    else:
                        logger.warning(f"Could not extract Chat ID for new conversation on thread {thread_id}.")
                else:
                    # Update message turns
                    self.store.save_thread(
                        thread_id=session.discord_thread_id,
                        channel_id=session.channel_id,
                        chatgpt_chat_id=session.chatgpt_chat_id,
                        title=session.conversation_title,
                        summary=session.cached_summary,
                        message_count=session.message_count
                    )
                    
                # Check turns threshold for auto-summarization
                if session.message_count >= self.config.memory.max_thread_messages:
                    asyncio.create_task(self.summarize_thread(session))
                    
            except Exception as e:
                logger.error(f"Error executing prompt stream on thread {thread_id}: {e}", exc_info=True)
                raise
            finally:
                session.status = SessionStatus.IDLE
                logger.info(f"Released browser lock for thread {thread_id}.")
                await self.events.dispatch("QueueFinished")

    async def start_new_chat_and_get_title(self, temp_id: int, channel_id: int, channel_name: str, prompt: str, file_paths: Optional[list] = None) -> tuple:
        """Starts a new ChatGPT session, submits the query, buffers initial stream using a queue, and returns the generated title."""
        session = Session(thread_id=0, chatgpt_chat_id="NEW", channel_id=channel_id)
        compiled_prompt = self.compile_prompt(session, prompt)
        
        logger.info(f"Acquiring browser lock for temp_id {temp_id} (Zero-Classification Routing)...")
        await self._lock.acquire()
        logger.info(f"Browser lock acquired for temp_id {temp_id}.")
        session.status = SessionStatus.PROCESSING
        
        try:
            authenticated = await self.service.driver.ensure_authenticated()
            if not authenticated:
                raise RuntimeError("ChatGPT session is unauthenticated.")
                
            await self.service.create_new_conversation()
            
            generator = self.service.send_prompt_stream(compiled_prompt, file_paths)
            
            # Setup background producer task with a queue
            queue = asyncio.Queue()
            async def producer():
                try:
                    async for chunk in generator:
                        await queue.put(chunk)
                except Exception as e:
                    logger.error(f"Error in prompt stream producer: {e}", exc_info=True)
                    await queue.put(e)
                finally:
                    await queue.put(None)
                    
            producer_task = asyncio.create_task(producer())
            
            # 1. Wait for URL change to capture chat_id (up to 5 seconds)
            chat_id = None
            start_url_wait = asyncio.get_event_loop().time()
            logger.info("Submitting prompt. Waiting for URL redirect to extract Chat ID...")
            while asyncio.get_event_loop().time() - start_url_wait < 5.0:
                chat_id = self.service.extract_chat_id()
                if chat_id:
                    break
                await asyncio.sleep(0.2)
                
            if not chat_id:
                logger.warning("Failed to extract Chat ID from URL after 5 seconds.")
                
            # 2. Wait for ChatGPT to generate a custom sidebar title for this chat_id (up to 8 seconds)
            title = None
            if chat_id:
                logger.info(f"Chat ID extracted: {chat_id}. Polling sidebar for custom generated title...")
                start_title_wait = asyncio.get_event_loop().time()
                while asyncio.get_event_loop().time() - start_title_wait < 8.0:
                    title = await self.service.get_sidebar_title_by_id(chat_id)
                    # Break if title has been generated and is not "new chat"
                    if title and title.lower() not in ("new chat", "newchat", "new conversation"):
                        break
                    await asyncio.sleep(0.5)
            
            if not title:
                # If still "New chat" or missing, fallback to generic title
                title = await self.service.get_sidebar_title_by_id(chat_id) if chat_id else None
                if not title or title.lower() in ("new chat", "newchat", "new conversation"):
                    title = prompt[:25].strip() or "New Chat"
                
            logger.info(f"Scraped title: '{title}', chat_id: '{chat_id}'")
            
            self._active_generators[temp_id] = (queue, producer_task, session, chat_id)
            return chat_id, title
            
        except Exception as e:
            logger.error(f"Error starting routed chat for temp_id {temp_id}: {e}", exc_info=True)
            if self._lock.locked():
                self._lock.release()
            raise

    async def consume_active_stream(self, temp_id: int, actual_thread_id: int, actual_channel_id: int, actual_channel_name: str) -> AsyncGenerator[str, None]:
        """Yields the buffered and remaining chunks of the active session from the queue, then registers thread in DB and releases lock."""
        state = self._active_generators.pop(temp_id, None)
        if not state:
            logger.error(f"No active generator found for temp_id {temp_id}")
            return
            
        queue, producer_task, session, chat_id = state
        
        session.discord_thread_id = actual_thread_id
        session.channel_id = actual_channel_id
        self._active_sessions[actual_thread_id] = session
        
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                if isinstance(item, Exception):
                    raise item
                yield item
                
            await producer_task
            
            if chat_id:
                session.chatgpt_chat_id = chat_id
                session.chatgpt_url = f"https://chatgpt.com/c/{chat_id}"
                
                final_title = await self.service.get_sidebar_title()
                if final_title:
                    session.conversation_title = final_title
                else:
                    session.conversation_title = f"Chat {chat_id[:8]}"
                    
                self.store.save_thread(
                    thread_id=actual_thread_id,
                    channel_id=actual_channel_id,
                    chatgpt_chat_id=chat_id,
                    title=session.conversation_title,
                    summary=session.cached_summary,
                    message_count=session.message_count
                )
                logger.info(f"Routed chat mapped in SQLite: Thread {actual_thread_id} -> Chat ID {chat_id}")
                await self.events.dispatch("ConversationCreated", actual_thread_id, chat_id)
            else:
                logger.warning(f"Could not extract Chat ID for routed chat on thread {actual_thread_id}.")
                
        except Exception as e:
            logger.error(f"Error consuming active stream for thread {actual_thread_id}: {e}", exc_info=True)
            raise
        finally:
            session.status = SessionStatus.IDLE
            if self._lock.locked():
                self._lock.release()
            logger.info(f"Released browser lock for thread {actual_thread_id}.")
            await self.events.dispatch("QueueFinished")

    async def _auto_rename_thread(self, session: Session):
        """Scrapes ChatGPT's auto-generated title from sidebar and dispatches ThreadRenamed event."""
        logger.info(f"Triggering auto-rename pipeline for thread {session.discord_thread_id}...")
        
        # Wait up to 10 seconds for ChatGPT to generate custom title in the sidebar
        title = None
        start_wait = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start_wait < 10.0:
            async with self._lock:
                try:
                    title = await self.service.get_sidebar_title_by_id(session.chatgpt_chat_id)
                except Exception as e:
                    logger.warning(f"Error during title scrape: {e}")
            if title and title.lower() not in ("new chat", "newchat", "new conversation"):
                break
            await asyncio.sleep(1.0)
            
        if not title or title.lower() in ("new chat", "newchat", "new conversation"):
            logger.info(f"No custom title generated yet for thread {session.discord_thread_id}, keeping temporary name.")
            return
            
        async with self._lock:
            try:
                session.conversation_title = title
                self.store.save_thread(
                    thread_id=session.discord_thread_id,
                    channel_id=session.channel_id,
                    chatgpt_chat_id=session.chatgpt_chat_id,
                    title=title,
                    summary=session.cached_summary,
                    message_count=session.message_count
                )
                logger.info(f"Scraped new title for thread {session.discord_thread_id}: '{title}'")
                await self.events.dispatch("ThreadRenamed", session.discord_thread_id, session.channel_id if session.rename_parent else 0, title)
            except Exception as e:
                logger.warning(f"Failed to auto-rename thread {session.discord_thread_id}: {e}")

    async def summarize_thread(self, session: Session):
        """Requests a thread summary from ChatGPT, caching it in SQLite."""
        logger.info(f"Triggering background summarization for thread {session.discord_thread_id}...")
        session.status = SessionStatus.SUMMARIZING
        
        summary_prompt = "Summarize the key decisions, code snippets, and topics resolved in this chat context in under 150 words."
        
        async with self._lock:
            try:
                await self.service.open_conversation(session.chatgpt_chat_id)
                summary_text = ""
                async for chunk in self.service.send_prompt_stream(summary_prompt):
                    summary_text += chunk
                    
                summary_text = summary_text.strip()
                if summary_text:
                    session.cached_summary = summary_text
                    session.message_count = 0  # Reset turns count
                    self.store.save_thread(
                        thread_id=session.discord_thread_id,
                        channel_id=session.channel_id,
                        chatgpt_chat_id=session.chatgpt_chat_id,
                        title=session.conversation_title,
                        summary=summary_text,
                        message_count=0
                    )
                    logger.info(f"Thread summary updated for thread {session.discord_thread_id}.")
            except Exception as e:
                logger.error(f"Failed to summarize thread {session.discord_thread_id}: {e}", exc_info=True)
            finally:
                session.status = SessionStatus.IDLE

    async def archive_thread(self, thread_id: int):
        """Combines thread summary into parent channel's summary block and removes session cache."""
        session = self._active_sessions.pop(thread_id, None)
        if not session:
            thread_record = self.store.get_thread(thread_id)
            if thread_record:
                session = Session(thread_id, thread_record["chatgpt_chat_id"], thread_record["channel_id"])
                session.cached_summary = thread_record["thread_summary"] or ""
                
        if session and session.cached_summary:
            logger.info(f"Merging thread {thread_id} summary into channel {session.channel_id} summary...")
            
            channel_record = self.store.get_channel(session.channel_id)
            channel_name = channel_record["channel_name"] if channel_record else f"channel-{session.channel_id}"
            old_summary = channel_record["channel_summary"] if channel_record else ""
            
            new_bullet = f"- [{datetime.now().strftime('%Y-%m-%d')}]: {session.cached_summary}"
            if old_summary:
                updated_summary = f"{old_summary}\n{new_bullet}"
            else:
                updated_summary = new_bullet
                
            # Compaction Capping: Prune old lines if word count exceeds limit
            words = updated_summary.split()
            word_limit = self.config.memory.channel_summary_limit
            if len(words) > word_limit:
                logger.info(f"Channel summary exceeds {word_limit} words limit. Pruning oldest lines...")
                lines = updated_summary.split("\n")
                while len(" ".join(lines).split()) > word_limit and len(lines) > 1:
                    lines.pop(0)
                updated_summary = "\n".join(lines)
                
            self.store.save_channel(
                channel_id=session.channel_id,
                channel_name=channel_name,
                summary=updated_summary
            )
            logger.info(f"Channel {session.channel_id} summary updated and synced.")
            await self.events.dispatch("SummaryUpdated", session.channel_id, updated_summary)
            await self.events.dispatch("ConversationArchived", thread_id)

    async def classify_prompt_channel(self, prompt: str) -> str:
        """Asks ChatGPT to classify the prompt into an appropriate channel name, or suggest a new one."""
        channels = self.store.list_channels()
        channel_names = [c["channel_name"] for c in channels if c["channel_name"]]
        
        if channel_names:
            channels_str = ", ".join(channel_names)
            classification_prompt = (
                f"You are a routing helper. Classify this user query: '{prompt}'.\n"
                f"Select the most appropriate channel from the existing list: [{channels_str}].\n"
                f"If none of them fit, suggest a new, simple lowercase topic channel name (alphanumeric, dash-separated, max 30 characters, no '#', do not use 'none').\n"
                f"Reply with exactly and only the channel name. Do not write a sentence or add formatting."
            )
        else:
            channels_str = "None registered yet"
            classification_prompt = (
                f"You are a routing helper. Classify this user query: '{prompt}'.\n"
                f"Suggest a new, simple lowercase topic channel name for this topic (alphanumeric, dash-separated, max 30 characters, no '#', do not use 'none' or 'general').\n"
                f"Reply with exactly and only the channel name. Do not write a sentence or add formatting."
            )
            
        logger.info(f"Classifying prompt for channel routing. Available channels: [{channels_str}]")
        
        async with self._lock:
            try:
                authenticated = await self.service.driver.ensure_authenticated()
                if not authenticated:
                    raise RuntimeError("ChatGPT session is unauthenticated.")
                    
                await self.service.create_new_conversation()
                
                response_text = ""
                async for chunk in self.service.send_prompt_stream(classification_prompt):
                    response_text += chunk
                    
                clean_channel = response_text.strip().lower().replace("#", "").replace('"', '').replace("'", "")
                clean_channel = "".join(c for c in clean_channel if c.isalnum() or c == "-")
                
                if not clean_channel or clean_channel == "none" or len(clean_channel) > 30:
                    clean_channel = "general"
                    
                logger.info(f"Prompt classified as target channel: '#{clean_channel}'")
                return clean_channel
            except Exception as e:
                logger.error(f"Failed to classify channel routing: {e}", exc_info=True)
                return "general"
