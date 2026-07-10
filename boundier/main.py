import sys
import asyncio
import logging
from boundier.config import load_config
from boundier.logger import setup_logging
from boundier.chatgpt.driver import PlaywrightDriver
from boundier.chatgpt.service import ChatGPTService
from boundier.storage.sqlite_store import SQLiteStore
from boundier.core.manager import ConversationManager
import os
from boundier.discord_bot.bot import BoundierBot

async def health_check_server():
    port = int(os.environ.get("PORT", "10000"))
    logger = logging.getLogger("boundier.health")
    logger.info(f"Starting dummy health check server on port {port}...")
    
    async def handle_client(reader, writer):
        try:
            line = await reader.readline()
            if not line:
                return
            parts = line.decode("utf-8").split()
            if len(parts) >= 2:
                path = parts[1]
                if path.startswith("/diagnostics/") and ".." not in path:
                    filename = path.replace("/diagnostics/", "")
                    file_path = os.path.join("logs", "diagnostics", filename)
                    if os.path.isfile(file_path):
                        with open(file_path, "rb") as f:
                            content = f.read()
                        response = (
                            f"HTTP/1.1 200 OK\r\n"
                            f"Content-Type: image/png\r\n"
                            f"Content-Length: {len(content)}\r\n"
                            f"Connection: close\r\n\r\n"
                        ).encode("utf-8") + content
                        writer.write(response)
                        await writer.drain()
                        return
            
            response = (
                "HTTP/1.1 200 OK\r\n"
                "Content-Type: text/plain\r\n"
                "Content-Length: 2\r\n"
                "Connection: close\r\n\r\n"
                "OK"
            )
            writer.write(response.encode("utf-8"))
            await writer.drain()
        except Exception as err:
            logger.error(f"Error handling health check client: {err}")
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
                
    try:
        server = await asyncio.start_server(handle_client, "0.0.0.0", port)
        async with server:
            await server.serve_forever()
    except Exception as e:
        logger.error(f"Failed to start health check server: {e}")

async def async_main():
    logger = setup_logging()
    logger.info("Initializing Boundier bot bootstrap...")
    
    # Start background health check server immediately for Render port binding requirements
    asyncio.create_task(health_check_server())
    
    try:
        config = load_config("config.yaml")
        
        # 1. Sync markdown files and initialize SQLite store
        store = SQLiteStore(db_path="boundier.db", schema_path="schema.sql", memory_dir="memory")
        store.sync_markdown_files()
        
        # 2. Start Playwright browser driver
        driver = PlaywrightDriver(config)
        await driver.start()
        
        # Ensure session is authenticated on startup
        authenticated = await driver.ensure_authenticated()
        if not authenticated:
            logger.error("Session is unauthenticated. Startup aborted.")
            logger.error("Please run: python -m tests.verify_milestone2 in your terminal first to authorize ChatGPT.")
            await driver.stop()
            sys.exit(1)
            
        service = ChatGPTService(driver)
        manager = ConversationManager(config, service, store)
        
        # 3. Initialize Discord Bot
        bot = BoundierBot(config, manager, store)
        
        # Setup clean exit handlers
        async def cleanup():
            logger.info("Shutting down services...")
            try:
                await bot.close()
            except Exception:
                pass
            await driver.stop()
            logger.info("Shutdown complete.")
            
        # Run Bot
        try:
            logger.info("Starting Boundier Discord Bot...")
            await bot.start(config.discord.token)
        except (KeyboardInterrupt, asyncio.CancelledError):
            logger.info("Bot received termination signal.")
        finally:
            await cleanup()
            
    except Exception as e:
        logger.error(f"Fatal error during bootstrap: {e}", exc_info=True)
        sys.exit(1)

def main():
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
