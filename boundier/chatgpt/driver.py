import os
import logging
import asyncio
from typing import Optional
from playwright.async_api import async_playwright, Playwright, BrowserContext, Page
from boundier.config import BoundierConfig
from boundier.chatgpt.selectors import ChatGPTSelectors, load_selectors

logger = logging.getLogger("boundier.driver")

class PlaywrightDriver:
    def __init__(self, config: BoundierConfig):
        self.config = config
        self.selectors: ChatGPTSelectors = load_selectors()
        self.playwright: Optional[Playwright] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None

    async def start(self):
        """Initializes Playwright, loads/launches Chromium context with persistent configuration."""
        logger.info("Starting Playwright driver for ChatGPT...")
        self.playwright = await async_playwright().start()
        
        user_data_dir = os.path.abspath(self.config.playwright.user_data_dir)
        os.makedirs(user_data_dir, exist_ok=True)
        
        viewport_dims = {
            "width": self.config.playwright.viewport.width,
            "height": self.config.playwright.viewport.height
        }
        
        user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        locale = "en-US"
        extra_headers = {
            "accept-language": "en-US,en;q=0.9"
        }
        
        logger.info(f"Launching Chromium context. Profile dir: '{user_data_dir}', Headless: {self.config.playwright.headless}")
        self.context = await self.playwright.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=self.config.playwright.headless,
            viewport=viewport_dims,
            user_agent=user_agent,
            locale=locale,
            extra_http_headers=extra_headers,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--start-minimized",
                "--window-position=100,100",
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
        
        self.context.set_default_timeout(self.config.playwright.timeout_ms)
        # Add init script to remove webdriver trace
        await self.context.add_init_script("delete Object.getPrototypeOf(navigator).webdriver;")
        
        # Inject storage state cookies if provided via environment variable
        storage_state_str = os.environ.get("CHATGPT_STORAGE_STATE")
        if storage_state_str:
            try:
                import json
                cookies = json.loads(storage_state_str)
                if isinstance(cookies, dict) and "cookies" in cookies:
                    cookies = cookies["cookies"]
                if isinstance(cookies, list):
                    await self.context.add_cookies(cookies)
                    logger.info("Successfully injected session cookies from CHATGPT_STORAGE_STATE environment variable.")
                else:
                    logger.warning("CHATGPT_STORAGE_STATE environment variable is not in a valid JSON list/object format.")
            except Exception as e:
                logger.error(f"Error parsing/injecting CHATGPT_STORAGE_STATE: {e}")
        
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
        """Checks if an active logged-in session exists on ChatGPT, optionally navigating first."""
        if not self.page:
            raise RuntimeError("PlaywrightDriver is not running. Call start() first.")
            
        url = "https://chatgpt.com"
        
        try:
            if navigate:
                logger.info(f"Checking session status by loading: {url}")
                await self.page.goto(url, wait_until="domcontentloaded", timeout=self.config.playwright.timeout_ms)
            
            # Check redirect status
            current_url = self.page.url
            page_title = await self.page.title()
            logger.info(f"Session check diagnostics - URL: {current_url} | Title: {page_title}")
            
            if "auth" in current_url or "login" in current_url:
                logger.warning(f"Session unverified: Redirected to landing page/login URL: {current_url}")
                return False

            if current_url != "about:blank":
                # Wait for either chat input or login button to be attached (indicates React hydration is complete)
                combined_selector = f'{self.selectors.chat_input}, [data-testid="login-button"]'
                try:
                    logger.info("Waiting for page elements to hydrate...")
                    await self.page.locator(combined_selector).first.wait_for(state="attached", timeout=15000)
                    logger.info("Page elements hydrated successfully.")
                except Exception as wait_err:
                    logger.warning(f"Timeout waiting for elements to hydrate: {wait_err}")
                
            # Direct check: active input exists, and no login button is present
            chat_input = self.page.locator(self.selectors.chat_input).first
            login_btn = self.page.locator('[data-testid="login-button"]').first
            
            has_input = await chat_input.count() > 0
            has_login = await login_btn.count() > 0
            
            # Log post-hydration diagnostics
            current_url = self.page.url
            page_title = await self.page.title()
            logger.info(f"Post-hydration diagnostics - URL: {current_url} | Title: {page_title}")
            try:
                text_content = await self.page.locator("body").text_content()
                clean_text = text_content.strip()[:400].replace('\n', ' ')
                logger.info(f"Page text content snippet: {clean_text}")
            except Exception:
                pass
            
            if has_input and not has_login:
                logger.info("Session verified: Chat input found and login button is absent (authenticated).")
                return True
                
            logger.warning(f"Session unverified: chat_input_exists={has_input}, login_button_exists={has_login}")
            try:
                os.makedirs("logs/diagnostics", exist_ok=True)
                await self.page.screenshot(path="logs/diagnostics/session_unverified.png")
                logger.info("Saved diagnostics screenshot to logs/diagnostics/session_unverified.png")
            except Exception as ss_err:
                logger.warning(f"Failed to save unverified session screenshot: {ss_err}")
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
            # Crucial: Do NOT navigate/reload during polling to avoid interrupting the user's login typing!
            if await self.check_session_active(navigate=False):
                logger.info("Manual login verified! Resuming execution.")
                return True
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
            
        logger.error("Manual login wait period timed out.")
        return False

    async def ensure_authenticated(self) -> bool:
        """Verifies session active status. If inactive, restarts in headed mode and polls for manual login."""
        logger.info("Verifying ChatGPT session status...")
        
        # Check current status without re-navigating
        is_active = await self.check_session_active(navigate=False)
        if is_active:
            logger.info("Session is active (cached). Proceeding.")
            return True
            
        # If not detected on active DOM, reload chatgpt.com once to be sure
        is_active = await self.check_session_active(navigate=True)
        if is_active:
            logger.info("Session is active after page reload. Proceeding.")
            return True
            
        # Check if headless mode is forced/mandatory (e.g. running on Linux without DISPLAY)
        import sys
        if sys.platform != "win32" and "DISPLAY" not in os.environ:
            logger.error("ChatGPT session is unauthenticated. Headless Linux environment detected: Cannot launch headed browser for manual authentication.")
            logger.error("Please run the session exporter script locally: 'python -m tests.export_session'")
            logger.error("Then add the output as the 'CHATGPT_STORAGE_STATE' environment variable in your Render dashboard to authenticate.")
            return False

        logger.warning("ChatGPT session has expired or is invalid. Relaunching in HEADED mode for manual authentication...")
        
        # If running headless, we must stop and restart in headed mode
        was_headless = self.config.playwright.headless
        if was_headless:
            logger.info("Temporarily switching headless configuration to False for authentication...")
            self.config.playwright.headless = False
            await self.stop()
            await self.start()
            
        # Navigate to login page
        await self.page.goto("https://chatgpt.com", wait_until="domcontentloaded")
        
        print("\n" + "="*80)
        print("AUTHENTICATION REQUIRED:")
        print("ChatGPT requires login. A Chromium window has been opened.")
        print("Please log in manually using Google, email, or your preferred method.")
        print("The bot will wait and automatically detect when you have successfully logged in.")
        print("="*80 + "\n")
        
        # Poll for active login
        # Wait up to 300 seconds (5 minutes)
        authenticated = await self.wait_for_manual_login(timeout_seconds=300)
        
        if authenticated:
            logger.info("Authentication successful!")
            # If we temporarily switched to headed mode, restart in the user's configured mode
            if was_headless:
                logger.info("Re-applying headless mode config and restarting browser driver...")
                self.config.playwright.headless = was_headless
                await self.stop()
                await self.start()
            return True
        else:
            logger.error("Authentication failed or timed out.")
            return False