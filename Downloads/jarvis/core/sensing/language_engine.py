"""
Multilingual support. Currently: English + Urdu.
Whisper already handles both. TTS uses edge-tts Urdu voice.
"""

URDU_VOICE = "ur-PK-AsadNeural"  # Male Urdu voice
ENGLISH_VOICE = "en-US-AndrewNeural"
FRIDAY_VOICE = "en-US-JennyNeural"

URDU_TRIGGERS = [
    "urdu mein", "urdu me", "urdu main",
    "اردو", "بولو",
    "in urdu", "speak urdu",
]

ENGLISH_TRIGGERS = [
    "english mein", "in english", "speak english",
    "english me bolain", "english main",
]


def detect_language(text: str) -> str:
    """Detect if user wants Urdu or English response."""
    t = text.lower()
    if any(trigger in t for trigger in URDU_TRIGGERS):
        return "ur"
    if any(trigger in t for trigger in ENGLISH_TRIGGERS):
        return "en"
    # Auto-detect Urdu script
    urdu_chars = sum(1 for c in text if "\u0600" <= c <= "\u06ff")
    if urdu_chars > len(text) * 0.3:
        return "ur"
    return "en"


def get_voice_for_language(lang: str) -> str:
    return URDU_VOICE if lang == "ur" else ENGLISH_VOICE


def detect_script_language(text: str) -> str:
    """Detect script language from content only (no trigger words)."""
    urdu_chars = sum(1 for c in text if "\u0600" <= c <= "\u06ff")
    return "ur" if urdu_chars > len(text) * 0.25 else "en"


def get_voice_profiles() -> dict:
    """Named voice presets for assistant personas."""
    return {
        "jarvis": ENGLISH_VOICE,
        "friday": FRIDAY_VOICE,
        "urdu": URDU_VOICE,
    }


def get_system_prompt_suffix(lang: str) -> str:
    if lang == "ur":
        return "\n\nIMPORTANT: The user wants a response in Urdu (اردو). Respond in Urdu using Urdu script."
    return ""
