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
        activity = discord.Activity(type=discord.ActivityType.watching, name="Breaking Boundaries")
        await self.change_presence(activity=activity)
        logger.info("Activity status set to: Watching 'Breaking Boundaries'")
