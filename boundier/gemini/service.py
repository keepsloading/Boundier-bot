import logging
import asyncio
from typing import AsyncGenerator, Optional
from boundier.gemini.driver import PlaywrightDriver
from boundier.gemini.selectors import GeminiSelectors

logger = logging.getLogger("boundier.gemini_service")

class GeminiService:
    def __init__(self, driver: PlaywrightDriver):
        self.driver = driver

    @property
    def selectors(self) -> GeminiSelectors:
        return self.driver.selectors

    @property
    def page(self):
        if not self.driver.page:
            raise RuntimeError("Browser page not initialized. Start the driver first.")
        return self.driver.page

    async def open_conversation(self, chat_id: str) -> bool:
        """Navigates to an existing Gemini conversation by its unique URL ID suffix."""
        url = f"https://gemini.google.com/app/c/{chat_id}"
        logger.info(f"Opening existing conversation: {url}")
        
        try:
            await self.page.goto(url, wait_until="domcontentloaded", timeout=self.driver.config.playwright.timeout_ms)
            # Wait for the input box to verify loading was successful
            await self.page.wait_for_selector(self.selectors.chat_input, timeout=self.driver.config.playwright.timeout_ms)
            logger.info(f"Successfully opened conversation: {chat_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to open conversation {chat_id}: {e}", exc_info=True)
            return False

    async def create_new_conversation(self) -> bool:
        """Navigates to the default Gemini portal to begin a new chat session."""
        url = "https://gemini.google.com/app"
        logger.info("Initializing new conversation...")
        
        try:
            # Check if we are already on the new chat page and chat input is active
            if self.page.url == url or self.page.url.startswith(url):
                input_locator = self.page.locator(self.selectors.chat_input)
                if await input_locator.count() > 0 and await input_locator.first.is_visible():
                    logger.info("Already on a new conversation screen and input is visible.")
                    return True
                    
                # If on /app but input is not visible or disabled, try clicking new chat button
                new_chat_btn = self.page.locator(self.selectors.new_chat_button)
                if await new_chat_btn.count() > 0 and await new_chat_btn.is_visible() and not await new_chat_btn.is_disabled():
                    logger.info("Clicking new chat button in sidebar...")
                    await new_chat_btn.click()
                    await asyncio.sleep(1)
                    return True
            
            await self.page.goto(url, wait_until="domcontentloaded", timeout=self.driver.config.playwright.timeout_ms)
            await self.page.wait_for_selector(self.selectors.chat_input, timeout=self.driver.config.playwright.timeout_ms)
            logger.info("New conversation screen loaded.")
            return True
        except Exception as e:
            logger.error(f"Failed to create new conversation: {e}", exc_info=True)
            return False

    def extract_chat_id(self) -> Optional[str]:
        """Parses the current browser URL to extract the unique Gemini chat ID."""
        url = self.page.url
        # Expected format: https://gemini.google.com/app/c/unique_id_here
        if "/app/c/" in url:
            parts = url.split("/app/c/")
            if len(parts) > 1:
                return parts[1].split("?")[0].split("/")[0]
        return None

    async def send_prompt_stream(self, prompt: str) -> AsyncGenerator[str, None]:
        """Submits a prompt to the active page and yields the generated output tokens as they stream."""
        logger.info("Focusing and filling chat input...")
        
        # Click the chat input area
        input_locator = self.page.locator(self.selectors.chat_input).first
        await input_locator.click()
        
        # Clear existing text and fill
        await input_locator.fill(prompt)
        await asyncio.sleep(0.5)
        
        # Count current messages to identify where the new reply starts
        existing_count = await self.page.locator(self.selectors.response_containers).count()
        logger.info(f"Existing response container count: {existing_count}")
        
        # Click the submit button
        submit_btn = self.page.locator(self.selectors.submit_button).first
        await submit_btn.click()
        logger.info("Prompt submitted, waiting for new response container...")
        
        # Wait for a new response bubble to appear
        timeout = 15.0  # seconds
        start_time = asyncio.get_event_loop().time()
        while True:
            current_count = await self.page.locator(self.selectors.response_containers).count()
            if current_count > existing_count:
                break
            if asyncio.get_event_loop().time() - start_time > timeout:
                raise TimeoutError("Timeout waiting for response generation to start.")
            await asyncio.sleep(0.2)
            
        # Target the last response bubble
        response_locator = self.page.locator(self.selectors.response_containers).last
        
        logger.info("Scraping response stream...")
        last_text = ""
        unchanged_polls = 0
        max_unchanged_polls = 10  # 10 * 300ms = 3.0s unchanged -> done
        
        while True:
            # Check active loader status
            is_generating = await self.page.locator(self.selectors.streaming_indicators).count() > 0
            
            # Extract content text
            current_text = await response_locator.text_content()
            current_text = current_text.strip() if current_text else ""
            
            if current_text != last_text:
                # Yield incremental new content
                delta = current_text[len(last_text):]
                if delta:
                    yield delta
                last_text = current_text
                unchanged_polls = 0
            else:
                unchanged_polls += 1
                
            # Stop generator if loading indicator is gone and text is steady (and not empty)
            if not is_generating and unchanged_polls >= max_unchanged_polls:
                if current_text != "":
                    break
                
            # Fallback timeout to prevent infinite loops on stalled pages
            if unchanged_polls >= 50:
                logger.warning("Generation stream stalled for 15 seconds. Terminating reader.")
                break
                
            await asyncio.sleep(0.3)

    async def get_sidebar_title(self) -> Optional[str]:
        """Attempts to scrape the top sidebar title from the history list."""
        try:
            sidebar_items = self.page.locator(self.selectors.sidebar_history_items)
            if await sidebar_items.count() > 0:
                # Take the first sidebar history link text
                first_title = await sidebar_items.first.text_content()
                if first_title:
                    return first_title.strip()
        except Exception as e:
            logger.warning(f"Failed to scrape sidebar title: {e}")
        return None
