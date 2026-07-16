import logging
import asyncio
import os
from datetime import datetime
from typing import AsyncGenerator, Optional
from boundier.chatgpt.driver import PlaywrightDriver
from boundier.chatgpt.selectors import ChatGPTSelectors

logger = logging.getLogger("boundier.chatgpt_service")

class ChatGPTService:
    def __init__(self, driver: PlaywrightDriver):
        self.driver = driver
        self._page_override = None

    @property
    def selectors(self) -> ChatGPTSelectors:
        return self.driver.selectors

    @property
    def page(self):
        if self._page_override:
            return self._page_override
        if not self.driver.page:
            raise RuntimeError("Browser page not initialized. Start the driver first.")
        return self.driver.page

    @page.setter
    def page(self, value):
        self._page_override = value

    async def save_diagnostics_screenshot(self, context_name: str = "error"):
        """Captures page viewport screenshot and saves it to logs/diagnostics/ folder for debugging."""
        try:
            os.makedirs("logs/diagnostics", exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"logs/diagnostics/{context_name}_{timestamp}.jpg"
            await self.page.screenshot(path=filename, full_page=False, type="jpeg", quality=50)
            logger.info(f"Diagnostics screenshot captured and saved as low-memory JPEG to: {filename}")
        except Exception as e:
            logger.warning(f"Failed to capture diagnostics screenshot: {e}")

    async def open_conversation(self, chat_id: str) -> bool:
        """Navigates to an existing ChatGPT conversation by its unique URL UUID suffix."""
        url = f"https://chatgpt.com/c/{chat_id}"
        if f"/c/{chat_id}" in self.page.url:
            logger.info(f"Already on conversation page: {chat_id}. Skipping navigation.")
            return True
            
        logger.info(f"Opening existing ChatGPT conversation: {url}")
        
        # Optimization: Attempt client-side React router navigation by clicking the sidebar link if present
        try:
            sidebar_link = self.page.locator(f'a[href*="/c/{chat_id}"]')
            if await sidebar_link.count() > 0 and await sidebar_link.first.is_visible():
                logger.info(f"Sidebar link found. Clicking to trigger SPA transition to chat: {chat_id}")
                await sidebar_link.first.click(force=True)
                await self.page.locator(self.selectors.chat_input).first.wait_for(state="attached", timeout=10000)
                # Wait for history elements to load
                try:
                    await self.page.locator('[data-message-author-role], [data-turn], div.markdown').first.wait_for(state="attached", timeout=5000)
                except Exception:
                    pass
                logger.info(f"Successfully transitioned to conversation via sidebar: {chat_id}")
                return True
        except Exception as spa_err:
            logger.warning(f"Sidebar transition to chat {chat_id} failed: {spa_err}. Falling back to standard navigation...")

        try:
            await self.page.goto(url, wait_until="domcontentloaded", timeout=self.driver.config.playwright.timeout_ms)
            await self.page.locator(self.selectors.chat_input).first.wait_for(state="attached", timeout=self.driver.config.playwright.timeout_ms)
            # Wait for history elements to load
            try:
                await self.page.locator('[data-message-author-role], [data-turn], div.markdown').first.wait_for(state="attached", timeout=5000)
            except Exception:
                pass
            logger.info(f"Successfully opened conversation: {chat_id}")
            return True
        except Exception as e:
            logger.warning(f"Failed to open conversation {chat_id} on first attempt: {e}. Retrying with hard reload...")
            try:
                await self.page.goto(url, wait_until="domcontentloaded", timeout=self.driver.config.playwright.timeout_ms)
                await self.page.reload(wait_until="domcontentloaded", timeout=self.driver.config.playwright.timeout_ms)
                await self.page.locator(self.selectors.chat_input).first.wait_for(state="attached", timeout=self.driver.config.playwright.timeout_ms)
                try:
                    await self.page.locator('[data-message-author-role], [data-turn], div.markdown').first.wait_for(state="attached", timeout=5000)
                except Exception:
                    pass
                logger.info(f"Successfully opened conversation on retry: {chat_id}")
                return True
            except Exception as retry_err:
                logger.error(f"Failed to open conversation {chat_id} on retry: {retry_err}", exc_info=True)
                await self.save_diagnostics_screenshot("open_conv_error")
                return False

    async def create_new_conversation(self) -> bool:
        """Navigates to ChatGPT portal to begin a new chat session."""
        url = "https://chatgpt.com"
        logger.info("Initializing new ChatGPT conversation...")
        
        try:
            if self.page.url == url or self.page.url == f"{url}/":
                input_locator = self.page.locator(self.selectors.chat_input)
                if await input_locator.count() > 0 and await input_locator.first.is_attached():
                    logger.info("Already on a new ChatGPT conversation screen and input is attached.")
                    return True
            
            await self.page.goto(url, wait_until="domcontentloaded", timeout=self.driver.config.playwright.timeout_ms)
            await self.page.locator(self.selectors.chat_input).first.wait_for(state="attached", timeout=self.driver.config.playwright.timeout_ms)
            logger.info("New ChatGPT conversation screen loaded.")
            return True
        except Exception as e:
            logger.warning(f"Failed to create new conversation on first attempt: {e}. Retrying with hard reload...")
            try:
                await self.page.goto(url, wait_until="domcontentloaded", timeout=self.driver.config.playwright.timeout_ms)
                await self.page.reload(wait_until="domcontentloaded", timeout=self.driver.config.playwright.timeout_ms)
                await self.page.locator(self.selectors.chat_input).first.wait_for(state="attached", timeout=self.driver.config.playwright.timeout_ms)
                logger.info("New ChatGPT conversation screen loaded on retry.")
                return True
            except Exception as retry_err:
                logger.error(f"Failed to create new conversation on retry: {retry_err}", exc_info=True)
                await self.save_diagnostics_screenshot("new_conv_error")
                return False

    def extract_chat_id(self) -> Optional[str]:
        """Parses the current browser URL to extract the unique ChatGPT chat ID."""
        url = self.page.url
        if "/c/" in url:
            parts = url.split("/c/")
            if len(parts) > 1:
                return parts[1].split("?")[0].split("/")[0]
        return None

    async def send_prompt_stream(self, prompt: str, file_paths: Optional[list] = None, skip_settle: bool = False, is_edit: bool = False) -> AsyncGenerator[str, None]:
        """Submits a prompt and optional file attachments to ChatGPT and yields response stream."""
        # Wait for any async page history loading to settle (only if not skipping settle)
        if not skip_settle:
            await asyncio.sleep(0.4)
            
        logger.info(f"Submitting prompt (is_edit={is_edit})...")
        
        try:
            if is_edit:
                logger.info("Executing prompt edit in ChatGPT browser tab...")
                js_edit = """
                async (newText) => {
                    const userMessages = document.querySelectorAll('div[data-message-author-role="user"]');
                    if (userMessages.length === 0) throw new Error("No user messages found to edit.");
                    
                    const lastUser = userMessages[userMessages.length - 1];
                    
                    // Trigger hover events to reveal action buttons
                    lastUser.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
                    lastUser.dispatchEvent(new MouseEvent('mouseenter', { bubbles: true }));
                    await new Promise(resolve => setTimeout(resolve, 150));
                    
                    let editBtn = lastUser.querySelector('button[aria-label*="Edit" i]');
                    if (!editBtn) {
                        const buttons = lastUser.querySelectorAll('button');
                        for (const btn of buttons) {
                            const label = (btn.getAttribute('aria-label') || btn.getAttribute('title') || '').toLowerCase();
                            if (label.includes('edit')) {
                                editBtn = btn;
                                break;
                            }
                        }
                    }
                    
                    if (!editBtn) throw new Error("Edit button not found.");
                    editBtn.click();
                    
                    for (let i = 0; i < 20; i++) {
                        const textarea = lastUser.querySelector('textarea');
                        if (textarea) {
                            textarea.focus();
                            const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, "value")?.set;
                            if (setter) {
                                setter.call(textarea, newText);
                            } else {
                                textarea.value = newText;
                            }
                            textarea.dispatchEvent(new Event('input', { bubbles: true }));
                            textarea.dispatchEvent(new Event('change', { bubbles: true }));
                            
                            const saveBtn = lastUser.querySelector('button.btn-primary, button[class*="primary"], button:not([class*="secondary"]):not([aria-label*="Cancel" i])');
                            if (saveBtn && !saveBtn.disabled && saveBtn.getAttribute('aria-disabled') !== 'true') {
                                saveBtn.click();
                                return true;
                            }
                        }
                        await new Promise(resolve => setTimeout(resolve, 100));
                    }
                    throw new Error("Edit textarea or submit button did not appear.");
                }
                """
                await self.page.evaluate(js_edit, prompt)
            else:
                # Check if the page is currently stuck in generating state (stop button visible)
                try:
                    stop_btn = self.page.locator('button[data-testid="stop-button"]').first
                    if await stop_btn.count() > 0 and await stop_btn.is_visible():
                        logger.info("ChatGPT UI is stuck in generating state (stop button visible). Forcing page reload to reset.")
                        await self.page.reload(wait_until="domcontentloaded")
                except Exception as e:
                    logger.warning(f"Error checking for stuck stop button: {e}")

                # Wait for the chat input to be ready in Python to allow Next.js hydration to complete
                try:
                    logger.info("Waiting for page input to settle in Python...")
                    await self.page.locator(self.selectors.chat_input).first.wait_for(state="attached", timeout=15000)
                    # Let the event loop rest briefly to ensure Next.js handlers are fully bound
                    await asyncio.sleep(2.0)
                except Exception as e:
                    logger.warning(f"Timeout waiting for input elements to settle: {e}")

                # Handle attachments upload
                if file_paths:
                    logger.info(f"Uploading file attachments to ChatGPT: {file_paths}")
                    try:
                        # Determine if we are uploading an image or a document/file
                        is_image = any(f.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp')) for f in file_paths)
                        if is_image:
                            file_input = self.page.locator('input#upload-photos, input[data-testid="upload-photos-input"]').first
                            if await file_input.count() == 0:
                                file_input = self.page.locator(self.selectors.file_input).first
                        else:
                            file_input = self.page.locator('input#upload-files').first
                            if await file_input.count() == 0:
                                file_input = self.page.locator(self.selectors.file_input).first

                        await file_input.wait_for(state="attached", timeout=5000)
                    except Exception as upload_err:
                        logger.critical(
                            f"[CRITICAL] ChatGPT UI change detected! File uploader element was not found. "
                            f"Please check selectors.yaml."
                        )
                        raise RuntimeError(f"File upload element not found: {upload_err}")
                        
                    await file_input.set_input_files(file_paths)
                    logger.info("File uploaded, waiting for send button to enable (up to 30 seconds)...")
                    submit_sel = 'button[data-testid="send-button"]:not([disabled]):not([aria-disabled="true"]), button[aria-label*="Send"]:not([disabled]):not([aria-disabled="true"])'
                    try:
                        await self.page.wait_for_selector(submit_sel, timeout=30000)
                        logger.info("Upload completed (send button enabled).")
                    except Exception as e:
                        logger.critical(
                            f"[CRITICAL] ChatGPT UI change detected or file upload failed! Send button did not enable after 30 seconds. "
                            f"Please check selectors.yaml."
                        )
                        raise TimeoutError(f"Timeout waiting for enabled send button after file upload: {e}")

                # If we are on an existing conversation page, wait for history elements to load first
                if "/c/" in self.page.url:
                    try:
                        logger.info("Waiting for existing conversation history elements to load...")
                        await self.page.locator('[data-message-author-role], [data-turn], div.markdown').first.wait_for(state="attached", timeout=10000)
                    except Exception as e:
                        logger.warning(f"Timeout or error waiting for history elements to load: {e}")

                # Get the state of the last assistant bubble before submitting to prevent history hydration race conditions
                # We avoid JSHandles to prevent Protocol errors if the DOM is refreshed/hydrated.
                last_assistant_state = await self.page.evaluate(
                    "() => { const els = document.querySelectorAll('[data-message-author-role=\"assistant\"], [data-turn=\"assistant\"]'); return els.length ? (els[els.length - 1].getAttribute('data-message-id') || els.length.toString()) : 'none'; }"
                )
                
                # Direct JavaScript Submission to bypass all Playwright click & fill actionability delays
                js_submit = """
                async (text) => {
                    const queryTextarea = () => {
                        const els = document.querySelectorAll('div#prompt-textarea, textarea[placeholder*="ChatGPT"], textarea[placeholder*="Ask"], textarea.wcDTda_fallbackTextarea');
                        for (const el of els) {
                            const rect = el.getBoundingClientRect();
                            if (rect.width > 0 && rect.height > 0 && window.getComputedStyle(el).display !== 'none') {
                                return el;
                            }
                        }
                        return els[0];
                    };
                    const textarea = queryTextarea();
                    if (!textarea) throw new Error("Chat input textarea not found.");
                    
                    textarea.focus();
                    
                    // Clear existing text first
                    if (textarea.tagName === 'DIV') {
                        textarea.innerHTML = '';
                        document.execCommand('insertText', false, text);
                    } else {
                        textarea.select();
                        const success = document.execCommand('insertText', false, text);
                        if (!success) {
                            const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, "value")?.set;
                            if (setter) {
                                setter.call(textarea, text);
                            } else {
                                textarea.value = text;
                            }
                            textarea.dispatchEvent(new Event('input', { bubbles: true }));
                            textarea.dispatchEvent(new Event('change', { bubbles: true }));
                        } else {
                            textarea.dispatchEvent(new Event('input', { bubbles: true }));
                            textarea.dispatchEvent(new Event('change', { bubbles: true }));
                        }
                    }
                    
                    const querySubmitBtn = () => {
                        const els = document.querySelectorAll('button[data-testid="send-button"], button[aria-label*="Send"], button[aria-label="Send prompt"]');
                        for (const el of els) {
                            const rect = el.getBoundingClientRect();
                            if (rect.width > 0 && rect.height > 0 && window.getComputedStyle(el).display !== 'none') {
                                return el;
                            }
                        }
                        return els[0];
                    };
                    
                    for (let i = 0; i < 60; i++) {
                        const submitBtn = querySubmitBtn();
                        if (submitBtn && !submitBtn.disabled && submitBtn.getAttribute('aria-disabled') !== 'true') {
                            submitBtn.click();
                            return true;
                        }
                        await new Promise(resolve => setTimeout(resolve, 100));
                    }
                    throw new Error("Submit button not found or remained disabled.");
                }
                """
                await self.page.evaluate(js_submit, prompt)
                logger.info("Prompt submitted via JS, waiting for response container...")
        except Exception as e:
            err_str = str(e)
            if "not found" in err_str or "selector" in err_str.lower():
                logger.critical(
                    f"[CRITICAL] ChatGPT UI change detected! Selector error during submission: {e}. "
                    f"Please check selectors.yaml or download the screenshot."
                )
            else:
                logger.error(f"Failed to submit prompt (is_edit={is_edit}): {e}", exc_info=True)
            await self.save_diagnostics_screenshot("submit_prompt_error")
            raise
            
        try:
            timeout = 90.0
            start_time = asyncio.get_event_loop().time()
            if is_edit:
                while True:
                    # Check for auth redirect
                    curr_url = self.page.url
                    if "/auth/" in curr_url or "/login" in curr_url:
                        raise PermissionError("Session expired (redirected to auth/login). Please run Option [2] in terminal.py to re-authorize.")
                    
                    # Check for Turnstile
                    cf_frame = self.page.locator('iframe[src*="challenges.cloudflare.com"]').first
                    if await cf_frame.count() > 0 and await cf_frame.is_visible():
                        raise ConnectionError("Cloudflare Turnstile challenge detected. The request was blocked.")
                        
                    # Check for error banner
                    err_banner = self.page.locator('div.text-token-text-error, div[class*="error-message"]').first
                    if await err_banner.count() > 0 and await err_banner.is_visible():
                        err_text = await err_banner.inner_text()
                        raise RuntimeError(f"ChatGPT error banner detected: {err_text}")

                    try:
                        is_generating = await asyncio.wait_for(
                            self.page.evaluate(f"() => document.querySelector('{self.selectors.streaming_indicators}') !== null"),
                            timeout=5.0
                        )
                    except Exception as eval_err:
                        logger.warning(f"Playwright edit evaluate timed out or failed: {eval_err}")
                        is_generating = False
                        
                    if is_generating:
                        break
                    if asyncio.get_event_loop().time() - start_time > timeout:
                        logger.warning("Timeout waiting for edit streaming to start. Continuing...")
                        break
                    await asyncio.sleep(0.5)
            else:
                try:
                    while True:
                        # Check for auth redirect
                        curr_url = self.page.url
                        if "/auth/" in curr_url or "/login" in curr_url:
                            raise PermissionError("Session expired (redirected to auth/login). Please run Option [2] in terminal.py to re-authorize.")
                        
                        # Check for Turnstile
                        cf_frame = self.page.locator('iframe[src*="challenges.cloudflare.com"]').first
                        if await cf_frame.count() > 0 and await cf_frame.is_visible():
                            raise ConnectionError("Cloudflare Turnstile challenge detected. The request was blocked.")
                            
                        # Check for error banner
                        err_banner = self.page.locator('div.text-token-text-error, div[class*="error-message"]').first
                        if await err_banner.count() > 0 and await err_banner.is_visible():
                            err_text = await err_banner.inner_text()
                            raise RuntimeError(f"ChatGPT error banner detected: {err_text}")

                        try:
                            is_new_bubble = await asyncio.wait_for(
                                self.page.evaluate(
                                    """(lastState) => {
                                        const els = document.querySelectorAll('[data-message-author-role="assistant"], [data-turn="assistant"]');
                                        if (els.length === 0) return false;
                                        const currentLast = els[els.length - 1];
                                        const currentState = currentLast.getAttribute('data-message-id') || els.length.toString();
                                        return currentState !== lastState;
                                    }""",
                                    last_assistant_state
                                ),
                                timeout=5.0
                            )
                        except Exception as eval_err:
                            logger.warning(f"Playwright bubble evaluate timed out or failed: {eval_err}")
                            is_new_bubble = False
                            
                        if is_new_bubble:
                            break
                        if asyncio.get_event_loop().time() - start_time > timeout:
                            raise TimeoutError("Timeout waiting for ChatGPT response generation to start.")
                        await asyncio.sleep(0.5)
                finally:
                    pass
        except Exception as e:
            if isinstance(e, TimeoutError):
                logger.critical(
                    f"[CRITICAL] ChatGPT UI change detected or request stalled! Response bubble ('div[data-message-author-role=\"assistant\"]') did not appear after {timeout} seconds. "
                    f"Please check selectors.yaml or download the screenshot."
                )
            else:
                logger.error(f"Error waiting for response bubble (is_edit={is_edit}): {e}", exc_info=True)
            await self.save_diagnostics_screenshot("bubble_wait_error")
            raise
            
        # Locate the last assistant container
        assistant_locator = self.page.locator('[data-message-author-role="assistant"], [data-turn="assistant"]').last
        # Scrape inside the markdown child element
        response_locator = assistant_locator.locator('div.markdown')
        
        logger.info("Scraping ChatGPT response stream...")
        last_text = ""
        unchanged_polls = 0
        empty_polls = 0
        max_unchanged_polls = 60
        
        js_scrape_stream = """
        (selector_indicator) => {
            const hasIndicator = document.querySelector(selector_indicator) !== null;
            
            const isGenerating = hasIndicator;
            
            const htmlToMarkdown = (node) => {
                if (!node) return "";
                if (node.nodeType === 3) {
                    if (node.parentNode) {
                        const parentTag = node.parentNode.tagName.toUpperCase();
                        if (['UL', 'OL', 'TR', 'TABLE', 'TBODY', 'THEAD'].includes(parentTag)) {
                            if (!node.textContent.trim()) return "";
                        }
                    }
                    return node.textContent;
                }
                if (node.nodeType === 1) {
                    const tagName = node.tagName.toUpperCase();
                    if (node.classList.contains('sr-only') || node.style.display === 'none') {
                        return "";
                    }
                    const parseChildren = () => {
                        return Array.from(node.childNodes).map(htmlToMarkdown).join("");
                    };
                    switch (tagName) {
                        case 'P': {
                            const parentTag = node.parentNode ? node.parentNode.tagName.toUpperCase() : "";
                            const suffix = (parentTag === 'LI') ? "" : "\\n\\n";
                            return parseChildren() + suffix;
                        }
                        case 'H1': return "# " + parseChildren() + "\\n\\n";
                        case 'H2': return "## " + parseChildren() + "\\n\\n";
                        case 'H3': return "### " + parseChildren() + "\\n\\n";
                        case 'H4': return "#### " + parseChildren() + "\\n\\n";
                        case 'H5': return "##### " + parseChildren() + "\\n\\n";
                        case 'H6': return "###### " + parseChildren() + "\\n\\n";
                        case 'STRONG':
                        case 'B':
                            return "**" + parseChildren() + "**";
                        case 'EM':
                        case 'I':
                            return "*" + parseChildren() + "*";
                        case 'CODE':
                            if (node.parentNode && node.parentNode.tagName === 'PRE') {
                                return parseChildren();
                            }
                            return "`" + parseChildren() + "`";
                        case 'PRE': {
                            const codeEl = node.querySelector('code');
                            let lang = "";
                            if (codeEl) {
                                const classList = Array.from(codeEl.classList);
                                const langClass = classList.find(c => c.startsWith('language-'));
                                if (langClass) {
                                    lang = langClass.replace('language-', '');
                                }
                            }
                            const codeText = codeEl ? Array.from(codeEl.childNodes).map(htmlToMarkdown).join("") : node.textContent;
                            return "\\n```" + lang + "\\n" + codeText.trim() + "\\n```\\n\\n";
                        }
                        case 'A': {
                            const href = node.getAttribute('href');
                            const text = parseChildren();
                            if (href && href.startsWith('http')) {
                                return "[" + text + "](" + href + ")";
                            }
                            return text;
                        }
                        case 'BLOCKQUOTE':
                            return "> " + parseChildren().trim().replace(/\\n/g, "\\n> ") + "\\n\\n";
                        case 'UL':
                        case 'OL':
                            return parseChildren() + "\\n";
                        case 'LI': {
                            const isOrdered = node.parentNode && node.parentNode.tagName === 'OL';
                            const prefix = isOrdered ? "1. " : "- ";
                            const content = parseChildren().trim();
                            return prefix + content + "\\n";
                        }
                        case 'BR':
                            return "\\n";
                        case 'TABLE':
                            return "\\n" + parseChildren() + "\\n";
                        case 'TR':
                            return parseChildren() + "\\n";
                        case 'TD':
                        case 'TH':
                            return parseChildren() + " | ";
                        default:
                            return parseChildren();
                    }
                }
                return "";
            };
            
            const assistants = document.querySelectorAll('[data-message-author-role="assistant"], [data-turn="assistant"]');
            let text = "";
            if (assistants.length > 0) {
                const lastAssistant = assistants[assistants.length - 1];
                let responseEl = lastAssistant.querySelector('div.markdown, div.prose, .markdown, .prose');
                if (responseEl) {
                    // Temporarily hide any source-citation UI blocks that ChatGPT web-search
                    // injects (e.g. div[class*="source"], div[data-testid*="citation"],
                    // div[data-testid*="source"]) so they are not scraped into the text output.
                    const hiddenEls = [];
                    const sourceSelectors = [
                        'div[class*="source"]',
                        'div[data-testid*="citation"]',
                        'div[data-testid*="source"]',
                        'div[data-testid*="search-result"]',
                        'div[class*="citation"]',
                        'div[class*="search-result"]'
                    ];
                    sourceSelectors.forEach(sel => {
                        responseEl.querySelectorAll(sel).forEach(el => {
                            hiddenEls.push({ el, prev: el.style.display });
                            el.style.display = 'none';
                        });
                    });
                    
                    let txt = htmlToMarkdown(responseEl);
                    
                    // Restore hidden elements
                    hiddenEls.forEach(({ el, prev }) => { el.style.display = prev; });
                    
                    txt = txt.replace(/^(Analyzing\\s*(image|file|data)?\\.{0,3}\\s*(\\r?\\n)+)|^(Analyzing\\s*(image|file|data)?\\.{0,3}\\s*$)/i, "");
                    txt = txt.replace(/^(\\[Speaker:\\s*Boundier\\]\\s*(\\r?\\n)*)/i, "");
                    text = txt.trim();
                }
            }
            
            return {
                isGenerating: isGenerating,
                text: text
            };
        }
        """

        while True:
            try:
                result = await asyncio.wait_for(
                    self.page.evaluate(js_scrape_stream, self.selectors.streaming_indicators),
                    timeout=5.0
                )
                is_generating = result["isGenerating"]
                current_text = result["text"]
            except Exception as e:
                logger.error(f"Error reading response stream: {e}", exc_info=True)
                await self.save_diagnostics_screenshot("stream_read_error")
                raise
                
            if current_text != last_text:
                delta = current_text[len(last_text):]
                if delta:
                    yield delta
                last_text = current_text
                unchanged_polls = 0
                empty_polls = 0
                try:
                    # Clear V8 heap memory actively during generation
                    await self.page.evaluate("window.gc && window.gc()")
                except Exception:
                    pass
            else:
                if is_generating and current_text == "":
                    empty_polls += 1
                    if empty_polls >= 180:  # Allow up to 90 seconds of empty text for DALL-E generation
                        logger.warning("Generation stream stalled (empty text). Terminating reader.")
                        break
                    unchanged_polls = 0
                else:
                    unchanged_polls += 1
                    empty_polls = 0
                
            # If ChatGPT has finished generating (send button is active), settle within 2 ticks (1.0s)
            if not is_generating and unchanged_polls >= 2:
                if current_text != "":
                    break
                    
            # If is_generating is True, allow more time for slow tasks like DALL-E generation
            stall_limit = 180 if is_generating else 40
            if unchanged_polls >= stall_limit:
                logger.warning(f"Generation stream stalled (is_generating={is_generating}). Terminating reader.")
                await self.save_diagnostics_screenshot("stream_stalled")
                break
                
            await asyncio.sleep(0.5)

    async def extract_generated_assets(self) -> list[dict]:
        """
        Scans the page/last assistant response for downloadable files and images,
        clicks download triggers (including opening the image lightbox modal if needed),
        and waits for Playwright download events.
        All files are saved to scratch/attachments/ and must be cleaned up by the caller.
        Returns a list of dicts: [{"path": save_path, "filename": filename, "type": "image"|"file"}]
        """
        assets = []
        try:
            last_assistant_selector = '[data-message-author-role="assistant"], [data-turn="assistant"]'
            last_assistant = self.page.locator(last_assistant_selector).last
            
            # Check if there is a generated image card (wrapper div with role="button" containing img)
            img_wrapper = last_assistant.locator('div[role="button"][aria-labelledby]')
            has_image = await img_wrapper.count() > 0
            
            if has_image:
                logger.info("Detected generated image card. Opening lightbox to download...")
                try:
                    await img_wrapper.click()
                    save_button = self.page.locator('button[aria-label="Save"]').first
                    await save_button.wait_for(state="visible", timeout=6000)
                    
                    async with self.page.expect_download(timeout=20000) as dl_info:
                        await save_button.click()
                    download = await dl_info.value
                    
                    filename = download.suggested_filename or "image.png"
                    os.makedirs("scratch/attachments", exist_ok=True)
                    save_path = os.path.abspath(os.path.join("scratch/attachments", filename))
                    await download.save_as(save_path)
                    
                    assets.append({"path": save_path, "filename": filename, "type": "image"})
                    logger.info(f"Asset captured via lightbox: '{filename}' (image) -> {save_path}")
                    
                    # Close lightbox
                    close_button = self.page.locator('button[aria-label="Close fullscreen view"]').first
                    if await close_button.count() > 0:
                        await close_button.click()
                        await asyncio.sleep(0.5)
                except Exception as e:
                    logger.error(f"Error downloading image from lightbox: {e}", exc_info=True)
                    # Close lightbox if open
                    try:
                        close_button = self.page.locator('button[aria-label="Close fullscreen view"]').first
                        if await close_button.count() > 0:
                            await close_button.click()
                    except:
                        pass

            # Now continue with standard file downloads (like docx, xlsx, txt) if any exist
            js_detect = """
            () => {
                const results = [];
                const bubbles = document.querySelectorAll('[data-message-author-role="assistant"], [data-turn="assistant"]');
                if (!bubbles.length) return results;
                const last = bubbles[bubbles.length - 1];

                // Find all download-triggering links/buttons in the bubble (excluding image overlays)
                const candidates = last.querySelectorAll(
                    'a[download], a[href*="download"], button.behavior-btn, a[href^="sandbox:"], a[href*="/files/"], a[href*="oaiusercontent"]'
                );
                candidates.forEach(el => {
                    let filename = el.getAttribute('download') || '';
                    if (!filename && el.tagName === 'BUTTON' && el.classList.contains('behavior-btn')) {
                        filename = el.textContent.trim();
                    }
                    if (!filename) {
                        const href = el.getAttribute('href') || '';
                        filename = href.split('/').pop().split('?')[0] || '';
                    }
                    if (!filename) filename = 'generated_file';
                    results.push({ filename: filename });
                });
                return results;
            }"""
            
            detected = await self.page.evaluate(js_detect)
            if detected:
                logger.info(f"Asset detection: found {len(detected)} downloadable document(s) in last response bubble.")
                os.makedirs("scratch/attachments", exist_ok=True)
                
                dl_buttons = last_assistant.locator('a[download], a[href*="download"], button.behavior-btn, a[href^="sandbox:"], a[href*="/files/"], a[href*="oaiusercontent"]')
                count = await dl_buttons.count()
                
                for i in range(count):
                    btn = dl_buttons.nth(i)
                    try:
                        async with self.page.expect_download(timeout=20000) as dl_info:
                            await btn.click()
                        download = await dl_info.value
                        
                        hint = detected[i]["filename"] if i < len(detected) else "generated_file"
                        filename = download.suggested_filename or hint or f"asset_{i}.bin"
                        
                        save_path = os.path.abspath(os.path.join("scratch/attachments", filename))
                        await download.save_as(save_path)
                        
                        ext = os.path.splitext(filename)[1].lower()
                        asset_type = "image" if ext in (".png", ".jpg", ".jpeg", ".webp", ".gif") else "file"
                        
                        assets.append({"path": save_path, "filename": filename, "type": asset_type})
                        logger.info(f"Asset captured: '{filename}' ({asset_type}) -> {save_path}")
                    except Exception as dl_err:
                        logger.warning(f"Failed to download asset [{i}]: {dl_err}")
                        
        except Exception as e:
            logger.error(f"Error in extract_generated_assets: {e}", exc_info=True)

        return assets

    async def get_sidebar_title_by_id(self, chat_id: str) -> Optional[str]:
        """Looks for the conversation in the sidebar by its ID and returns its title text."""
        try:
            selector = f'a[href*="{chat_id}"]'
            locator = self.page.locator(selector)
            if await locator.count() > 0:
                title = await locator.first.text_content()
                if title:
                    return title.strip()
        except Exception as e:
            logger.warning(f"Failed to get sidebar title for chat {chat_id}: {e}")
        return None

    async def get_sidebar_conversations(self) -> list:
        """Scrapes all conversation titles and IDs visible in the sidebar."""
        js_get_sidebar = """
        () => {
            const items = [];
            const links = document.querySelectorAll('a[href*="/c/"]');
            for (const link of links) {
                const href = link.getAttribute('href') || '';
                const parts = href.split('/c/');
                if (parts.length > 1) {
                    const id = parts[1].split('?')[0].split('/')[0];
                    const title = (link.textContent || '').trim();
                    if (id && title) {
                        items.push({ id: id, title: title });
                    }
                }
            }
            return items;
        }
        """
        try:
            return await self.page.evaluate(js_get_sidebar)
        except Exception as e:
            logger.warning(f"Failed to scrape sidebar conversations: {e}")
            return []