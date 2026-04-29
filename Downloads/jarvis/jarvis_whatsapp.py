"""
JARVIS WhatsApp Web Automation
Full read + reply via Selenium
"""

import time
import urllib.parse
import re
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from pathlib import Path


class WhatsAppAutomation:
    def __init__(self):
        self.driver  = None
        self.wait    = None
        self.profile = str(Path.home() / "jarvis_whatsapp_profile")

    def start(self):
        """Launch Chrome with saved WhatsApp session."""
        opts = webdriver.ChromeOptions()
        opts.add_argument(f"--user-data-dir={self.profile}")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--remote-allow-origins=*")

        # Try Selenium Manager first for best Chrome/driver compatibility.
        try:
            self.driver = webdriver.Chrome(options=opts)
        except Exception:
            service = Service(ChromeDriverManager().install())
            try:
                self.driver = webdriver.Chrome(service=service, options=opts)
            except Exception:
                # Fallback when the persistent profile is locked by another Chrome instance.
                fallback_opts = webdriver.ChromeOptions()
                fallback_opts.add_argument("--no-sandbox")
                fallback_opts.add_argument("--disable-dev-shm-usage")
                fallback_opts.add_argument("--disable-gpu")
                fallback_opts.add_argument("--remote-allow-origins=*")
                self.driver = webdriver.Chrome(service=service, options=fallback_opts)
        self.wait   = WebDriverWait(self.driver, 30)

        self.driver.get("https://web.whatsapp.com")
        print("WhatsApp Web loaded. Scan QR if first time.")
        # Wait until either chat UI or QR/login UI is visible.
        time.sleep(2)
        return True

    def _ensure_alive(self):
        """Ensure Selenium session/window is still valid before operations."""
        try:
            if self.driver is None:
                self.start()
                return
            _ = self.driver.current_url
        except Exception:
            try:
                self.close()
            except Exception:
                pass
            self.start()

    def _find_first(self, xpaths: list, timeout: int = 20):
        """Try multiple selectors and return the first matching element."""
        last_exc = None
        for xp in xpaths:
            try:
                return WebDriverWait(self.driver, timeout).until(
                    EC.presence_of_element_located((By.XPATH, xp))
                )
            except Exception as e:
                last_exc = e
                continue
        if last_exc:
            raise last_exc
        raise Exception("No selector candidates provided")

    def send_message(self, contact: str, message: str) -> bool:
        """Send a message to a contact."""
        try:
            self._ensure_alive()
            # Direct phone-number send path: works even if the number is not in contacts.
            if re.fullmatch(r"[+]?\d[\d\s-]{6,}", contact.strip()):
                phone = re.sub(r"\D", "", contact)
                text = urllib.parse.quote(message)
                self.driver.get(f"https://web.whatsapp.com/send?phone={phone}&text={text}")
                time.sleep(2)
                msg_box = self._find_first([
                    '//footer//div[@role="textbox" and @contenteditable="true"]',
                    '//div[@contenteditable="true"][@data-tab="10"]',
                    '//div[@contenteditable="true"][@data-tab="9"]',
                ], timeout=25)
                msg_box.click()
                msg_box.send_keys(Keys.RETURN)
                time.sleep(0.8)
                return True

            # Search for contact
            search = self._find_first([
                '//div[@role="textbox" and @contenteditable="true" and contains(@aria-label,"Search")]',
                '//div[@contenteditable="true"][@data-tab="3"]',
                '//div[@contenteditable="true"][@data-tab="2"]',
            ], timeout=25)
            search.click()
            search.send_keys(Keys.COMMAND + "a")
            search.send_keys(contact)
            time.sleep(1.5)
            search.send_keys(Keys.RETURN)
            time.sleep(1)

            # Find message input
            msg_box = self._find_first([
                '//footer//div[@role="textbox" and @contenteditable="true"]',
                '//div[@contenteditable="true"][@data-tab="10"]',
                '//div[@contenteditable="true"][@data-tab="9"]',
            ], timeout=25)
            msg_box.click()
            msg_box.send_keys(message)
            msg_box.send_keys(Keys.RETURN)
            time.sleep(0.5)
            return True
        except Exception as e:
            print(f"WhatsApp send error: {e}")
            return False

    def read_unread(self, max_chats: int = 5) -> list:
        """Read unread messages."""
        messages = []
        try:
            self._ensure_alive()
            unread = self.driver.find_elements(By.XPATH, '//span[contains(@aria-label, "unread") or @data-testid="icon-unread-count"]')
            for elem in unread[:max_chats]:
                try:
                    elem.click()
                    time.sleep(0.8)
                    # Get contact name
                    name_el = self._find_first([
                        '//header//span[@dir="auto"]',
                        '//header//*[@data-testid="conversation-info-header-chat-title"]',
                    ], timeout=10)
                    name = name_el.text
                    # Get last message
                    msgs = self.driver.find_elements(By.XPATH, '//div[contains(@class, "message-in")]//span[@dir="ltr"]')
                    if not msgs:
                        msgs = self.driver.find_elements(By.XPATH, '//div[contains(@data-testid,"msg-container")]//span[@dir="auto"]')
                    last = msgs[-1].text if msgs else ""
                    messages.append({"from": name, "message": last})
                except Exception:
                    continue
        except Exception as e:
            print(f"WhatsApp read error: {e}")
        return messages

    def close(self):
        if self.driver:
            self.driver.quit()