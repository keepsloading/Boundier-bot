import os
import logging
import asyncio
from typing import Optional
from playwright.async_api import async_playwright, Playwright, BrowserContext, Page
from boundier.config import BoundierConfig
from boundier.gemini.selectors import GeminiSelectors, load_selectors

logger = logging.getLogger("boundier.driver")

class PlaywrightDriver:
    def __init__(self, config: BoundierConfig):
        self.config = config
        self.selectors: GeminiSelectors = load_selectors()
        self.playwright: Optional[Playwright] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None

    async def start(self):
        """Initializes Playwright, loads/launches Chromium context with persistent configuration."""
        logger.info("Starting Playwright driver...")
        self.playwright = await async_playwright().start()
        
        # Resolve absolute path for Chromium user profile storage
        user_data_dir = os.path.abspath(self.config.playwright.user_data_dir)
        os.makedirs(user_data_dir, exist_ok=True)
        
        # Grab viewport dimensions
        viewport_dims = {
            "width": self.config.playwright.viewport.width,
            "height": self.config.playwright.viewport.height
        }
        
        logger.info(f"Launching Chromium context. Profile dir: '{user_data_dir}', Headless: {self.config.playwright.headless}")
        self.context = await self.playwright.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=self.config.playwright.headless,
            viewport=viewport_dims,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-gpu",
                "--disable-dev-shm-usage",
                "--disable-extensions",
                "--disable-background-networking",
                "--disable-background-timer-throttling",
                "--disable-renderer-backgrounding",
                "--disable-software-rasterizer",
                "--no-first-run",
                "--js-flags=--max-old-space-size=128"
            ]
        )
        
        # Configure global timeouts
        self.context.set_default_timeout(self.config.playwright.timeout_ms)
        
        # Setup page handle
        pages = self.context.pages
        if pages:
            self.page = pages[0]
        else:
            self.page = await self.context.new_page()
            
        logger.info("Playwright driver initialized successfully.")

    async def stop(self):
        """Closes browser context and shuts down Playwright instance."""
        logger.info("Stopping Playwright driver...")
        if self.context:
            await self.context.close()
            self.context = None
        if self.playwright:
            await self.playwright.stop()
            self.playwright = None
        logger.info("Playwright driver stopped successfully.")

    async def check_session_active(self, navigate: bool = True) -> bool:
        """Checks if an active logged-in session exists, optionally navigating first."""
        if not self.page:
            raise RuntimeError("PlaywrightDriver is not running. Call start() first.")
            
        url = "https://gemini.google.com"
        
        try:
            if navigate:
                logger.info(f"Checking session status by loading: {url}")
                await self.page.goto(url, wait_until="domcontentloaded", timeout=self.config.playwright.timeout_ms)
                await asyncio.sleep(3.0)
            
            # Check redirect status
            current_url = self.page.url
            if "auth" in current_url or "login" in current_url:
                logger.warning(f"Session unverified: Redirected to landing page/login URL: {current_url}")
                return False
                
            # Direct check: chat input exists, and no login button is present
            chat_input = self.page.locator(self.selectors.chat_input).first
            login_btn = self.page.locator('[data-testid="login-button"]').first
            
            has_input = await chat_input.count() > 0
            has_login = await login_btn.count() > 0
            
            if has_input and not has_login:
                logger.info("Session verified: Chat input found and login button is absent (authenticated).")
                return True
                
            logger.warning(f"Session unverified: chat_input_exists={has_input}, login_button_exists={has_login}")
            return False
                
        except Exception as e:
            logger.error(f"Error checking session status: {e}", exc_info=True)
            return False

    async def wait_for_manual_login(self, timeout_seconds: int = 300) -> bool:
        """Enters a polling loop waiting for the operator to log in manually via browser GUI."""
        if self.config.playwright.headless:
            logger.error("Cannot perform manual login loop in headless mode! Set headless=false in config.yaml")
            return False
            
        logger.warning(f"Awaiting manual login. You have {timeout_seconds} seconds to log in using the browser window...")
        
        elapsed = 0
        poll_interval = 2
        while elapsed < timeout_seconds:
            if await self.check_session_active(navigate=False):
                logger.info("Manual login verified! Resuming execution.")
                return True
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
            
        logger.error("Manual login wait period timed out.")
        return False

    async def ensure_authenticated(self) -> bool:
        """Verifies session active status. If inactive, polls for manual login."""
        logger.info("Verifying Gemini session status...")
        
        is_active = await self.check_session_active(navigate=False)
        if is_active:
            logger.info("Session is active (cached). Proceeding.")
            return True
            
        is_active = await self.check_session_active(navigate=True)
        if is_active:
            logger.info("Session is active after page reload. Proceeding.")
            return True
            
        logger.warning("Gemini session has expired or is invalid. Relaunching in HEADED mode for manual authentication...")
        
        was_headless = self.config.playwright.headless
        if was_headless:
            logger.info("Temporarily switching headless configuration to False for authentication...")
            self.config.playwright.headless = False
            await self.stop()
            await self.start()
            
        await self.page.goto("https://gemini.google.com", wait_until="domcontentloaded")
        
        print("\n" + "="*80)
        print("AUTHENTICATION REQUIRED:")
        print("Gemini requires login. A Chromium window has been opened.")
        print("Please log in manually using Google, email, or your preferred method.")
        print("The bot will wait and automatically detect when you have successfully logged in.")
        print("="*80 + "\n")
        
        authenticated = await self.wait_for_manual_login(timeout_seconds=300)
        
        if authenticated:
            logger.info("Authentication successful!")
            if was_headless:
                logger.info("Re-applying headless mode config and restarting browser driver...")
                self.config.playwright.headless = was_headless
                await self.stop()
                await self.start()
            return True
        else:
            logger.error("Authentication failed or timed out.")
            return False