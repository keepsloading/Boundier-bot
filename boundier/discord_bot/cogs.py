import asyncio
import logging
import os
import io
import re
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime
from typing import Optional

logger = logging.getLogger("boundier.discord.cogs")

def parse_citations(text: str):
    """Extracts external markdown URLs [text](url), replaces them with clean references, and returns mapped URLs list.
    Also strips the raw source-attribution blob that ChatGPT web-search sometimes dumps as concatenated text at the
    very start of the response (e.g. 'SiteName Title TodayOtherSite Another Title https://...').
    """
    # --- Step 1: Strip the raw source-attribution preamble ---
    # ChatGPT web-search responses can start with a block like:
    #   "OlympicsFIFA World Cup 2026 – Spain vs Belgium...TodayOlympics+1spain-vs-belgium...utm_source=chatgpt.com)"
    # This block has no newlines, contains raw URLs and mixed title text.
    # We detect it by: starts before the first real newline AND contains a raw http URL pattern.
    lines = text.split('\n')
    preamble_end = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if re.search(r'https?://\S+', stripped) and not re.search(r'\[[^\]]+\]\(https?://[^\)]+\)', stripped):
            preamble_end = i + 1
        elif stripped == '' and preamble_end > 0:
            # First blank line after a source-blob line signals end of preamble
            break
        elif preamble_end > 0 and i > 0:
            # If we already had a source-blob line and now see normal text, stop
            break
    if preamble_end > 0:
        text = '\n'.join(lines[preamble_end:]).lstrip()

    # --- Step 2: Replace markdown links [text](url) with citation indices ---
    pattern = r'\[([^\]]+)\]\((https?://[^\)]+)\)'
    urls = []
    url_to_index = {}
    
    def replace_link(match):
        link_text = match.group(1)
        url = match.group(2)
        
        if url not in url_to_index:
            index = len(urls) + 1
            urls.append(url)
            url_to_index[url] = index
        else:
            index = url_to_index[url]
            
        if link_text.strip().isdigit() or link_text.strip().lower() in ("source", "†source", "source link"):
            return f"[{index}]"
        else:
            # Clean up trailing "+1", "+2", etc.
            clean_name = re.sub(r'\s*\+\s*\d+\s*$', '', link_text).strip()
            return f"{clean_name} [{index}]"
            
    cleaned_text = re.sub(pattern, replace_link, text)
    # Remove consecutive empty lines (replace 3 or more newlines with exactly 2 newlines)
    cleaned_text = re.sub(r'\n{3,}', '\n\n', cleaned_text)
    return cleaned_text.strip(), urls


class CitationsButton(discord.ui.Button):
    def __init__(self, urls: list):
        super().__init__(
            label="Citations",
            style=discord.ButtonStyle.success,
            emoji="🔗",
            custom_id="citations_list"
        )
        self.urls = urls
        
    async def callback(self, interaction: discord.Interaction):
        """Displays a clean list of citation URLs in an ephemeral response."""
        lines = []
        for idx, url in enumerate(self.urls, 1):
            lines.append(f"**[{idx}]** {url}")
        msg = "### Citation Links:\n" + "\n".join(lines)
        await interaction.response.send_message(content=msg, ephemeral=True)

class ResponseView(discord.ui.View):
    def __init__(self, cog=None, thread_id: Optional[int] = None, channel_id: Optional[int] = None, channel_name: Optional[str] = None, prompt: Optional[str] = None, citation_urls: list = None, author_name: Optional[str] = None, has_image: bool = False):
        super().__init__(timeout=None)  # Persistent view
        self.cog = cog
        self.thread_id = thread_id
        self.channel_id = channel_id
        self.channel_name = channel_name
        self.prompt = prompt
        self.author_name = author_name
        self.has_image = has_image
        
        # Add dynamic Citations button if URLs were extracted
        if citation_urls:
            self.add_item(CitationsButton(citation_urls))

    async def _resolve_context(self, interaction: discord.Interaction):
        """Resolves missing context variables dynamically upon button interactions (essential after bot restarts)."""
        bot = interaction.client
        if not self.cog:
            self.cog = bot.get_cog("BoundierCog")
            
        if not self.thread_id:
            self.thread_id = interaction.channel.id
            
        if not self.channel_id:
            if isinstance(interaction.channel, discord.Thread):
                self.channel_id = interaction.channel.parent_id
            else:
                self.channel_id = interaction.channel.id
                
        if not self.channel_name:
            if isinstance(interaction.channel, discord.Thread):
                self.channel_name = interaction.channel.parent.name
            else:
                self.channel_name = interaction.channel.name
                
        if not self.author_name:
            self.author_name = interaction.user.display_name
            
        if not self.prompt:
            # Dynamically fetch the last human message in this channel's history
            prompt_text = ""
            async for msg in interaction.channel.history(limit=50):
                if not msg.author.bot:
                    prompt_text = msg.content
                    self.has_image = bool(msg.attachments)
                    break
            self.prompt = prompt_text

    @discord.ui.button(label="Copy Response", style=discord.ButtonStyle.secondary, emoji="📋", custom_id="copy_response")
    async def copy_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Sends the response content inside an ephemeral raw code block to allow easy copying."""
        message = interaction.message
        if not message:
            await interaction.response.send_message("⚠️ Failed to locate response text.", ephemeral=True)
            return
            
        content = ""
        if message.embeds:
            content = message.embeds[0].description or ""
        else:
            content = message.content
            
        # Strip trailing formatting indicators
        if content.endswith(" ▌"):
            content = content[:-2]
            
        if len(content) <= 1900:
            await interaction.response.send_message(
                content=f"Here is the raw text for easy copying:\n```markdown\n{content}\n```",
                ephemeral=True
            )
        else:
            file_data = io.BytesIO(content.encode("utf-8"))
            await interaction.response.send_message(
                content="Here is the response text as a file download:",
                file=discord.File(fp=file_data, filename="response.md"),
                ephemeral=True
            )

    @discord.ui.button(label="Show Query", style=discord.ButtonStyle.secondary, emoji="❓", custom_id="show_query")
    async def query_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Displays the prompt/query used for this response in an ephemeral message."""
        await self._resolve_context(interaction)
        
        clean_prompt = self.prompt or ""
        # Strip replied context prefix if present so user sees only their actual query
        if clean_prompt.startswith("[Replied Message Context]"):
            parts = clean_prompt.split("\n\n", 1)
            if len(parts) > 1:
                clean_prompt = parts[1]
                
        # If there was an image attachment used, append indication
        attachment_indicator = ""
        if self.has_image:
            attachment_indicator = "\n\n📎 *[Image attachment included]*"
            
        await interaction.response.send_message(
            content=f"### Prompt:\n{clean_prompt}{attachment_indicator}",
            ephemeral=True
        )

    @discord.ui.button(label="Retry Prompt", style=discord.ButtonStyle.primary, emoji="🔄", custom_id="retry_response")
    async def retry_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Triggers a retry of the prompt on this thread."""
        await interaction.response.defer(ephemeral=False)
        await self._resolve_context(interaction)
        
        # Disable buttons temporarily during retry
        for item in self.children:
            item.disabled = True
        await interaction.message.edit(view=self)
        
        # Find thread channel
        thread = interaction.channel
        if not isinstance(thread, discord.Thread):
            thread = self.cog.bot.get_channel(self.thread_id)
            if not thread:
                thread = await self.cog.bot.fetch_channel(self.thread_id)
                
        # Re-trigger generation stream
        asyncio.create_task(self.cog._process_message_stream(
            thread=thread,
            channel_id=self.channel_id,
            channel_name=self.channel_name,
            user_message=self.prompt,
            file_paths=[],
            is_first_response=False,
            author_name=self.author_name,
            has_image=self.has_image
        ))


class BoundierCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.bot.manager.events.register("ThreadRenamed", self.on_manager_thread_rename)
        self._thread_forbidden_channels = set()

    async def on_manager_thread_rename(self, thread_id: int, channel_id: int, new_title: str):
        """Callback when ChatGPT generates a sidebar title. Renames Discord thread and parent channel."""
        try:
            thread = self.bot.get_channel(thread_id)
            if not thread:
                thread = await self.bot.fetch_channel(thread_id)
                
            if isinstance(thread, discord.Thread):
                logger.info(f"Renaming Discord thread {thread_id} to match ChatGPT sidebar: '{new_title}'")
                await thread.edit(name=new_title[:100])
                
            # Rename parent text channel as well (only if channel_id is not 0)
            if channel_id != 0:
                parent_channel = self.bot.get_channel(channel_id)
                if not parent_channel and channel_id:
                    parent_channel = await self.bot.fetch_channel(channel_id)
                    
                if parent_channel and isinstance(parent_channel, discord.TextChannel):
                    # Sanitize to lowercase, dash-separated channel name
                    new_channel_name = new_title.lower().strip().replace(" ", "-").replace("#", "")
                    new_channel_name = "".join(c for c in new_channel_name if c.isalnum() or c == "-")
                    if new_channel_name and new_channel_name != "none":
                        logger.info(f"Renaming Parent Channel {channel_id} to: '#{new_channel_name}'")
                        await parent_channel.edit(name=new_channel_name[:30])
                        self.bot.store.save_channel(parent_channel.id, new_channel_name[:30], "")
        except Exception as e:
            logger.warning(f"Failed to auto-rename thread/channel: {e}")

    async def _download_attachments(self, attachments) -> list:
        """Downloads a list of Discord attachments to scratch/attachments/ and returns local absolute paths."""
        local_paths = []
        if not attachments:
            return local_paths
            
        os.makedirs("scratch/attachments", exist_ok=True)
        for att in attachments:
            try:
                # Save locally
                file_path = os.path.abspath(os.path.join("scratch/attachments", att.filename))
                await att.save(file_path)
                local_paths.append(file_path)
                logger.info(f"Downloaded attachment: {file_path}")
            except Exception as e:
                logger.error(f"Failed to download attachment {att.filename}: {e}")
        return local_paths

    def _cleanup_files(self, file_paths: list):
        """Removes local temporary attachment files."""
        for path in file_paths:
            try:
                if os.path.exists(path):
                    os.remove(path)
                    logger.info(f"Cleaned up local file: {path}")
            except Exception as e:
                logger.warning(f"Failed to delete file {path}: {e}")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Listens for follow-up messages in tracked conversation threads (natural chat)."""
        if message.author.bot:
            return
            
        is_thread = isinstance(message.channel, discord.Thread)
        thread_id = message.channel.id
        
        thread_record = self.bot.store.get_thread(thread_id)
        if not thread_record:
            if is_thread:
                parent_channel_id = message.channel.parent_id
                logger.info(f"Thread mapping missing for thread '{message.channel.name}'. Attempting sidebar recovery...")
                chat_id = await self.bot.manager.find_chat_id_by_title(message.channel.name)
                if chat_id:
                    self.bot.store.save_channel(parent_channel_id, message.channel.parent.name, "")
                    self.bot.store.save_thread(
                        thread_id=thread_id,
                        channel_id=parent_channel_id,
                        chatgpt_chat_id=chat_id,
                        title=message.channel.name,
                        summary="",
                        message_count=0
                    )
                    thread_record = self.bot.store.get_thread(thread_id)
                    logger.info(f"Self-healing successful: Restored thread '{message.channel.name}' -> ChatGPT Chat ID {chat_id}")
                else:
                    logger.warning(f"Could not find matching conversation in ChatGPT sidebar for thread '{message.channel.name}'.")
                    return
            else:
                return # Not a tracked ChatGPT thread
            
        if not is_thread:
            # For direct/on-the-spot channels, only reply if pinged or if replying to bot's message
            is_pinged = self.bot.user in message.mentions
            is_reply_to_bot = False
            if message.reference and message.reference.message_id:
                try:
                    ref_msg = message.reference.resolved
                    if not ref_msg or isinstance(ref_msg, discord.DeletedReferencedMessage):
                        ref_msg = await message.channel.fetch_message(message.reference.message_id)
                    if ref_msg and ref_msg.author.id == self.bot.user.id:
                        is_reply_to_bot = True
                except Exception:
                    pass
            if not (is_pinged or is_reply_to_bot):
                return
            
        # Check user restriction (Max 5 users)
        author_id = message.author.id
        author_name = message.author.name
        if not self.bot.store.check_or_register_user(author_id, author_name):
            try:
                await message.reply("⚠️ Bot usage is restricted to a maximum of 5 registered users. The limit has been reached.")
            except Exception:
                pass
            return
            
        # Download attachments if user uploaded images/files in follow-up chat
        file_paths = []
        if message.attachments:
            file_paths = await self._download_attachments(message.attachments)
            
        # Extract referenced message context if replying to another human
        ref_context = ""
        if message.reference and message.reference.message_id:
            try:
                ref_msg = message.reference.resolved
                if not ref_msg or isinstance(ref_msg, discord.DeletedReferencedMessage):
                    ref_msg = await message.channel.fetch_message(message.reference.message_id)
                if ref_msg and ref_msg.author.id != self.bot.user.id:
                    ref_author = ref_msg.author.display_name
                    ref_content = ref_msg.embeds[0].description if ref_msg.embeds else ref_msg.content
                    if ref_content:
                        ref_context = f"[Replied Message Context]\n[Speaker: {ref_author}]\n{ref_content}\n\n"
            except Exception:
                pass
            
        # Fetch last 10 messages from the thread context (excluding the current user message)
        history_messages = []
        try:
            async for msg in message.channel.history(limit=100):
                if len(history_messages) >= 10:
                    break
                if msg.id == message.id:
                    continue
                    
                is_bot = msg.author.id == self.bot.user.id
                is_pinged = self.bot.user in msg.mentions
                is_reply = False
                if msg.reference and msg.reference.message_id:
                    try:
                        ref_msg = msg.reference.resolved
                        if not ref_msg or isinstance(ref_msg, discord.DeletedReferencedMessage):
                            ref_msg = await message.channel.fetch_message(msg.reference.message_id)
                        if ref_msg and ref_msg.author.id == self.bot.user.id:
                            is_reply = True
                    except Exception:
                        pass
                        
                if is_thread or is_bot or is_pinged or is_reply:
                    author = "Boundier" if msg.author.bot else msg.author.display_name
                    content = msg.embeds[0].description if msg.embeds else msg.content
                    if msg.author.bot and content and content.startswith("**") and "**: " in content:
                        parts = content.split("**: ", 1)
                        author = parts[0].replace("**", "").strip()
                        content = parts[1].strip()
                    if content and content.startswith("TOPIC:"):
                        parts = content.split("\n\n", 1)
                        if len(parts) > 1:
                            content = parts[1]
                    if content:
                        history_messages.append(f"[Speaker: {author}]\n{content}")
            history_messages.reverse()
            history_context = "\n".join(history_messages)
        except Exception as hist_err:
            logger.warning(f"Could not load channel history context (on_message): {hist_err}")
            history_context = None
        
        author_name = message.author.display_name
            
        # Execute follow-up stream with files and history context
        guild_name = message.channel.guild.name if message.channel.guild else "Direct Message"
        logger.info(f"Message received. Server: '{guild_name}' | Channel: '{message.channel.name}' | User: '{author_name}'")
        asyncio.create_task(self._process_message_stream(
            message.channel,
            thread_record["channel_id"],
            message.channel.parent.name,
            ref_context + message.content,
            file_paths,
            is_first_response=False,
            rename_parent=False,
            history_context=history_context,
            author_name=author_name
        ))

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        """Listens for user message updates in tracked threads, deletes the old bot response, and submits the edit to ChatGPT."""
        if after.author.bot:
            return
            
        is_thread = isinstance(after.channel, discord.Thread)
        thread_id = after.channel.id
        thread_record = self.bot.store.get_thread(thread_id)
        if not thread_record:
            if is_thread:
                parent_channel_id = after.channel.parent_id
                logger.info(f"Thread mapping missing for thread '{after.channel.name}'. Attempting sidebar recovery...")
                chat_id = await self.bot.manager.find_chat_id_by_title(after.channel.name)
                if chat_id:
                    self.bot.store.save_channel(parent_channel_id, after.channel.parent.name, "")
                    self.bot.store.save_thread(
                        thread_id=thread_id,
                        channel_id=parent_channel_id,
                        chatgpt_chat_id=chat_id,
                        title=after.channel.name,
                        summary="",
                        message_count=0
                    )
                    thread_record = self.bot.store.get_thread(thread_id)
                    logger.info(f"Self-healing successful: Restored thread '{after.channel.name}' -> ChatGPT Chat ID {chat_id}")
                else:
                    logger.warning(f"Could not find matching conversation in ChatGPT sidebar for thread '{after.channel.name}'.")
                    return
            else:
                return # Not a tracked ChatGPT thread
            
        # Check user restriction (Max 5 users)
        author_id = after.author.id
        author_name = after.author.name
        if not self.bot.store.check_or_register_user(author_id, author_name):
            try:
                await after.reply("⚠️ Bot usage is restricted to a maximum of 5 registered users. The limit has been reached.")
            except Exception:
                pass
            return
            
        # Verify that this edited message is indeed the latest user message in the thread
        last_user_msg = None
        async for msg in after.channel.history(limit=10):
            if not msg.author.bot:
                last_user_msg = msg
                break
                
        if not last_user_msg or last_user_msg.id != after.id:
            logger.info(f"Edited message {after.id} is not the latest user message in thread {thread_id}. Ignoring.")
            return
            
        logger.info(f"User edited the latest message in thread {thread_id}. Regenerating response...")
        
        # 1. Delete the bot's subsequent responses to this prompt in the Discord channel
        deleted_count = 0
        async for msg in after.channel.history(limit=20):
            if msg.id == after.id:
                break
            if msg.author.id == self.bot.user.id:
                try:
                    await msg.delete()
                    deleted_count += 1
                except Exception as e:
                    logger.warning(f"Failed to delete old bot response {msg.id}: {e}")
        logger.info(f"Deleted {deleted_count} stale bot response messages from thread.")
        
        # Fetch last 10 messages context (excluding the edited one)
        history_messages = []
        try:
            async for msg in after.channel.history(limit=100):
                if len(history_messages) >= 10:
                    break
                if msg.id == after.id:
                    continue
                    
                is_bot = msg.author.id == self.bot.user.id
                is_pinged = self.bot.user in msg.mentions
                is_reply = False
                if msg.reference and msg.reference.message_id:
                    try:
                        ref_msg = msg.reference.resolved
                        if not ref_msg or isinstance(ref_msg, discord.DeletedReferencedMessage):
                            ref_msg = await after.channel.fetch_message(msg.reference.message_id)
                        if ref_msg and ref_msg.author.id == self.bot.user.id:
                            is_reply = True
                    except Exception:
                        pass
                        
                if is_thread or is_bot or is_pinged or is_reply:
                    author = "Boundier" if msg.author.bot else msg.author.display_name
                    content = msg.embeds[0].description if msg.embeds else msg.content
                    if msg.author.bot and content and content.startswith("**") and "**: " in content:
                        parts = content.split("**: ", 1)
                        author = parts[0].replace("**", "").strip()
                        content = parts[1].strip()
                    if content and content.startswith("TOPIC:"):
                        parts = content.split("\n\n", 1)
                        if len(parts) > 1:
                            content = parts[1]
                    if content:
                        history_messages.append(f"[Speaker: {author}]\n{content}")
            history_messages.reverse()
            history_context = "\n".join(history_messages)
        except Exception as hist_err:
            logger.warning(f"Could not load channel history context (on_message_edit): {hist_err}")
            history_context = None
        
        author_name = after.author.display_name
        
        # Trigger streaming response in Discord with the is_edit=True flag!
        asyncio.create_task(self._process_message_stream(
            thread=after.channel,
            channel_id=thread_record["channel_id"],
            channel_name=after.channel.parent.name,
            user_message=after.content,
            file_paths=[],
            is_first_response=False,
            rename_parent=False,
            history_context=history_context,
            author_name=author_name,
            is_edit=True
        ))

    @app_commands.command(name="ask", description="Submit a query directly to the current conversation or start a thread locally")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def ask(self, interaction: discord.Interaction, prompt: str, attachment: Optional[discord.Attachment] = None):
        """Executes prompt on existing thread directly, or creates thread locally inside current text channel."""
        # Clean prompt from potential Discord client reply slash-command UI glitches
        prompt = prompt.strip()
        if prompt.lower().startswith("prompt:"):
            prompt = prompt[len("prompt:"):].strip()
        elif prompt.lower().startswith("prompt :"):
            prompt = prompt[len("prompt :"):].strip()
            
        await interaction.response.defer(ephemeral=False)
        
        guild = interaction.guild
        if not guild:
            await interaction.followup.send("Commands can only be used in servers.")
            return
            
        # Check user restriction (Max 5 users)
        author_id = interaction.user.id
        author_name = interaction.user.name
        if not self.bot.store.check_or_register_user(author_id, author_name):
            await interaction.followup.send("⚠️ Bot usage is restricted to a maximum of 5 registered users. The limit has been reached.")
            return
            
        current_channel = interaction.channel
        author_name = interaction.user.display_name
        
        # Download attachment if present
        file_paths = []
        if attachment:
            file_paths = await self._download_attachments([attachment])
        
        # Register channel in DB if needed
        channel_record = self.bot.store.get_channel(current_channel.id)
        if not channel_record:
            ch_name = current_channel.name
            if not isinstance(ch_name, str):
                ch_name = str(ch_name)
            # Strip invalid path characters for Windows compatibility
            ch_name = "".join(c for c in ch_name if c.isalnum() or c in ("-", "_", " "))
            ch_name = ch_name.strip()
            if not ch_name:
                ch_name = f"channel-{current_channel.id}"
            self.bot.store.save_channel(current_channel.id, ch_name, "")
            
        thread_record = self.bot.store.get_thread(current_channel.id)
        is_first = True
        history_context = None
        
        if thread_record:
            is_first = False
            is_thread = isinstance(current_channel, discord.Thread)
            # Fetch last 10 messages for history context
            history_messages = []
            try:
                async for msg in current_channel.history(limit=100):
                    if len(history_messages) >= 10:
                        break
                        
                    is_bot = msg.author.id == self.bot.user.id
                    is_pinged = self.bot.user in msg.mentions
                    is_reply = False
                    if msg.reference and msg.reference.message_id:
                        try:
                            ref_msg = msg.reference.resolved
                            if not ref_msg or isinstance(ref_msg, discord.DeletedReferencedMessage):
                                ref_msg = await current_channel.fetch_message(msg.reference.message_id)
                            if ref_msg and ref_msg.author.id == self.bot.user.id:
                                is_reply = True
                        except Exception:
                            pass
                            
                    if is_thread or is_bot or is_pinged or is_reply:
                        author = "Boundier" if msg.author.bot else msg.author.display_name
                        content = msg.embeds[0].description if msg.embeds else msg.content
                        if msg.author.bot and content and content.startswith("**") and "**: " in content:
                            parts = content.split("**: ", 1)
                            author = parts[0].replace("**", "").strip()
                            content = parts[1].strip()
                        if content and content.startswith("TOPIC:"):
                            parts = content.split("\n\n", 1)
                            if len(parts) > 1:
                                content = parts[1]
                        if content:
                            history_messages.append(f"[Speaker: {author}]\n{content}")
                history_messages.reverse()
                history_context = "\n".join(history_messages)
            except Exception as hist_err:
                logger.warning(f"Could not load channel history context (ask): {hist_err}")
                history_context = None
            
        # Spawn direct channel streaming response
        guild_name = current_channel.guild.name if current_channel.guild else "Direct Message"
        logger.info(f"Slash command /ask received. Server: '{guild_name}' | Channel: '{current_channel.name}' | User: '{author_name}'")
        asyncio.create_task(self._process_message_stream(
            current_channel,
            current_channel.id,
            current_channel.name,
            prompt,
            file_paths,
            is_first_response=is_first,
            rename_parent=False,
            history_context=history_context,
            author_name=author_name,
            interaction=interaction
        ))

    @app_commands.command(name="new", description="Starts a new conversation (creates a new channel or a thread in an existing channel)")
    @app_commands.choices(type=[
        app_commands.Choice(name="New Channel", value="new_channel"),
        app_commands.Choice(name="New Thread in Existing Channel", value="new_thread")
    ])
    async def new_chat(
        self,
        interaction: discord.Interaction,
        type: str,
        prompt: str,
        name: Optional[str] = None,
        channel: Optional[discord.TextChannel] = None,
        attachment: Optional[discord.Attachment] = None
    ):
        """Creates a new channel or starts a thread in an existing chosen channel."""
        # Clean prompt from potential Discord client reply slash-command UI glitches
        prompt = prompt.strip()
        if prompt.lower().startswith("prompt:"):
            prompt = prompt[len("prompt:"):].strip()
        elif prompt.lower().startswith("prompt :"):
            prompt = prompt[len("prompt :"):].strip()
            
        await interaction.response.defer(ephemeral=False)
        
        guild = interaction.guild
        if not guild:
            await interaction.followup.send("Commands can only be used in servers.")
            return
            
        # Check user restriction (Max 5 users)
        author_id = interaction.user.id
        author_name = interaction.user.name
        if not self.bot.store.check_or_register_user(author_id, author_name):
            await interaction.followup.send("⚠️ Bot usage is restricted to a maximum of 5 registered users. The limit has been reached.")
            return
            
        # Enforce that the bot has Administrator permissions in the server
        if not guild.me.guild_permissions.administrator:
            await interaction.followup.send("⚠️ This command requires the bot to have Administrator permissions in the server.")
            return
            
        author_name = interaction.user.display_name
        logger.info(f"Slash command /new received. Server: '{guild.name}' | User: '{author_name}'")
            
        # Download attachment if present
        file_paths = []
        if attachment:
            file_paths = await self._download_attachments([attachment])
        
        # Derive temporary title
        if name:
            temp_title = name.strip()
        else:
            words = prompt.split()[:5]
            temp_title = " ".join(words)[:50] + "..." if len(words) >= 5 else prompt[:50]
        
        rename_parent = False
        if type == "new_channel":
            rename_parent = True
            temp_channel_name = temp_title.lower().strip().replace(" ", "-").replace("#", "")
            temp_channel_name = "".join(c for c in temp_channel_name if c.isalnum() or c == "-")
            if not temp_channel_name:
                temp_channel_name = "workspace"
                
            category = None
            if self.bot.config.discord.watched_categories:
                cat_id = self.bot.config.discord.watched_categories[0]
                category = guild.get_channel(cat_id)
                if not category and isinstance(cat_id, str):
                    category = discord.utils.get(guild.categories, name=cat_id)
                    
            try:
                logger.info(f"Creating routed channel: #{temp_channel_name}")
                target_channel = await guild.create_text_channel(
                    name=temp_channel_name,
                    category=category,
                    topic=f"Workspace for {temp_channel_name} topics."
                )
                self.bot.store.save_channel(channel_id=target_channel.id, channel_name=target_channel.name, summary="")
            except Exception as e:
                logger.error(f"Failed to create new channel: {e}", exc_info=True)
                self._cleanup_files(file_paths)
                await interaction.followup.send(f"⚠️ Error creating channel: `{e}`")
                return
        else:
            # Target channel is the chosen one, or fallback to current channel
            target_channel = channel or interaction.channel
            if not isinstance(target_channel, discord.TextChannel):
                self._cleanup_files(file_paths)
                await interaction.followup.send("Please select a valid Text Channel.")
                return
                
            channel_record = self.bot.store.get_channel(target_channel.id)
            if not channel_record:
                self.bot.store.save_channel(target_channel.id, target_channel.name, "")
                
        if target_channel.id in self._thread_forbidden_channels:
            asyncio.create_task(self._process_message_stream(
                target_channel,
                target_channel.id,
                target_channel.name,
                prompt,
                file_paths,
                is_first_response=True,
                rename_parent=rename_parent,
                author_name=author_name,
                interaction=interaction,
                require_auth=True
            ))
            return
            
        try:
            thread = await target_channel.create_thread(
                name=temp_title[:100],
                auto_archive_duration=60,
                type=discord.ChannelType.public_thread
            )
            await interaction.followup.send(f"Conversation started in thread in {target_channel.mention}: {thread.mention}")
            await thread.send(content=f"**{interaction.user.display_name}**: {prompt}")
            
            asyncio.create_task(self._process_message_stream(
                thread,
                target_channel.id,
                target_channel.name,
                prompt,
                file_paths,
                is_first_response=True,
                rename_parent=rename_parent,
                author_name=author_name,
                require_auth=True
            ))
        except discord.Forbidden:
            logger.warning(f"Forbidden to create thread in channel {target_channel.id}. Falling back to direct channel response.")
            self._thread_forbidden_channels.add(target_channel.id)
            await interaction.followup.send(f"⚠️ Thread creation is not permitted in {target_channel.mention}. Responding directly in the channel...")
            asyncio.create_task(self._process_message_stream(
                target_channel,
                target_channel.id,
                target_channel.name,
                prompt,
                file_paths,
                is_first_response=True,
                rename_parent=rename_parent,
                author_name=author_name,
                interaction=interaction,
                require_auth=True
            ))
        except Exception as e:
            logger.error(f"Failed to start thread: {e}", exc_info=True)
            self._cleanup_files(file_paths)
            await interaction.followup.send(f"⚠️ Error initiating chat: `{e}`")

    @app_commands.command(name="login", description="Checks the current ChatGPT authentication status of the bot")
    async def check_login_status(self, interaction: discord.Interaction):
        """Checks if the bot is authenticated with ChatGPT."""
        await interaction.response.defer(ephemeral=True)
        try:
            is_logged_in = await self.bot.manager.service.driver.check_session_active(navigate=True)
            if is_logged_in:
                embed = discord.Embed(
                    title="🔒 ChatGPT Authentication Status",
                    description="**Status:** Connected & Logged In\n\nThe bot is successfully authenticated with ChatGPT under your account.",
                    color=0x00FF00
                )
            else:
                embed = discord.Embed(
                    title="🔓 ChatGPT Authentication Status",
                    description="**Status:** Unauthenticated / Guest Mode\n\nThe bot is not currently logged into an account. `/ask` will function as guest chat, but `/new` will be paused until authenticated.",
                    color=0xFF9900
                )
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            logger.error(f"Error checking login status: {e}", exc_info=True)
            await interaction.followup.send(f"⚠️ Error checking authentication status: {e}", ephemeral=True)

    @app_commands.command(name="archive", description="Summarizes and archives the current thread")
    async def archive(self, interaction: discord.Interaction):
        """Archives the active thread and merges its summary into the channel's memory block."""
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message("This command can only be used inside a thread.", ephemeral=True)
            return
            
        thread_id = interaction.channel.id
        thread_record = self.bot.store.get_thread(thread_id)
        if not thread_record:
            await interaction.response.send_message("This thread is not mapped to an active ChatGPT conversation.", ephemeral=True)
            return
            
        await interaction.response.defer(ephemeral=False)
        await interaction.followup.send("Archiving conversation. Running ChatGPT summarization...")
        
        try:
            await self.bot.manager.archive_thread(thread_id)
            await interaction.channel.edit(archived=True, locked=True)
            logger.info(f"Successfully archived Discord thread: {thread_id}")
        except Exception as e:
            logger.error(f"Error archiving thread {thread_id}: {e}", exc_info=True)
            await interaction.followup.send(f"Error archiving thread: {e}")

    async def _process_message_stream(
        self,
        thread: discord.Thread,
        channel_id: int,
        channel_name: str,
        user_message: str,
        file_paths: list,
        is_first_response: bool = False,
        rename_parent: bool = False,
        history_context: Optional[str] = None,
        author_name: Optional[str] = None,
        is_edit: bool = False,
        interaction: Optional[discord.Interaction] = None,
        has_image: bool = False,
        require_auth: bool = False
    ):
        """Helper to stream ChatGPT outputs directly to a Discord thread message using white embeds with rate-limiting."""
        start_time = asyncio.get_event_loop().time()
        
        reply_message = None
        # Start a background task to keep typing continuously until streaming ends
        async def keep_typing_alive():
            try:
                while True:
                    async with thread.typing():
                        await asyncio.sleep(5.0)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.warning(f"Error in continuous typing task: {e}")

        typing_task = asyncio.create_task(keep_typing_alive())
        
        try:
            # Initialize White Embed for streaming
            embed = discord.Embed(description="▌", color=0xFFFFFF)
            
            if interaction:
                reply_message = await interaction.followup.send(embed=embed, wait=True)
            else:
                reply_message = await thread.send(embed=embed)
                
            # Save stub mapping immediately to handle potential crashes during initialization
            if not self.bot.store.get_thread(thread.id):
                self.bot.store.save_thread(
                    thread_id=thread.id,
                    channel_id=channel_id,
                    chatgpt_chat_id="NEW",
                    title=thread.name if hasattr(thread, "name") else str(thread.id),
                    summary="",
                    message_count=0
                )
                
            buffer = ""
            last_update = 0.0
            
            async for chunk in self.bot.manager.execute_prompt_stream(
                thread_id=thread.id,
                channel_id=channel_id,
                channel_name=channel_name,
                user_message=user_message,
                file_paths=file_paths,
                rename_parent=rename_parent,
                history_context=history_context,
                author_name=author_name,
                is_edit=is_edit,
                require_auth=require_auth
            ):
                buffer += chunk
                now = asyncio.get_event_loop().time()
                if now - last_update > 0.8:
                    cursor = " ▌" if int(now * 2) % 2 == 0 else ""
                    if len(buffer) < 4000:
                        embed.description = buffer + cursor
                        await reply_message.edit(embed=embed)
                    else:
                        embed.description = buffer[:4000]
                        await reply_message.edit(embed=embed)
                        buffer = buffer[4000:]
                        embed = discord.Embed(description="Thinking... ▌", color=0xFFFFFF)
                        if interaction:
                            reply_message = await interaction.followup.send(embed=embed, wait=True)
                        else:
                            reply_message = await thread.send(embed=embed)
                    last_update = now
                    
            session = self.bot.manager._active_sessions.get(thread.id)
            if buffer:
                # Clean up citations and extract URLs to put under the Citations button
                cleaned_buffer, citation_urls = parse_citations(buffer)
                
                buffer_with_topic = cleaned_buffer
                has_image_flag = has_image or bool(file_paths)
                    
                if len(buffer_with_topic) < 4000:
                    embed.description = buffer_with_topic
                    # Create and attach interactive ResponseView with Copy/Retry and optional Citations
                    view = ResponseView(self, thread.id, channel_id, channel_name, user_message, citation_urls=citation_urls, author_name=author_name, has_image=has_image_flag)
                    await reply_message.edit(embed=embed, view=view)
                else:
                    embed.description = buffer_with_topic[:4000]
                    await reply_message.edit(embed=embed)
                    
                    remaining = buffer_with_topic[4000:]
                    embed_next = discord.Embed(description=remaining, color=0xFFFFFF)
                    view = ResponseView(self, thread.id, channel_id, channel_name, user_message, citation_urls=citation_urls, author_name=author_name, has_image=has_image_flag)
                    if interaction:
                        await interaction.followup.send(embed=embed_next, view=view)
                    else:
                        await thread.send(embed=embed_next, view=view)
            else:
                if session and session.generated_assets:
                    embed.description = "🎨 Generated asset(s) attached below."
                    view = ResponseView(self, thread.id, channel_id, channel_name, user_message, citation_urls=[], author_name=author_name, has_image=has_image or bool(file_paths))
                    await reply_message.edit(embed=embed, view=view)
                else:
                    embed.description = "[Empty Response]"
                    await reply_message.edit(embed=embed)
                
            elapsed = asyncio.get_event_loop().time() - start_time
            guild_name = thread.guild.name if thread.guild else "Direct Message"
            logger.info(f"Response complete. Server: '{guild_name}' | Channel/Thread: '{thread.name}' (#{channel_name}) | Time: {elapsed:.2f}s")
            if session and (thread.name.endswith("...") or session.conversation_title.lower() in ("new chat", "newchat", "new conversation")):
                logger.info(f"Thread '{thread.name}' needs renaming. Triggering background rename update...")
                asyncio.create_task(self.bot.manager._auto_rename_thread(session))

            # Deliver any GPT Image 2 generated images or downloadable files to Discord
            if session and session.generated_assets:
                asset_paths_to_cleanup = []
                for asset in session.generated_assets:
                    try:
                        asset_path = asset.get("path", "")
                        asset_filename = asset.get("filename", "file")
                        asset_type = asset.get("type", "file")
                        asset_paths_to_cleanup.append(asset_path)

                        if not asset_path or not os.path.exists(asset_path):
                            logger.warning(f"Asset file not found, skipping: {asset_path}")
                            continue

                        # Check Discord 25MB limit
                        file_size_mb = os.path.getsize(asset_path) / (1024 * 1024)
                        if file_size_mb > 25:
                            await thread.send(content=f"⚠️ Generated asset **{asset_filename}** is too large to upload ({file_size_mb:.1f} MB > 25 MB Discord limit).")
                            continue

                        label = "🖼️ Generated Image" if asset_type == "image" else "📄 Generated File"
                        discord_file = discord.File(asset_path, filename=asset_filename)
                        await thread.send(content=label, file=discord_file)
                        logger.info(f"Delivered generated asset to Discord: '{asset_filename}' in thread {thread.id}")

                    except Exception as asset_send_err:
                        logger.warning(f"Failed to send generated asset '{asset.get('filename', '?')}' to Discord: {asset_send_err}")

                # Clean up temp files and reset
                self._cleanup_files(asset_paths_to_cleanup)
                session.generated_assets = []
                
        except Exception as e:
            logger.error(f"Failed to stream response to thread {thread.id}: {e}", exc_info=True)
            if reply_message:
                try:
                    embed.description = f"⚠️ Error while processing response: `{e}`"
                    await reply_message.edit(embed=embed)
                except Exception as edit_err:
                    logger.warning(f"Failed to edit error message: {edit_err}")
            elif interaction:
                try:
                    await interaction.followup.send(content=f"⚠️ Error while processing response: `{e}`")
                except Exception as send_err:
                    logger.warning(f"Failed to send error response: {send_err}")
        finally:
            if 'typing_task' in locals() and typing_task:
                typing_task.cancel()
            self._cleanup_files(file_paths)
