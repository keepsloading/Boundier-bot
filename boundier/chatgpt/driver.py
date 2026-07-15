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
        self._session_verified = False
        self._last_gist_sync_time = 0.0
        self._gist_sync_lock = asyncio.Lock()
        self._lease_lock = asyncio.Lock()

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
                "--no-zygote",
                "--mute-audio",
                "--disable-3d-apis",
                "--disable-accelerated-2d-canvas",
                "--disable-webgl",
                "--disable-audio-output",
                "--renderer-process-limit=1",
                "--disable-site-isolation-trials",
                "--disable-features=Translate,OptimizationHints,BackForwardCache,MediaRouter",
                "--js-flags=--expose-gc --max-old-space-size=384"
            ]
        )
        
        self.context.set_default_timeout(self.config.playwright.timeout_ms)
        
        # Block non-essential heavy resources to optimize CPU/Memory and speed up loads
        async def route_intercept(route):
            req = route.request
            if req.resource_type in ("font", "media"):
                await route.abort()
            elif req.resource_type == "image":
                # Only allow generated images and downloadable files to load, block external decorative UI images
                url_lower = req.url.lower()
                if "oaiusercontent" in url_lower or "/files/" in url_lower:
                    await route.continue_()
                else:
                    await route.abort()
            else:
                await route.continue_()

        await self.context.route("**/*", route_intercept)
        
        # Add init script to remove webdriver trace and spoof Win32 platform matching user_agent
        init_script = """
        delete Object.getPrototypeOf(navigator).webdriver;
        Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });
        """
        await self.context.add_init_script(init_script)
        
        # Inject storage state cookies (from Gist if enabled, otherwise fallback to environment variable)
        storage_state_str = None
        if os.environ.get("GITHUB_PAT") and os.environ.get("ENCRYPTION_KEY"):
            storage_state_str = await self._load_gist_session_state()
            
        if not storage_state_str:
            storage_state_str = os.environ.get("CHATGPT_STORAGE_STATE")
            
        if storage_state_str:
            try:
                import json
                cookies = json.loads(storage_state_str)
                if isinstance(cookies, dict) and "cookies" in cookies:
                    cookies = cookies["cookies"]
                if isinstance(cookies, list):
                    await self.context.add_cookies(cookies)
                    logger.info("Successfully injected session cookies into browser context.")
                else:
                    logger.warning("Session cookies format is not in a valid JSON list/object format.")
            except Exception as e:
                logger.error(f"Error parsing/injecting session cookies: {e}")
        
        self._leased_pages = set()
        pages = self.context.pages
        if pages:
            self.page = pages[0]
            await self._setup_page(self.page)
        else:
            self.page = await self.create_new_page()
            
        logger.info("Playwright driver initialized successfully.")

    async def _setup_page(self, page: Page):
        """Sets up console handlers, pageerror handlers, and blocks tracking/telemetry on the page."""
        page.on("console", lambda msg: logger.info(f"BROWSER CONSOLE: [{msg.type}] {msg.text}"))
        page.on("pageerror", lambda err: logger.error(f"BROWSER EXCEPTION: {err}"))
        
        # Block telemetry, ads, and tracking resources to save RAM and network overhead
        blocked_domains = [
            "sentry.io",
            "datadoghq",
            "google-analytics.com",
            "statsig",
            "mixpanel.com",
            "segment.io",
            "amplitude",
            "hotjar",
            "browser-intake",
            "doubleclick.net",
            "googleadservices.com",
            "statsig-api.net",
            "browser-intake-datadoghq.com"
        ]
        
        async def route_filter(route):
            url = route.request.url.lower()
            if any(domain in url for domain in blocked_domains):
                await route.abort()
            else:
                await route.continue_()
                
        await page.route("**/*", route_filter)

    async def create_new_page(self) -> Page:
        """Helper to create and initialize a new page tab in the browser context."""
        page = await self.context.new_page()
        await self._setup_page(page)
        return page

    async def lease_page(self) -> Page:
        """Leases a page from the pool or opens a new tab if within max_pages limit."""
        while True:
            async with self._lease_lock:
                if not hasattr(self, "_leased_pages"):
                    self._leased_pages = set()
                    
                # Clear closed pages from leased set
                active_pages = self.context.pages
                self._leased_pages = {p for p in self._leased_pages if p in active_pages}
                
                # 1. Look for a currently idle page
                for page in active_pages:
                    if page not in self._leased_pages:
                        self._leased_pages.add(page)
                        logger.info(f"Leased existing idle page/tab: {id(page)}")
                        use_count = getattr(page, "_use_count", 0) + 1
                        page._use_count = use_count
                        return page
                        
                # 2. Create a new tab if below max limit
                max_pages = getattr(self.config.playwright, "max_pages", 3)
                if len(active_pages) < max_pages:
                    new_page = await self.create_new_page()
                    self._leased_pages.add(new_page)
                    logger.info(f"Created and leased new browser page/tab: {id(new_page)} (Total tabs: {len(self.context.pages)})")
                    new_page._use_count = 1
                    return new_page
                    
            logger.info("Max browser pages reached. Waiting for an idle page/tab to become free...")
            await asyncio.sleep(0.5)

    async def release_page(self, page: Page):
        """Releases a page back to the pool, recycling it if the use limit is reached."""
        should_restart = False
        async with self._lease_lock:
            if not hasattr(self, "_leased_pages"):
                self._leased_pages = set()
                
            if page in self._leased_pages:
                self._leased_pages.remove(page)
                
            use_count = getattr(page, "_use_count", 0)
            if use_count >= 10:  # Recycle tabs every 10 uses to avoid DOM bloating
                logger.info(f"Tab {id(page)} reached use threshold ({use_count}/10). Closing...")
                try:
                    await page.close()
                except Exception as e:
                    logger.warning(f"Failed to close recycled page: {e}")
            else:
                logger.info(f"Released page/tab back to pool: {id(page)}")
                try:
                    # Force V8 garbage collection to free unused JS memory heap back to the OS
                    await page.evaluate("window.gc && window.gc()")
                    logger.info(f"Triggered forced V8 garbage collection on tab: {id(page)}")
                except Exception as e:
                    logger.warning(f"Failed to trigger V8 garbage collection: {e}")

            if not hasattr(self, "_processed_requests"):
                self._processed_requests = 0
            self._processed_requests += 1
            
            # Restart browser if requests count exceeds limit and no other pages are leased
            if self._processed_requests >= 5 and len(self._leased_pages) == 0:
                should_restart = True
                self._processed_requests = 0

        if should_restart:
            logger.info("Processed threshold requests. Restarting browser context to purge memory...")
            try:
                await self.stop()
                await self.start()
                logger.info("Browser context restarted successfully. Memory purged.")
            except Exception as restart_err:
                logger.error(f"Failed to restart browser context: {restart_err}", exc_info=True)

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

    async def solve_turnstile_if_present(self, page: Page) -> bool:
        """Detects and clicks Cloudflare Turnstile checkbox if present on the page."""
        if getattr(page, "_turnstile_solved_count", 0) >= 3:
            return False
            
        try:
            for frame in page.frames:
                if "cloudflare" in frame.url or "challenges" in frame.url:
                    logger.info("Cloudflare Turnstile challenge detected in iframe! Attempting auto-solve...")
                    checkbox = frame.locator('input[type="checkbox"], .cb-i, span.mark, label').first
                    if await checkbox.count() > 0 and await checkbox.is_visible():
                        await checkbox.click(force=True)
                        page._turnstile_solved_count = getattr(page, "_turnstile_solved_count", 0) + 1
                        logger.info(f"[SUCCESS] Clicked Cloudflare Turnstile checkbox! ({page._turnstile_solved_count}/3)")
                        return True
            return False
        except Exception as e:
            logger.warning(f"Error checking/solving Cloudflare Turnstile challenge: {e}")
            return False

    async def check_session_active(self, navigate: bool = True) -> bool:
        """Checks if an active logged-in session exists on ChatGPT, optionally navigating first."""
        if not self.page:
            raise RuntimeError("PlaywrightDriver is not running. Call start() first.")
            
        url = "https://chatgpt.com"
        
        try:
            if navigate and "chatgpt.com" not in self.page.url:
                logger.info(f"Checking session status by loading: {url}")
                await self.page.goto(url, wait_until="domcontentloaded", timeout=self.config.playwright.timeout_ms)
            
            # Check redirect status
            current_url = self.page.url
            page_title = await self.page.title()
            logger.info(f"Session check diagnostics - URL: {current_url} | Title: {page_title}")
            
            if "auth" in current_url or "login" in current_url:
                logger.warning(f"Session unverified: Redirected to landing page/login URL: {current_url}")
                return False

            has_input = False
            has_login = False
            
            if current_url != "about:blank":
                # Wait for either chat input or login button to hydrate/become visible (up to 120 seconds)
                logger.info("Waiting for page elements to hydrate (polling up to 120 seconds)...")
                import asyncio
                start_wait = asyncio.get_event_loop().time()
                while asyncio.get_event_loop().time() - start_wait < 120.0:
                    # Solve Turnstile challenge if present during hydration
                    await self.solve_turnstile_if_present(self.page)
                    
                    chat_input = self.page.locator(self.selectors.chat_input).first
                    login_btn = self.page.locator('[data-testid="login-button"]').first
                    
                    has_input = await chat_input.count() > 0
                    has_login = await login_btn.count() > 0
                    
                    if has_input or has_login:
                        break
                    await asyncio.sleep(2.0)
                logger.info(f"Page elements checking complete. has_input={has_input}, has_login={has_login}")
            
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
                await self.page.screenshot(path="logs/diagnostics/session_unverified.jpg", type="jpeg", quality=50)
                logger.info("Saved diagnostics screenshot to logs/diagnostics/session_unverified.jpg")
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

    def trigger_background_gist_sync(self):
        import time
        if time.time() - self._last_gist_sync_time > 86400:
            logger.info("Triggering background Gist session sync...")
            asyncio.create_task(self.save_gist_session_state())

    async def ensure_authenticated(self, force: bool = False) -> bool:
        """Verifies session active status. If inactive, restarts in headed mode and polls for manual login."""
        logger.info("Verifying ChatGPT session status...")
        
        # Check current status without re-navigating
        is_active = await self.check_session_active(navigate=False)
        if is_active:
            logger.info("Session is active. Proceeding.")
            self._session_verified = True
            self.trigger_background_gist_sync()
            return True
            
        # If not detected on active DOM, reload chatgpt.com once to be sure
        is_active = await self.check_session_active(navigate=True)
        if is_active:
            logger.info("Session is active after page reload. Proceeding.")
            self._session_verified = True
            self.trigger_background_gist_sync()
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
            # Save storage state on successful login
            await self.save_gist_session_state()
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

    def _get_fernet_key(self, passphrase: str) -> bytes:
        import base64
        import hashlib
        key_hash = hashlib.sha256(passphrase.encode('utf-8')).digest()
        return base64.urlsafe_b64encode(key_hash)

    async def _load_gist_session_state(self) -> Optional[str]:
        """Loads and decrypts CHATGPT_STORAGE_STATE from a private GitHub Gist using GITHUB_PAT and ENCRYPTION_KEY."""
        github_pat = os.environ.get("GITHUB_PAT")
        encryption_key = os.environ.get("ENCRYPTION_KEY")
        if not github_pat or not encryption_key:
            return None

        logger.info("Sync: GITHUB_PAT and ENCRYPTION_KEY detected. Looking for persistent session Gist...")
        try:
            import json
            import urllib.request
            import urllib.error
            from cryptography.fernet import Fernet

            # 1. Find the Gist ID
            url = "https://api.github.com/gists"
            headers = {
                "Authorization": f"token {github_pat}",
                "Accept": "application/vnd.github.v3+json",
                "User-Agent": "Boundier-Bot"
            }
            req = urllib.request.Request(url, headers=headers)
            
            loop = asyncio.get_running_loop()
            def run_get():
                try:
                    with urllib.request.urlopen(req) as res:
                        return json.loads(res.read().decode("utf-8"))
                except Exception as err:
                    logger.warning(f"Error listing Gists: {err}")
                    return []
                    
            gists = await loop.run_in_executor(None, run_get)
            
            raw_url = None
            for gist in gists:
                if "boundier_session.enc" in gist["files"]:
                    raw_url = gist["files"]["boundier_session.enc"]["raw_url"]
                    break

            if not raw_url:
                logger.info("Sync: No existing session Gist found. Will create a new one on successful login.")
                return None

            # 2. Fetch raw encrypted content
            req_raw = urllib.request.Request(raw_url, headers=headers)
            def run_get_raw():
                try:
                    with urllib.request.urlopen(req_raw) as res:
                        return res.read().decode("utf-8")
                except Exception as err:
                    logger.warning(f"Error fetching Gist file raw content: {err}")
                    return ""

            encrypted_data = await loop.run_in_executor(None, run_get_raw)
            if not encrypted_data:
                return None

            # 3. Decrypt
            fernet_key = self._get_fernet_key(encryption_key)
            fernet = Fernet(fernet_key)
            decrypted_data = fernet.decrypt(encrypted_data.encode('utf-8')).decode('utf-8')
            logger.info("Sync: Successfully loaded and decrypted persistent session from Gist.")
            return decrypted_data

        except Exception as e:
            logger.warning(f"Sync: Failed to load persistent session from Gist: {e}")
            return None

    async def save_gist_session_state(self):
        """Encrypts and pushes the current browser storage state to a private GitHub Gist."""
        async with self._gist_sync_lock:
            import time
            if time.time() - self._last_gist_sync_time < 86000: # Keep a small margin
                return
                
            github_pat = os.environ.get("GITHUB_PAT")
            encryption_key = os.environ.get("ENCRYPTION_KEY")
            if not github_pat or not encryption_key or not self.context:
                return

        try:
            import json
            import urllib.request
            from cryptography.fernet import Fernet

            logger.info("Sync: Exporting and encrypting browser storage state to persist on Gist...")
            state = await self.context.storage_state()
            state_str = json.dumps(state)

            fernet_key = self._get_fernet_key(encryption_key)
            fernet = Fernet(fernet_key)
            encrypted_data = fernet.encrypt(state_str.encode('utf-8')).decode('utf-8')

            # 1. Find existing Gist
            url_list = "https://api.github.com/gists"
            headers = {
                "Authorization": f"token {github_pat}",
                "Accept": "application/vnd.github.v3+json",
                "User-Agent": "Boundier-Bot"
            }
            
            loop = asyncio.get_running_loop()
            
            def find_and_save():
                # Find Gist ID
                req = urllib.request.Request(url_list, headers=headers)
                try:
                    with urllib.request.urlopen(req) as res:
                        gists = json.loads(res.read().decode("utf-8"))
                except Exception as err:
                    logger.warning(f"Sync: Error listing Gists during save: {err}")
                    gists = []
                    
                gist_id = None
                for gist in gists:
                    if "boundier_session.enc" in gist["files"]:
                        gist_id = gist["id"]
                        break

                if gist_id:
                    # Update existing Gist
                    url_update = f"https://api.github.com/gists/{gist_id}"
                    data = {
                        "files": {
                            "boundier_session.enc": {
                                "content": encrypted_data
                            }
                        }
                    }
                    req_update = urllib.request.Request(
                        url_update, 
                        headers=headers, 
                        method="PATCH", 
                        data=json.dumps(data).encode("utf-8")
                    )
                    with urllib.request.urlopen(req_update) as res:
                        res.read()
                    logger.info(f"Sync: Successfully updated existing session Gist: {gist_id}")
                else:
                    # Create new Gist
                    url_create = "https://api.github.com/gists"
                    data = {
                        "description": "Boundier Bot Encrypted Session Storage State",
                        "public": False,
                        "files": {
                            "boundier_session.enc": {
                                "content": encrypted_data
                            }
                        }
                    }
                    req_create = urllib.request.Request(
                        url_create, 
                        headers=headers, 
                        method="POST", 
                        data=json.dumps(data).encode("utf-8")
                    )
                    with urllib.request.urlopen(req_create) as res:
                        new_gist = json.loads(res.read().decode("utf-8"))
                    logger.info(f"Sync: Successfully created new private session Gist: {new_gist['id']}")

            await loop.run_in_executor(None, find_and_save)
            self._last_gist_sync_time = time.time()

        except Exception as e:
            logger.warning(f"Sync: Failed to save persistent session state to Gist: {e}")