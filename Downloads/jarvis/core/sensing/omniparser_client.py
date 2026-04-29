"""
OmniParser V2 integration - Gap 3: GUI Semantic Understanding.
OmniParser converts screenshots to structured semantic elements.
GitHub: https://github.com/microsoft/OmniParser
"""

import time
import threading
from PIL import Image
import mss
import imagehash


class OmniParserClient:
    """
    Wraps OmniParser V2 for JARVIS.
    OmniParser parses screenshots into:
    - Interactable elements (buttons, inputs, links)
    - Semantic descriptions of each element
    - OCR text layer
    """

    def __init__(self, llm_fn):
        self.llm = llm_fn
        self._last_hash = None
        self._last_parse = None
        self._lock = threading.Lock()
        self._running = False
        self._omni_available = self._check_omni()

    def _check_omni(self) -> bool:
        """Check if OmniParser is installed."""
        try:
            import importlib.util

            spec = importlib.util.find_spec("omniparser")
            if spec:
                print("OmniParser V2: available")
                return True
        except Exception:
            pass
        print("OmniParser V2: not installed. Using LLM fallback.")
        print("To install: git clone https://github.com/microsoft/OmniParser && pip install -e OmniParser/")
        return False

    def parse_screen(self) -> dict:
        """Parse current screen into semantic elements."""
        # Capture screenshot.
        with mss.mss() as sct:
            shot = sct.grab(sct.monitors[1])
            img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")

        img_small = img.resize((1280, 720))

        # Delta check - only parse if screen changed meaningfully.
        current_hash = imagehash.phash(img_small)
        with self._lock:
            if self._last_hash is not None:
                if (current_hash - self._last_hash) <= 6:
                    return self._last_parse or {}
            self._last_hash = current_hash

        if self._omni_available:
            result = self._parse_with_omni(img_small)
        else:
            result = self._parse_with_llm_fallback(img_small)

        with self._lock:
            self._last_parse = result
        return result

    def _parse_with_omni(self, img: Image.Image) -> dict:
        """Use OmniParser V2 for full semantic parsing."""
        try:
            from omniparser import OmniParser

            parser = OmniParser()
            result = parser.parse(img)
            return {
                "elements": result.interactable_elements,
                "text": result.ocr_text,
                "semantic_summary": result.description,
                "element_count": len(result.interactable_elements),
                "method": "omniparser_v2",
            }
        except Exception as e:
            print(f"OmniParser error: {e}")
            return self._parse_with_llm_fallback(img)

    def _parse_with_llm_fallback(self, img: Image.Image) -> dict:
        """OCR + LLM fallback when OmniParser not available."""
        try:
            import pytesseract

            text = pytesseract.image_to_string(img)
            lines = [line.strip() for line in text.split("\n") if line.strip()]
            sample = " | ".join(lines[:15])
        except Exception:
            sample = ""

        summary = ""
        if sample:
            summary = self.llm(
                f"In one sentence, what is this person working on? Screen text: {sample[:400]}",
                max_tokens=60,
                temperature=0.1,
            )

        return {
            "elements": [],
            "text": sample,
            "semantic_summary": summary,
            "element_count": 0,
            "method": "llm_fallback",
        }

    def start_continuous(self, callback, interval_secs=10):
        """Continuously parse screen and call callback on meaningful changes."""
        self._running = True

        def _loop():
            while self._running:
                try:
                    result = self.parse_screen()
                    if result:
                        callback(result)
                except Exception as e:
                    print(f"Screen parse error: {e}")
                time.sleep(interval_secs)

        threading.Thread(target=_loop, daemon=True).start()

    def stop(self):
        self._running = False
