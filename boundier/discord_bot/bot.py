import logging
import discord
from discord.ext import commands
from boundier.config import BoundierConfig
from boundier.core.manager import ConversationManager
from boundier.storage.sqlite_store import SQLiteStore

logger = logging.getLogger("boundier.discord")

class BoundierBot(commands.Bot):
    def __init__(self, config: BoundierConfig, manager: ConversationManager, store: SQLiteStore):
        # Setup intents
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        intents.messages = True
        
        super().__init__(
            command_prefix=config.discord.command_prefix or "/",
            intents=intents,
            member_cache_flags=discord.MemberCacheFlags.none(),
            max_messages=10
        )
        self.config = config
        self.manager = manager
        self.store = store

    async def setup_hook(self):
        """Loads bot cogs and syncs slash commands tree."""
        logger.info("Setting up Bot extensions...")
        from boundier.discord_bot.cogs import BoundierCog, ResponseView
        await self.add_cog(BoundierCog(self))
        self.add_view(ResponseView())
        
        logger.info("Syncing application commands tree...")
        await self.tree.sync()
        logger.info("Slash commands synced successfully.")

    async def on_ready(self):
        logger.info(f"Bot connected: Logged in as {self.user.name} ({self.user.id})")
        activity = discord.Game(name="Boundier")
        await self.change_presence(activity=activity)
        logger.info("Activity status set to: Playing 'Boundier'")
        
        try:
            await self.cleanup_stuck_responses()
        except Exception as e:
            logger.warning(f"Error during startup response cleanup: {e}", exc_info=True)

    async def cleanup_stuck_responses(self):
        """Scans active threads for any responses left stuck as a cursor from a prior crash or restart."""
        logger.info("Scanning database active threads for stuck cursor responses...")
        active_threads = self.store.list_active_threads()
        cleaned_count = 0
        
        for thread_data in active_threads:
            thread_id = thread_data.get("thread_id")
            if not thread_id:
                continue
                
            try:
                channel = self.get_channel(thread_id)
                if not channel:
                    channel = await self.fetch_channel(thread_id)
                    
                async for message in channel.history(limit=5):
                    if message.author.id == self.user.id:
                        if message.embeds:
                            embed = message.embeds[0]
                            desc = embed.description or ""
                            if desc == "▌" or desc.endswith(" ▌"):
                                logger.info(f"Editing stuck response in thread {thread_id}, message {message.id}...")
                                embed.description = "⚠️ *Generation interrupted due to bot restart. Please ask your question again.*"
                                embed.color = 0xff0000  # Red color for error/alert
                                await message.edit(embed=embed, view=None)
                                cleaned_count += 1
            except Exception as thread_err:
                logger.warning(f"Failed to scan/cleanup thread {thread_id}: {thread_err}")
                
        logger.info(f"Startup scan completed. Cleaned up {cleaned_count} stuck messages.")
