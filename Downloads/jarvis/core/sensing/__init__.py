"""
JARVIS Sensing
Input perception, emotion, face recognition, language detection, and GUI understanding.
"""

from .emotion_engine import Emotion, EmotionEngine
from .face_engine import face_engine
from .language_engine import detect_language, get_voice_for_language, detect_script_language, get_voice_profiles
from .omniparser_client import OmniParserClient

__all__ = [
    "Emotion",
    "EmotionEngine",
    "face_engine",
    "detect_language",
    "get_voice_for_language",
    "detect_script_language",
    "get_voice_profiles",
    "OmniParserClient",
]
