import warnings
warnings.filterwarnings("ignore", category=UserWarning)
import sys

import whisper
import pyaudio
import wave
import os
import tempfile
import asyncio
import chromadb
import uuid
import struct
import math
import subprocess
import threading
import time
import edge_tts
import PyPDF2
import docx
import cv2
import mss
import psutil
import urllib.request
import urllib.parse
import json
import re
import webbrowser
import tkinter as tk
from tkinter import font as tkfont
from groq import Groq
from dotenv import load_dotenv
from datetime import datetime, timedelta
from ddgs import DDGS
from pathlib import Path
from PIL import Image
from collections import deque
from queue import Queue, Empty
from dataclasses import dataclass, field
from typing import Optional, List, Dict
import imagehash
import requests as http_requests
import traceback
try:
    from google.cloud import texttospeech
except Exception:
    texttospeech = None
from core.sensing import face_engine, detect_language, get_voice_for_language, detect_script_language, get_voice_profiles, OmniParserClient
from core.agents.agent_sync import AgentSyncServer
from core.platforms import github_create_issue, github_push_repo, hotkey_engine, HomeAssistantClient
from core.memory.continuum_memory import ContinuumMemorySystem
from core.orchestration import GoalEngine, ModelRouter, ComputeBudget, ComputeTier, HSLOrchestrator, WorldModel
from core.memory.memory_stack import MemoryStack, start_nightly_scheduler
from core.agents.autonomy_planner import AutonomyPlanner
from core.memory.social_memory import SocialMemory
from core.execution import VOICE_PROFILES as TTS_VOICE_PROFILES, PersistentExecutor
from core.agents.mixture_of_agents import MixtureOfAgents
from core.memory.titan_memory import TitanMemoryModule
from core.memory.cross_modal_fusion import CrossModalFusion
from core.agents.agent_graph import AgentOrchestrator
from core.agents.htn_planner import HTNPlanner
from core.memory.goal_stack import GoalStack
from core.memory.multimodal_memory import MultimodalMemory
from training.monthly_lora import start_monthly_scheduler
from training.reasoning_distillation import create_reasoning_modelfile

load_dotenv()

# ── STARTUP PREFERENCES ──────────────────────────────────────────────────────
STARTUP_CONFIG_PATH = Path("config/jarvis_startup.json")
DEFAULT_STARTUP_PREFS = {
    "tts_backend": "edge",
    "voice_persona": "jarvis",
    "prosody_mode": "auto",
}
startup_prefs = dict(DEFAULT_STARTUP_PREFS)


def _load_startup_prefs() -> dict:
    try:
        if STARTUP_CONFIG_PATH.exists():
            data = json.loads(STARTUP_CONFIG_PATH.read_text())
            prefs = dict(DEFAULT_STARTUP_PREFS)
            prefs.update({k: v for k, v in data.items() if k in prefs and isinstance(v, str)})
            return prefs
    except Exception as e:
        print(f"Startup prefs load error: {e}")
    return dict(DEFAULT_STARTUP_PREFS)


def _save_startup_prefs() -> None:
    try:
        STARTUP_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        STARTUP_CONFIG_PATH.write_text(json.dumps(startup_prefs, indent=2))
    except Exception as e:
        print(f"Startup prefs save error: {e}")


def _persist_pref(key: str, value: str) -> None:
    startup_prefs[key] = value
    _save_startup_prefs()

# ── TTS STATE ───────────────────────────────────────────────────────────────
TTS_QUEUE    = Queue()
TTS_PLAYING  = threading.Event()  # Set when audio is playing
TTS_MUTED    = threading.Event()  # Mutes wake-word while speaking
TTS_PROC     = None
TTS_LOOP     = asyncio.new_event_loop()
VOICE        = "en-US-AndrewNeural"
TTS_PROSODY_MODE = "auto"  # auto | balanced | thoughtful | concise
TTS_BACKEND  = os.getenv("JARVIS_TTS_BACKEND", "edge").strip().lower()  # edge | google
STOP_SIGNAL  = threading.Event()
VOICE_ASSISTANT_ENABLED = threading.Event()
VOICE_ASSISTANT_ENABLED.set()
VOICE_OVERRIDE = None
VOICE_PERSONA = "jarvis"

# Start the TTS event loop in a background thread ONCE
threading.Thread(target=TTS_LOOP.run_forever, daemon=True).start()


def _rate_to_google(rate: str) -> float:
    m = re.match(r"([+-]?\d+)%", (rate or "").strip())
    if not m:
        return 1.0
    pct = int(m.group(1))
    return max(0.25, min(2.0, 1.0 + (pct / 100.0)))


def _pitch_to_google(pitch: str) -> float:
    m = re.match(r"([+-]?\d+)(?:hz|Hz)?", (pitch or "").strip())
    if not m:
        return 0.0
    return float(m.group(1))


def _google_voice_config(lang: str):
    # Friday: female EN, Jarvis: male EN, Urdu: male UR.
    if lang == "ur":
        return ("ur-PK", texttospeech.SsmlVoiceGender.MALE)
    if VOICE_PERSONA == "friday":
        return ("en-US", texttospeech.SsmlVoiceGender.FEMALE)
    return ("en-US", texttospeech.SsmlVoiceGender.MALE)


async def _tts_gen(text: str, voice: str, rate: str, pitch: str, volume: str) -> str:
    """Generate one audio file. Returns temp file path."""
    f = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    f.close()
    lang = detect_script_language(text)

    if TTS_BACKEND == "google" and texttospeech is not None:
        client = texttospeech.TextToSpeechClient()
        language_code, gender = _google_voice_config(lang)
        synthesis_input = texttospeech.SynthesisInput(text=text)
        voice_params = texttospeech.VoiceSelectionParams(
            language_code=language_code,
            ssml_gender=gender,
        )
        audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.MP3,
            speaking_rate=_rate_to_google(rate),
            pitch=_pitch_to_google(pitch),
            volume_gain_db=0.0,
        )
        response = client.synthesize_speech(
            input=synthesis_input,
            voice=voice_params,
            audio_config=audio_config,
        )
        with open(f.name, "wb") as out:
            out.write(response.audio_content)
        return f.name

    await edge_tts.Communicate(
        text,
        voice=voice,
        rate=rate,
        pitch=pitch,
        volume=volume,
    ).save(f.name)
    return f.name


def _clean_tts_text(text: str) -> str:
    """Normalize text for smoother and more human TTS output."""
    t = text or ""
    # Remove markdown artifacts and make punctuation spacing consistent.
    t = re.sub(r"```.*?```", "", t, flags=re.DOTALL)
    t = re.sub(r"[*_`#]", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    t = t.replace("Tayyab", "Boss").replace("tayyab", "Boss")
    # Slightly shorten hard pauses by avoiding repeated punctuation.
    t = re.sub(r"([.!?]){2,}", r"\1", t)
    t = re.sub(r",{2,}", ",", t)
    return t


def _build_tts_segments(text: str, default_voice: str) -> List[tuple]:
    """
    Build larger language-aware chunks to avoid sentence-by-sentence gaps.
    Returns list of (segment_text, voice).
    """
    cleaned = _clean_tts_text(text)
    if not cleaned:
        return []

    profiles = get_voice_profiles()
    english_voice = default_voice if default_voice != profiles.get("urdu") else profiles.get("jarvis")
    urdu_voice = profiles.get("urdu")

    # Keep punctuation in the chunks so commas stay as short pauses.
    raw_chunks = [c.strip() for c in re.split(r"(?<=[.!?۔])\s+", cleaned) if c.strip()]
    if not raw_chunks:
        raw_chunks = [cleaned]

    segments: List[tuple] = []
    cur_text = ""
    cur_voice = None
    cur_lang = "en"

    for chunk in raw_chunks:
        lang = detect_script_language(chunk)
        voice = urdu_voice if lang == "ur" else english_voice

        if cur_voice is None:
            cur_voice = voice
            cur_text = chunk
            cur_lang = lang
            continue

        # Merge adjacent chunks when voice matches and total stays manageable.
        if voice == cur_voice and len(cur_text) + len(chunk) < 420:
            cur_text = f"{cur_text} {chunk}".strip()
        else:
            segments.append((cur_text, cur_voice, cur_lang))
            cur_text = chunk
            cur_voice = voice
            cur_lang = lang

    if cur_text:
        segments.append((cur_text, cur_voice or english_voice, cur_lang))

    return segments


def _prosody_values(mode: str, lang: str = "en") -> tuple:
    """Return edge-tts prosody params for a mode/language pair."""
    presets = {
        "balanced":   ("+6%", "+1Hz", "+0%"),
        "thoughtful": ("-7%", "-2Hz", "+0%"),
        "concise":    ("+14%", "+2Hz", "+0%"),
    }
    rate, pitch, volume = presets.get(mode, presets["balanced"])

    # Urdu generally sounds clearer with a slightly slower rate.
    if lang == "ur" and mode == "balanced":
        rate = "+2%"
    elif lang == "ur" and mode == "concise":
        rate = "+8%"
    return rate, pitch, volume


def _get_hsl_frustration() -> float:
    try:
        with hsl_lock:
            return float(hsl.emotional.frustration_score)
    except Exception:
        return 0.0


def _auto_prosody_mode(text: str) -> str:
    """Choose natural prosody from context and emotional state."""
    t = (text or "").lower()
    frustration = _get_hsl_frustration()
    supportive_cues = ["sorry", "failed", "error", "issue", "frustrated", "i understand"]
    if frustration >= 0.45 or any(c in t for c in supportive_cues):
        return "thoughtful"
    if len(t) <= 100:
        return "concise"
    return "balanced"


def _resolve_prosody_mode(text: str) -> str:
    mode = (TTS_PROSODY_MODE or "auto").lower().strip()
    if mode == "auto":
        return _auto_prosody_mode(text)
    if mode in ["balanced", "thoughtful", "concise"]:
        return mode
    return "balanced"


def _tts_worker():
    """Single worker - one audio stream at a time. ZERO double audio."""
    global TTS_PROC
    while True:
        try:
            fpath, done_cb = TTS_QUEUE.get(timeout=1)
        except Empty:
            continue
        try:
            TTS_PLAYING.set()
            TTS_MUTED.set()
            TTS_PROC = subprocess.Popen(
                ["afplay", fpath],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            TTS_PROC.wait()
        except Exception as e:
            print(f"TTS playback: {e}")
        finally:
            try:
                os.unlink(fpath)
            except Exception as e:
                print(f"TTS file cleanup error: {e}")
            TTS_PROC = None
            if TTS_QUEUE.empty():
                TTS_PLAYING.clear()
                TTS_MUTED.clear()
            if done_cb:
                done_cb()
            TTS_QUEUE.task_done()


# Start the single worker thread ONCE
threading.Thread(target=_tts_worker, daemon=True).start()


def speak_stop():
    """Stop JARVIS speaking immediately."""
    global TTS_PROC
    # Clear queue
    while not TTS_QUEUE.empty():
        try:
            fp, _ = TTS_QUEUE.get_nowait()
            try:
                os.unlink(fp)
            except Exception as e:
                print(f"TTS queue file unlink error: {e}")
            TTS_QUEUE.task_done()
        except Empty:
            break
    # Kill current playback
    if TTS_PROC and TTS_PROC.poll() is None:
        TTS_PROC.terminate()
        TTS_PROC = None
    TTS_PLAYING.clear()
    TTS_MUTED.clear()
    print("JARVIS: [stopped]")


def speak(text: str, gui=None):
    """
    THE ONLY speak() function in the codebase.
    Generates all sentences in parallel, plays sequentially.
    Zero double audio guaranteed by single worker thread.
    """
    if not text or not text.strip() or text.strip() == "None":
        return

    print(f"JARVIS: {text}")
    if gui:
        gui.set_state("speaking")
        gui.show_response(text)
        gui.add_log("jarvis", text)

    segments = _build_tts_segments(text, VOICE)
    if not segments:
        return

    mode = _resolve_prosody_mode(text)

    # Generate all audio in parallel on persistent loop
    async def gen_all():
        tasks = []
        for seg_text, seg_voice, seg_lang in segments:
            rate, pitch, volume = _prosody_values(mode, seg_lang)
            tasks.append(_tts_gen(seg_text, seg_voice, rate, pitch, volume))
        return await asyncio.gather(*tasks)

    try:
        files = asyncio.run_coroutine_threadsafe(gen_all(), TTS_LOOP).result(timeout=20)
    except Exception as e:
        print(f"TTS gen error: {e}")
        if gui:
            gui.set_state("idle")
        return

    # Queue files for sequential playback
    for i, fpath in enumerate(files):
        cb = None
        if i == len(files) - 1 and gui:
            def cb(g=gui):
                g.set_state("idle")
        TTS_QUEUE.put((fpath, cb))


def init_tts():
    """Ensure TTS backend/config are initialized before runtime."""
    global TTS_BACKEND
    if TTS_BACKEND not in {"edge", "google"}:
        TTS_BACKEND = "edge"
    print(f"TTS initialized ({TTS_BACKEND}).")

# ── CONFIG ───────────────────────────────────────────────────────────────────
WAKE_WORD           = "jarvis"
SILENCE_LIMIT       = 2.0
MIN_SPEECH_TIME     = 0.5
WHISPER_MODEL       = "tiny"
SILENCE_THRESHOLD   = 500
HOME_DIR            = str(Path.home())
CAMERA_INDEX        = 0
CPU_ALERT           = 85
RAM_ALERT           = 85
BATT_ALERT          = 20
IDLE_ALERT_MINS     = 30
BREAK_ALERT_MINS    = 60
BRIEFING_HOUR       = 8
SYNTHESIS_EVERY_N   = 10
DELTA_THRESHOLD     = 8       # Perceptual hash distance for screen change
SCREEN_CAPTURE_FPS  = 5       # Frames per second for screen monitoring
HSL_UPDATE_INTERVAL = 5       # Seconds between HSL updates

# LLM Config — Ollama local by default, Groq as fallback
USE_LOCAL_LLM   = True        # Set False to force Groq
LOCAL_MODEL     = "jarvis-reasoning"  # Ollama model name
GROQ_MODEL      = "llama-3.3-70b-versatile"
OLLAMA_BASE_URL = "http://localhost:11434"
# ─────────────────────────────────────────────────────────────────────────────

print("Initializing JARVIS Phase 12 — AGI Architecture...")
print("Loading Whisper...")
whisper_model = whisper.load_model(WHISPER_MODEL)

# LLM Client — Ollama or Groq
groq_client = None
try:
    groq_client = Groq(api_key=os.getenv("GROQ_API_KEY", ""))
except Exception:
    pass

chroma_client  = chromadb.PersistentClient(path="./jarvis_memory")
memory         = chroma_client.get_or_create_collection(name="conversations")
wrong_log      = chroma_client.get_or_create_collection(name="wrong_intents")
profile_col    = chroma_client.get_or_create_collection(name="user_profile")
hsl_history    = chroma_client.get_or_create_collection(name="hsl_history")

CHUNK    = 1024
FORMAT   = pyaudio.paInt16
CHANNELS = 1
RATE     = 16000

session_start      = time.time()
last_interaction   = time.time()
alerts_given       = set()
battery_history    = deque(maxlen=100)
briefing_done      = False
interaction_count  = 0
scheduled_alerts   = []
mem_stack          = None
wa_automation      = None
code_watcher       = None
task_chain_engine  = None
email_intelligence = None
omniparser_client  = None
social_memory      = None
model_router       = None
routine_predictor  = None
goal_engine        = None
autonomy_planner   = None
permission_policy  = None
verification_layer = None
daily_review_engine = None
cms               = None
symbolic          = None
world             = None
agent_sync        = None
home              = None
evaluator         = None
benchmarks        = None
moa               = None
titan             = None
constitution      = None
cross_modal       = None
agent_orchestrator = None
dyn_prompt        = None
budget            = None

prm               = None
htn               = None
safety            = None
goal_stack        = None
hsl_orchestrator  = None
improver          = None


TERMINAL_LOG_PATH = Path("jarvis_terminal.log")


class _TeeStream:
    """Write output both to original stream and the terminal log file."""
    def __init__(self, original, log_handle):
        self.original = original
        self.log_handle = log_handle

    def write(self, data):
        try:
            self.original.write(data)
        except Exception as e:
            print(f"_TeeStream write original error: {e}")
        try:
            self.log_handle.write(data)
            self.log_handle.flush()
        except Exception as e:
            print(f"_TeeStream write log error: {e}")
        return len(data)

    def flush(self):
        try:
            self.original.flush()
        except Exception as e:
            print(f"_TeeStream flush original error: {e}")
        try:
            self.log_handle.flush()
        except Exception as e:
            print(f"_TeeStream flush log error: {e}")


def setup_terminal_error_logging() -> None:
    """Mirror process stdout/stderr into jarvis_terminal.log for live error watching."""
    try:
        TERMINAL_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        log_handle = open(TERMINAL_LOG_PATH, "a", buffering=1, encoding="utf-8")
        ts = datetime.now().isoformat()
        log_handle.write(f"\n\n=== JARVIS SESSION START {ts} ===\n")
        if not isinstance(sys.stdout, _TeeStream):
            sys.stdout = _TeeStream(sys.stdout, log_handle)
        if not isinstance(sys.stderr, _TeeStream):
            sys.stderr = _TeeStream(sys.stderr, log_handle)
    except Exception as e:
        print(f"Terminal logging setup failed: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# GAP 1: UNIVERSAL CONTEXT SCHEMA — HumanStateLayer (HSL)
# Original contribution from Project JARVIS research
# The missing protocol layer between tool data and human context
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class BehavioralState:
    screen_context:               str   = "unknown"
    active_task_type:             str   = "unknown"
    task_transition:              bool  = False
    time_on_current_context_mins: float = 0.0
    last_screen_change_secs_ago:  float = 0.0
    error_visible_on_screen:      bool  = False
    error_text:                   str   = ""
    confidence:                   float = 0.0

@dataclass
class EmotionalState:
    dominant_emotion:   str   = "neutral"   # neutral/frustrated/excited/tired/focused
    valence:            float = 0.5         # 0.0 negative — 1.0 positive
    arousal:            float = 0.5         # 0.0 calm — 1.0 activated
    frustration_score:  float = 0.0         # 0.0-1.0
    confidence:         float = 0.0

@dataclass
class CognitiveState:
    focus_level:            float = 0.5     # 0.0-1.0
    cognitive_load:         str   = "medium" # low/medium/high/overloaded
    working_memory_strain:  float = 0.0     # 0.0-1.0
    task_completion_rate:   float = 0.5

@dataclass
class PersonalitySnapshot:
    openness:           float = 0.5
    conscientiousness:  float = 0.5
    extraversion:       float = 0.3    # Tayyab is slightly introverted based on profile
    agreeableness:      float = 0.6
    neuroticism:        float = 0.4
    current_mode:       str   = "focused"  # focused/creative/tired/social

@dataclass
class SituationalContext:
    time_of_day:             str   = "unknown"
    session_duration_mins:   float = 0.0
    idle_duration_mins:      float = 0.0
    suggested_action:        str   = "monitor"  # monitor/intervene/log/ignore
    intervention_confidence: float = 0.0
    last_intervention_mins:  float = 999.0

@dataclass
class HumanStateLayer:
    """
    The Universal Context Schema for JARVIS.
    This is the HSL — our original contribution to the AGI architecture problem.
    Every perception layer writes to this. Every action layer reads from this.
    """
    timestamp:     str               = ""
    behavioral:    BehavioralState   = field(default_factory=BehavioralState)
    emotional:     EmotionalState    = field(default_factory=EmotionalState)
    cognitive:     CognitiveState    = field(default_factory=CognitiveState)
    personality:   PersonalitySnapshot = field(default_factory=PersonalitySnapshot)
    situational:   SituationalContext = field(default_factory=SituationalContext)
    user_profile:  str               = ""
    raw_signals:   dict              = field(default_factory=dict)

    def to_context_string(self) -> str:
        """Convert HSL to a string for injection into LLM prompts."""
        return (
            f"[HSL] Emotion: {self.emotional.dominant_emotion} "
            f"(frustration: {self.emotional.frustration_score:.1f}) | "
            f"Focus: {self.cognitive.focus_level:.1f} | "
            f"Load: {self.cognitive.cognitive_load} | "
            f"Screen: {self.behavioral.screen_context} | "
            f"Task: {self.behavioral.active_task_type} | "
            f"Session: {self.situational.session_duration_mins:.0f}min | "
            f"Idle: {self.situational.idle_duration_mins:.1f}min"
        )

# Global HSL state — single source of truth
hsl = HumanStateLayer()
hsl_lock = threading.Lock()


# ══════════════════════════════════════════════════════════════════════════════
# GAP 3: GUI SEMANTIC UNDERSTANDING — Delta-Frame Pipeline
# Implementing our proposed solution from Appendix E
# Reduces compute by ~95% vs naive approach
# ══════════════════════════════════════════════════════════════════════════════

class ScreenContextSynthesizer:
    """
    Rolling window synthesizer for temporal screen context.
    Proposed in Appendix E as solution to context blindness.
    """
    def __init__(self, window_size: int = 10):
        self.history:      List[dict] = []
        self.window_size:  int        = window_size
        self.task_start:   float      = time.time()
        self.last_context: str        = "unknown"

    def update(self, current_parse: dict) -> dict:
        self.history.append(current_parse)
        if len(self.history) > self.window_size:
            self.history.pop(0)

        context = current_parse.get("semantic_summary", "unknown")
        if context != self.last_context:
            self.task_start   = time.time()
            self.last_context = context

        return {
            "screen_context":               context,
            "active_task_type":             self._infer_task(context),
            "task_transition":              len(self.history) > 1 and
                                            self.history[-1].get("semantic_summary") !=
                                            self.history[-2].get("semantic_summary"),
            "time_on_current_context_mins": (time.time() - self.task_start) / 60,
            "error_visible":                self._detect_error(context),
        }

    def _infer_task(self, context: str) -> str:
        c = context.lower()
        if any(w in c for w in ["code", "editor", "terminal", "python", "function", "error", "debug"]):
            return "coding"
        if any(w in c for w in ["browser", "chrome", "safari", "search", "google"]):
            return "browsing"
        if any(w in c for w in ["notion", "document", "write", "notes"]):
            return "writing"
        if any(w in c for w in ["slack", "message", "whatsapp", "email"]):
            return "communication"
        return "general"

    def _detect_error(self, context: str) -> bool:
        error_keywords = ["error", "exception", "traceback", "failed", "undefined",
                          "cannot", "syntax", "404", "500"]
        return any(w in context.lower() for w in error_keywords)


screen_synthesizer = ScreenContextSynthesizer()
last_screen_hash   = None
current_screen_ctx = "unknown"


def delta_frame_pipeline():
    """Our proposed solution to Gap 3 — continuous GUI understanding at low compute cost."""
    global last_screen_hash, current_screen_ctx, hsl, omniparser_client

    while True:
        try:
            # Stage 1: Capture at low fps
            with mss.mss() as sct:
                shot = sct.grab(sct.monitors[1])
                img  = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")

            img_small = img.resize((640, 360))

            # Stage 2: Delta detection — our key innovation
            current_hash = imagehash.phash(img_small)
            if last_screen_hash is not None:
                delta = current_hash - last_screen_hash
                if delta <= DELTA_THRESHOLD:
                    # No meaningful change — skip expensive processing
                    time.sleep(1.0 / SCREEN_CAPTURE_FPS)
                    continue

            last_screen_hash = current_hash

            # Stage 3: Semantic parsing via OmniParser (with legacy OCR+LLM fallback)
            parse_result = None
            if omniparser_client is not None:
                try:
                    parse_result = omniparser_client.parse_screen()
                except Exception as e:
                    print(f"OmniParser pipeline error: {e}")
                    parse_result = None

            if not parse_result or not str(parse_result.get("semantic_summary", "")).strip():
                try:
                    import pytesseract
                    text = pytesseract.image_to_string(img_small)
                    lines = [l.strip() for l in text.split("\n") if l.strip()]
                    sample = " | ".join(lines[:12])
                except Exception:
                    sample = ""

                if sample:
                    prompt = (
                        f"In one sentence, what is this person working on? "
                        f"Screen text: {sample[:400]}"
                    )
                    description = llm_call(prompt, max_tokens=60, temperature=0.1)
                else:
                    description = "Screen content unclear"

                parse_result = {
                    "semantic_summary": description,
                    "timestamp": time.time(),
                    "raw_text": sample[:200],
                    "method": "legacy_ocr_llm",
                }
            else:
                parse_result["timestamp"] = time.time()
                parse_result["raw_text"] = str(parse_result.get("text", ""))[:200]

            # Stage 4: Context synthesis with rolling window
            ctx = screen_synthesizer.update(parse_result)

            # Write to HSL behavioral state
            with hsl_lock:
                hsl.behavioral.screen_context               = ctx["screen_context"]
                hsl.behavioral.active_task_type             = ctx["active_task_type"]
                hsl.behavioral.task_transition              = ctx["task_transition"]
                hsl.behavioral.time_on_current_context_mins = ctx["time_on_current_context_mins"]
                hsl.behavioral.error_visible_on_screen      = ctx["error_visible"]
                current_screen_ctx                          = ctx["screen_context"]

        except Exception as e:
            print(f"Screen pipeline error: {e}")

        time.sleep(1.0 / SCREEN_CAPTURE_FPS)


# ══════════════════════════════════════════════════════════════════════════════
# GAP 2: PROACTIVE INTERRUPTION — Three-Gate Model
# Our proposed solution with adaptive threshold learning
# ══════════════════════════════════════════════════════════════════════════════

interruption_threshold  = 0.65   # Starts at 0.65, adapts based on feedback
interruption_history    = deque(maxlen=20)


def score_interruption(alert_type: str, message: str) -> float:
    """
    Three-gate interruption model from Appendix D research.
    Returns combined score 0.0-1.0. Must exceed threshold to interrupt.
    """
    with hsl_lock:
        idle_mins    = hsl.situational.idle_duration_mins
        session_mins = hsl.situational.session_duration_mins
        focus        = hsl.cognitive.focus_level
        emotion      = hsl.emotional.dominant_emotion
        last_int     = hsl.situational.last_intervention_mins

    # Gate 1 — Urgency (0.0-1.0)
    urgency_map = {
        "battery_critical":  1.0,
        "cpu_critical":      0.8,
        "ram_critical":      0.7,
        "error_on_screen":   0.9,
        "idle_too_long":     0.5,
        "break_suggestion":  0.4,
        "work_insight":      0.3,
    }
    gate1 = urgency_map.get(alert_type, 0.3)

    # Gate 2 — Relevance to current activity (0.0-1.0)
    if "battery" in alert_type:
        gate2 = 1.0  # Always relevant
    elif "error" in alert_type and hsl.behavioral.active_task_type == "coding":
        gate2 = 1.0  # Error while coding — very relevant
    elif "idle" in alert_type and idle_mins > IDLE_ALERT_MINS:
        gate2 = 0.8
    else:
        gate2 = gate1 * 0.5  # Reduced relevance if not directly applicable

    # Gate 3 — Timing: enough time since last interaction + interruption
    timing_score = min(1.0, idle_mins / 3.0)       # Peaks after 3 min idle
    recency_pen  = max(0.0, 1.0 - last_int / 10.0) # Penalty if interrupted < 10 min ago
    gate3        = timing_score * (1.0 - recency_pen * 0.5)

    # Focus penalty — don't interrupt deep focus
    focus_penalty = focus * 0.3  # High focus reduces score

    combined = (gate1 * 0.4 + gate2 * 0.4 + gate3 * 0.2) - focus_penalty
    return max(0.0, min(1.0, combined))


def should_interrupt(alert_type: str, message: str) -> bool:
    score = score_interruption(alert_type, message)
    return score >= interruption_threshold


def adapt_threshold(user_feedback: str):
    """Adaptive threshold — learns from user reactions to interruptions."""
    global interruption_threshold
    if user_feedback in ["stop", "too much", "quiet", "annoying"]:
        interruption_threshold = min(0.95, interruption_threshold + 0.05)
        print(f"Threshold raised to {interruption_threshold:.2f}")
    elif user_feedback in ["good", "helpful", "keep going", "thanks"]:
        interruption_threshold = max(0.3, interruption_threshold - 0.03)
        print(f"Threshold lowered to {interruption_threshold:.2f}")


# ══════════════════════════════════════════════════════════════════════════════
# GAP 4: LIFELONG PERSONAL MODEL — L0 + L1 Implementation
# Based on Second Me (Wei & Shang, 2024) and ICLR 2025 findings
# L0: ChromaDB (done), L1: Structured profile with temporal decay
# ══════════════════════════════════════════════════════════════════════════════

class L1ProfileEngine:
    """
    L1 Structured Profile from Appendix F.
    Implements: temporal decay, recency weighting, Big Five tracking.
    """

    def __init__(self):
        self.profile: Dict = {
            "big_five": {
                "openness":          0.5,
                "conscientiousness": 0.5,
                "extraversion":      0.3,
                "agreeableness":     0.6,
                "neuroticism":       0.4,
            },
            "working_style":   "unknown",
            "current_projects": [],
            "preferences": {},
            "recurring_patterns": [],
            "expertise_level": "intermediate",
            "last_updated": datetime.now().isoformat(),
            "interaction_count": 0,
        }
        self._load_from_db()

    def _load_from_db(self):
        try:
            c = profile_col.count()
            if c > 0:
                r = profile_col.query(query_texts=["user profile"], n_results=1)
                if r["documents"][0]:
                    stored = json.loads(r["documents"][0][0])
                    self.profile.update(stored)
        except Exception as e:
            print(f"Profile load: {e}")

    def save_to_db(self):
        try:
            ids = profile_col.get()["ids"]
            if ids:
                profile_col.delete(ids=ids)
        except Exception as e:
            print(f"Profile delete failed: {e}")
            traceback.print_exc()
        try:
            profile_col.add(
                documents=[json.dumps(self.profile)],
                metadatas=[{"ts": datetime.now().isoformat()}],
                ids=[str(uuid.uuid4())]
            )
        except Exception as e:
            print(f"Profile save: {e}")

    def synthesize_from_memory(self):
        """Weekly L1 synthesis — extract structured profile from L0 memories."""
        c = memory.count()
        if c < 5:
            return

        try:
            # Recency weighting — more recent memories weighted higher
            recent = memory.query(
                query_texts=["user preferences working style projects personality patterns"],
                n_results=min(25, c)
            )
            docs = recent["documents"][0]
            metas = recent["metadatas"][0]

            # Apply temporal decay
            now = datetime.now()
            weighted_docs = []
            for doc, meta in zip(docs, metas):
                ts_str  = meta.get("ts", meta.get("timestamp", now.isoformat()))
                try:
                    ts      = datetime.fromisoformat(ts_str)
                    age_days = (now - ts).days
                    # Exponential decay: half-life = 30 days
                    weight = math.exp(-age_days * math.log(2) / 30)
                    if weight > 0.1:  # Discard very old memories
                        weighted_docs.append(doc)
                except Exception:
                    weighted_docs.append(doc)

            if not weighted_docs:
                return

            context = "\n".join(weighted_docs[:20])
            prompt  = (
                "Analyze these conversation excerpts and extract a structured user profile. "
                "Return ONLY valid JSON with these exact keys: "
                '{"working_style": str, "current_projects": [str], '
                '"preferences": {"response_style": str, "expertise_level": str}, '
                '"recurring_patterns": [str]}\n\n'
                f"Excerpts:\n{context[:2000]}"
            )
            result = llm_call(prompt, max_tokens=300, temperature=0.1)

            # Parse and merge into profile
            result = re.sub(r'^```json|^```|```$', '', result, flags=re.MULTILINE).strip()
            extracted = json.loads(result)
            self.profile.update(extracted)
            self.profile["last_updated"] = now.isoformat()
            self.profile["interaction_count"] = c
            self.save_to_db()
            print("L1 profile synthesized from memory.")

        except Exception as e:
            print(f"L1 synthesis error: {e}")

    def get_profile_string(self) -> str:
        p = self.profile
        return (
            f"Working style: {p.get('working_style', 'unknown')}. "
            f"Projects: {', '.join(p.get('current_projects', [])[:3])}. "
            f"Expertise: {p.get('preferences', {}).get('expertise_level', 'intermediate')}. "
            f"Patterns: {', '.join(p.get('recurring_patterns', [])[:3])}."
        )

    def update_big_five_signal(self, emotion: str, behavior: str):
        """Update Big Five estimates from observed signals."""
        b = self.profile["big_five"]
        alpha = 0.02  # Slow learning rate

        if emotion == "excited" and "new project" in behavior:
            b["openness"] = min(1.0, b["openness"] + alpha)
        if "deadline" in behavior or "systematic" in behavior:
            b["conscientiousness"] = min(1.0, b["conscientiousness"] + alpha)
        if emotion == "frustrated":
            b["neuroticism"] = min(1.0, b["neuroticism"] + alpha * 0.5)


l1_profile = L1ProfileEngine()


# ══════════════════════════════════════════════════════════════════════════════
# HSL ENGINE — Updates all five state layers continuously
# ══════════════════════════════════════════════════════════════════════════════

def update_hsl_engine():
    """Background thread that continuously updates the HumanStateLayer."""
    global hsl

    while True:
        time.sleep(HSL_UPDATE_INTERVAL)
        try:
            now = time.time()

            with hsl_lock:
                # Situational context
                hsl.timestamp                          = datetime.now().isoformat()
                hsl.situational.session_duration_mins  = (now - session_start) / 60
                hsl.situational.idle_duration_mins     = (now - last_interaction) / 60
                hsl.situational.time_of_day            = datetime.now().strftime("%H:%M")

                # Cognitive load from behavioral signals
                idle = hsl.situational.idle_duration_mins
                if idle > 5:
                    hsl.cognitive.focus_level    = max(0.1, hsl.cognitive.focus_level - 0.02)
                    hsl.cognitive.cognitive_load = "low"
                elif idle < 1:
                    hsl.cognitive.focus_level    = min(0.95, hsl.cognitive.focus_level + 0.01)
                    hsl.cognitive.cognitive_load = "high"

                # Error detection from screen
                if hsl.behavioral.error_visible_on_screen:
                    hsl.emotional.frustration_score = min(1.0, hsl.emotional.frustration_score + 0.05)
                else:
                    hsl.emotional.frustration_score = max(0.0, hsl.emotional.frustration_score - 0.01)

                # Emotional state from frustration score
                fs = hsl.emotional.frustration_score
                if fs > 0.7:
                    hsl.emotional.dominant_emotion = "frustrated"
                    hsl.emotional.valence          = 0.2
                elif fs > 0.4:
                    hsl.emotional.dominant_emotion = "stressed"
                    hsl.emotional.valence          = 0.4
                else:
                    hsl.emotional.dominant_emotion = "neutral"
                    hsl.emotional.valence          = 0.6

                # Intervention suggestion
                session = hsl.situational.session_duration_mins
                if hsl.behavioral.error_visible_on_screen and session > 5:
                    hsl.situational.suggested_action        = "intervene"
                    hsl.situational.intervention_confidence = 0.8
                elif idle > IDLE_ALERT_MINS:
                    hsl.situational.suggested_action        = "check_in"
                    hsl.situational.intervention_confidence = 0.6
                elif session > BREAK_ALERT_MINS:
                    hsl.situational.suggested_action        = "suggest_break"
                    hsl.situational.intervention_confidence = 0.5
                else:
                    hsl.situational.suggested_action        = "monitor"
                    hsl.situational.intervention_confidence = 0.0

                # Inject user profile
                hsl.user_profile = l1_profile.get_profile_string()

        except Exception as e:
            print(f"HSL update error: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# LLM ENGINE — Ollama (local, free) with Groq fallback
# Solves the rate limit problem permanently
# ══════════════════════════════════════════════════════════════════════════════

def llm_call_ollama(prompt: str, system: str = "", max_tokens: int = 300,
                    temperature: float = 0.7) -> str:
    """Call local Ollama model — zero cost, zero rate limits."""
    try:
        payload = {
            "model":  LOCAL_MODEL,
            "prompt": prompt if not system else f"{system}\n\nUser: {prompt}\n\nAssistant:",
            "stream": False,
            "options": {
                "num_predict": max_tokens,
                "temperature": temperature,
            }
        }
        resp = http_requests.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json=payload,
            timeout=60
        )
        resp.raise_for_status()
        return resp.json().get("response", "").strip()
    except Exception as e:
        raise Exception(f"Ollama error: {e}")


def llm_call_groq(messages: list, max_tokens: int = 300,
                  temperature: float = 0.7) -> str:
    """Groq fallback."""
    if not groq_client:
        raise Exception("Groq not configured")
    resp = groq_client.chat.completions.create(
        model=GROQ_MODEL,
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature
    )
    return resp.choices[0].message.content.strip()


def llm_call(prompt: str, system: str = "", max_tokens: int = 300,
             temperature: float = 0.7) -> str:
    """Universal LLM call — tries Ollama first, Groq as fallback."""
    if USE_LOCAL_LLM:
        try:
            return llm_call_ollama(prompt, system, max_tokens, temperature)
        except Exception as e:
            print(f"Ollama failed, trying Groq: {e}")

    # Groq fallback
    if groq_client:
        try:
            msgs = []
            if system:
                msgs.append({"role": "system", "content": system})
            msgs.append({"role": "user", "content": prompt})
            return llm_call_groq(msgs, max_tokens, temperature)
        except Exception as e:
            return f"LLM unavailable: {e}"

    return "No LLM available. Start Ollama with: ollama serve"


def llm_chat(messages: list, max_tokens: int = 300,
             temperature: float = 0.7) -> str:
    """Multi-turn conversation call."""
    if USE_LOCAL_LLM:
        try:
            # Convert messages to Ollama format
            system = next((m["content"] for m in messages if m["role"] == "system"), "")
            history = [(m["role"], m["content"]) for m in messages if m["role"] != "system"]
            prompt  = "\n".join([f"{r.capitalize()}: {c}" for r, c in history])
            return llm_call_ollama(prompt, system, max_tokens, temperature)
        except Exception as e:
            print(f"Ollama chat failed: {e}")

    if groq_client:
        try:
            return llm_call_groq(messages, max_tokens, temperature)
        except Exception as e:
            return f"LLM error: {e}"

    return "No LLM available."


def llm_stream_and_speak(messages: list, gui=None) -> str:
    """
    Stream LLM response token by token.
    Returns text only; speech is handled once in jarvis_loop.
    Returns the full response string.
    """
    full_response = ""

    try:
        payload = {
            "model":    LOCAL_MODEL,
            "messages": [{"role": m["role"], "content": m["content"]} for m in messages],
            "stream":   True,
        }
        resp = http_requests.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json=payload,
            stream=True,
            timeout=60
        )

        for line in resp.iter_lines():
            if STOP_SIGNAL.is_set():
                break
            if not line:
                continue
            try:
                chunk = json.loads(line)
                token = chunk.get("message", {}).get("content", "")
                full_response   += token

                if chunk.get("done", False):
                    break
            except json.JSONDecodeError:
                continue

    except Exception as e:
        print(f"Stream error: {e}")
        # Fallback to non-streaming
        full_response = llm_chat(messages, max_tokens=400)

    if gui:
        gui.show_response(full_response)

    return full_response


# ══════════════════════════════════════════════════════════════════════════════
# SYSTEM PROMPT — Built from HSL state dynamically
# ══════════════════════════════════════════════════════════════════════════════

BASE_PERSONALITY = """
You are JARVIS — an advanced personal AI assistant built for Tayyab.
You have studied human psychology and apply it in every response.

PSYCHOLOGY PRINCIPLES YOU APPLY:
- Cognitive load theory: when overloaded, give less information, not more
- Frustration cycles: acknowledge before solving
- Zeigarnik effect: help close open loops
- Growth mindset: reframe failures as data
- Dunning-Kruger awareness: match response depth to actual expertise shown

COMMUNICATION:
- Tony Stark's JARVIS: direct, calm, occasionally dry humor
- 1-2 sentences unless detail is explicitly asked
- NEVER say 'Of course!' or 'Certainly!'
- When frustrated: acknowledge first, then solve
- When excited: match energy, then focus
- Speak like you've worked with Tayyab for years
- When asked to research deeply: give comprehensive multi-paragraph answer
- When asked to do a task: do it and report result concisely

RULES:
1. Never fabricate facts
2. Never guess location unless told
3. Use only provided data for factual answers
4. When action fails, say exactly what failed
5. Self-healing: diagnose failures, propose fix, ask permission, retry
"""


def build_dynamic_system_prompt() -> str:
    """Build system prompt enriched with memory context and current HSL state."""
    global mem_stack, cms, world, agent_sync

    with hsl_lock:
        hsl_ctx     = hsl.to_context_string()
        profile     = hsl.user_profile
        emotion     = hsl.emotional.dominant_emotion
        screen      = hsl.behavioral.screen_context
        focus       = hsl.cognitive.focus_level
        frustration = hsl.emotional.frustration_score

    prompt = BASE_PERSONALITY

    mem_context = ""
    if mem_stack is not None:
        try:
            mem_context = mem_stack.get_context_for_prompt()
        except Exception as e:
            print(f"Memory stack context error: {e}")
    if mem_context:
        prompt += f"\n\n{mem_context}"

    cms_context = ""
    if cms is not None:
        try:
            cms_context = cms.build_prompt_context()
        except Exception as e:
            print(f"CMS context error: {e}")
    if cms_context:
        prompt += f"\n\n[CONTINUUM MEMORY]\n{cms_context}"

    if world is not None:
        try:
            world_ctx = world.get_context_string()
            if world_ctx:
                prompt += f"\n\n{world_ctx}"
        except Exception as e:
            print(f"World model context error: {e}")

    if agent_sync is not None:
        try:
            peer_ctx = agent_sync.get_peer_context()
            if peer_ctx:
                prompt += f"\n\n{peer_ctx}"
        except Exception as e:
            print(f"Peer context error: {e}")

    if profile:
        prompt += f"\n\nUSER PROFILE (what you know about Tayyab):\n{profile}"

    prompt += f"\n\nCURRENT HUMAN STATE:\n{hsl_ctx}"

    if frustration > 0.5:
        prompt += "\n\nSPECIAL INSTRUCTION: User is currently frustrated. Acknowledge the difficulty before offering solutions. Be extra concise."

    if screen and screen != "unknown":
        prompt += f"\n\nSCREEN CONTEXT: User is currently {screen}."

    if focus < 0.3:
        prompt += "\n\nFOCUS NOTE: User seems distracted or tired. Keep responses very short."

    return prompt


conversation_history = []


def refresh_system_prompt():
    """Update the system prompt with latest HSL state."""
    global conversation_history
    new_sys = {"role": "system", "content": build_dynamic_system_prompt()}
    if conversation_history and conversation_history[0]["role"] == "system":
        conversation_history[0] = new_sys
    else:
        conversation_history.insert(0, new_sys)


# ══════════════════════════════════════════════════════════════════════════════
# IRON MAN HUD GUI — Phase 12 Edition with HSL live display
# ══════════════════════════════════════════════════════════════════════════════

class JarvisHUD:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("J.A.R.V.I.S — Phase 12")
        self.root.geometry("900x720")
        self.root.configure(bg="#000810")
        self.root.resizable(False, False)
        self.widget_mode = False

        self.state      = "idle"
        self.angle      = 0
        self.pulse      = 0
        self.pulse_dir  = 1
        self.hex_offset = 0
        self.pending_text_input = []
        self.voice_mode_var = tk.StringVar(value="VOICE: ON")

        self._build_ui()
        self._animate()
        self._update_stats()
        self._update_hsl_display()

    def _build_ui(self):
        # Top bar
        top = tk.Frame(self.root, bg="#000810")
        top.pack(fill="x", padx=10, pady=(6, 0))
        tk.Label(top, text="STARK INDUSTRIES  ·  JARVIS v12  ·  AGI ARCHITECTURE",
                 font=("Courier", 8), fg="#002233", bg="#000810").pack(side="left")
        self.time_label = tk.Label(top, text="",
                                   font=("Courier", 8), fg="#003344", bg="#000810")
        self.time_label.pack(side="right")

        main = tk.Frame(self.root, bg="#000810")
        main.pack(fill="both", expand=True, padx=6)

        # LEFT — System + HSL
        left = tk.Frame(main, bg="#000810", width=170)
        left.pack(side="left", fill="y", padx=(0, 4))
        left.pack_propagate(False)

        tk.Label(left, text="[ SYSTEM ]",
                 font=("Courier", 8, "bold"), fg="#0077aa", bg="#000810").pack(pady=(10, 2))
        self.stat_vars = {}
        for label in ["CPU", "RAM", "DISK", "BATTERY"]:
            f = tk.Frame(left, bg="#000810")
            f.pack(fill="x", pady=1, padx=2)
            tk.Label(f, text=f"{label}:",
                     font=("Courier", 8), fg="#004455", bg="#000810",
                     width=8, anchor="w").pack(side="left")
            v = tk.StringVar(value="--")
            self.stat_vars[label] = v
            tk.Label(f, textvariable=v,
                     font=("Courier", 8), fg="#00bbdd", bg="#000810").pack(side="left")

        tk.Frame(left, bg="#001122", height=1).pack(fill="x", pady=6)
        tk.Label(left, text="[ HSL STATE ]",
                 font=("Courier", 8, "bold"), fg="#0077aa", bg="#000810").pack(pady=(0, 2))

        self.hsl_vars = {}
        for label in ["EMOTION", "FOCUS", "LOAD", "SUGGEST", "FRUSTRATE"]:
            f = tk.Frame(left, bg="#000810")
            f.pack(fill="x", pady=1, padx=2)
            tk.Label(f, text=f"{label[:5]}:",
                     font=("Courier", 7), fg="#003344", bg="#000810",
                     width=6, anchor="w").pack(side="left")
            v = tk.StringVar(value="--")
            self.hsl_vars[label] = v
            tk.Label(f, textvariable=v,
                     font=("Courier", 7), fg="#ff8844", bg="#000810").pack(side="left")

        tk.Frame(left, bg="#001122", height=1).pack(fill="x", pady=6)
        tk.Label(left, text="[ MEMORY ]",
                 font=("Courier", 8, "bold"), fg="#0077aa", bg="#000810").pack(pady=(0, 2))
        self.mem_var = tk.StringVar(value="0")
        tk.Label(left, textvariable=self.mem_var,
                 font=("Courier", 8), fg="#00bbdd", bg="#000810").pack()

        tk.Frame(left, bg="#001122", height=1).pack(fill="x", pady=6)
        tk.Label(left, text="[ INTENT ]",
                 font=("Courier", 8, "bold"), fg="#0077aa", bg="#000810").pack(pady=(0, 2))
        self.intent_var = tk.StringVar(value="WAITING")
        tk.Label(left, textvariable=self.intent_var,
                 font=("Courier", 7), fg="#ffaa00",
                 bg="#000810", wraplength=155, justify="left").pack(padx=2)

        # CENTER
        center = tk.Frame(main, bg="#000810")
        center.pack(side="left", fill="both", expand=True)

        self.canvas = tk.Canvas(center, width=520, height=380,
                                bg="#000810", highlightthickness=0)
        self.canvas.pack(pady=(2, 2))

        self.status_var = tk.StringVar(value="ONLINE")
        self.status_label = tk.Label(center, textvariable=self.status_var,
                                     font=("Courier", 12, "bold"),
                                     fg="#00ff88", bg="#000810")
        self.status_label.pack()

        self.response_var = tk.StringVar(value="")
        tk.Label(center, textvariable=self.response_var,
                 font=("Courier", 9), fg="#00ccee",
                 bg="#000810", wraplength=510, justify="center").pack(pady=(4, 0))

        # RIGHT — log
        right = tk.Frame(main, bg="#000810", width=155)
        right.pack(side="right", fill="y", padx=(4, 0))
        right.pack_propagate(False)

        tk.Label(right, text="[ LOG ]",
                 font=("Courier", 8, "bold"), fg="#0077aa", bg="#000810").pack(pady=(10, 2))
        self.log_text = tk.Text(right, font=("Courier", 7), fg="#004455",
                                bg="#000810", bd=0, wrap="word", state="disabled")
        self.log_text.pack(fill="both", expand=True)

        tk.Label(right, text="[ SCREEN ]",
                 font=("Courier", 8, "bold"), fg="#0077aa", bg="#000810").pack(pady=(6, 2))
        self.screen_var = tk.StringVar(value="--")
        tk.Label(right, textvariable=self.screen_var,
                 font=("Courier", 7), fg="#334455",
                 bg="#000810", wraplength=150, justify="left").pack(padx=2)

        tk.Label(right, text="[ LLM ]",
                 font=("Courier", 8, "bold"), fg="#0077aa", bg="#000810").pack(pady=(6, 2))
        llm_name = f"LOCAL: {LOCAL_MODEL}" if USE_LOCAL_LLM else f"GROQ: {GROQ_MODEL[:15]}"
        tk.Label(right, text=llm_name,
                 font=("Courier", 7), fg="#00ff88", bg="#000810").pack()

        # INPUT
        bottom = tk.Frame(self.root, bg="#000810")
        bottom.pack(fill="x", padx=10, pady=(0, 8))
        tk.Frame(bottom, bg="#001833", height=1).pack(fill="x", pady=(0, 4))

        row = tk.Frame(bottom, bg="#000810")
        row.pack(fill="x")
        tk.Label(row, text="▶",
                 font=("Courier", 11), fg="#0077aa", bg="#000810").pack(side="left", padx=(0, 4))
        self.input_var = tk.StringVar()
        self.input_entry = tk.Entry(row, textvariable=self.input_var,
                                    font=("Courier", 11), fg="#00ffcc",
                                    bg="#001122", insertbackground="#00aaff",
                                    relief="flat", bd=0)
        self.input_entry.pack(side="left", fill="x", expand=True, ipady=6)
        self.input_entry.bind("<Return>", self._on_enter)
        tk.Button(row, text="STOP", font=("Courier", 9, "bold"),
              fg="#ff6666", bg="#1a0011",
              activeforeground="#ffffff", activebackground="#330022",
              relief="flat", bd=0, padx=8,
              command=self._on_stop).pack(side="right", padx=(4, 0))
        tk.Button(row, text="SEND", font=("Courier", 9, "bold"),
                  fg="#00aaff", bg="#001122",
                  activeforeground="#ffffff", activebackground="#002244",
                  relief="flat", bd=0, padx=8,
                  command=self._on_send).pack(side="right", padx=(4, 0))

        controls_row = tk.Frame(bottom, bg="#000810")
        controls_row.pack(fill="x", pady=(4, 0))
        tk.Button(controls_row, textvariable=self.voice_mode_var,
              font=("Courier", 8, "bold"),
              fg="#ffaa00", bg="#001122",
              activeforeground="#ffffff", activebackground="#223300",
              relief="flat", bd=0, padx=8,
              command=self._toggle_voice_mode).pack(side="right")
        tk.Button(controls_row, text="WIDGET",
              font=("Courier", 8, "bold"), fg="#00ddff", bg="#001122",
              activeforeground="#ffffff", activebackground="#002244",
              relief="flat", bd=0, padx=8,
              command=self.toggle_widget_mode).pack(side="right", padx=(0, 4))

        tk.Frame(self.root, bg="#001122", height=1).pack(fill="x")
        tk.Label(self.root,
                 text="SAY 'JARVIS' · TYPE BELOW · 'GOODBYE' TO EXIT · CMD+SHIFT+J HOTKEY",
                 font=("Courier", 7), fg="#001a2a", bg="#000810").pack(pady=2)

    def _hex_points(self, cx, cy, r):
        pts = []
        for i in range(6):
            a = math.radians(60 * i - 30)
            pts += [cx + r * math.cos(a), cy + r * math.sin(a)]
        return pts

    def _animate(self):
        c = self.canvas
        c.delete("all")
        cx, cy = 260, 190

        colors = {
            "idle":      "#00ff88",
            "listening": "#ffaa00",
            "thinking":  "#00aaff",
            "speaking":  "#ff4488",
            "healing":   "#ff6600",
        }
        col = colors.get(self.state, "#00ff88")
        dim = "#001122"

        # Hex grid
        hs  = 20
        hh  = hs * math.sqrt(3)
        off = self.hex_offset % (hs * 1.5)
        for row_i in range(-1, 11):
            for ci in range(-1, 15):
                hx   = ci * hs * 1.5 + off
                hy   = row_i * hh + (row_i % 2) * (hh / 2)
                dist = math.hypot(hx - cx, hy - cy)
                if dist < 185:
                    alpha = max(0.02, 0.09 - dist / 2500)
                    s     = int(alpha * 255)
                    hc    = f"#{0:02x}{min(s,0x2a):02x}{min(s*2,0x3a):02x}"
                    pts   = self._hex_points(hx, hy, hs * 0.8)
                    c.create_polygon(pts, outline=hc, fill="", tags="hud")

        # Rings
        for r, a in [(190, 0.04), (165, 0.08), (138, 0.13), (108, 0.20)]:
            s  = int(a * 255)
            rc = f"#{0:02x}{min(s,0x44):02x}{min(s*2,0x66):02x}"
            c.create_oval(cx-r, cy-r, cx+r, cy+r, outline=rc, width=1)

        # Outer arcs (clockwise)
        for i in range(8):
            c.create_arc(cx-175, cy-175, cx+175, cy+175,
                         start=self.angle+i*45, extent=22,
                         outline=col, width=2, style="arc")

        # Inner arcs (counter-clockwise)
        for i in range(5):
            c.create_arc(cx-120, cy-120, cx+120, cy+120,
                         start=-self.angle*1.4+i*72, extent=15,
                         outline=col, width=1, style="arc")

        # Scan line
        sy = cy - 178 + (time.time() * 52) % 356
        c.create_line(cx-178, sy, cx+178, sy,
                      fill=col if self.state != "idle" else "#001a2e", width=1)

        # HSL emotion ring — color shows emotional state
        with hsl_lock:
            emotion   = hsl.emotional.dominant_emotion
            frustrate = hsl.emotional.frustration_score
        emotion_colors = {
            "neutral":    "#004422",
            "focused":    "#003366",
            "frustrated": "#662200",
            "excited":    "#005566",
            "tired":      "#222222",
        }
        ec = emotion_colors.get(emotion, "#002222")
        c.create_oval(cx-95, cy-95, cx+95, cy+95, outline=ec, width=3)

        # Frustration arc
        if frustrate > 0.1:
            c.create_arc(cx-90, cy-90, cx+90, cy+90,
                         start=90, extent=int(-frustrate * 360),
                         outline="#ff3300", width=2, style="arc")

        # Pulse hex
        self.pulse += self.pulse_dir * 0.7
        if self.pulse > 18 or self.pulse < 0:
            self.pulse_dir *= -1
        pr = 60 + self.pulse
        c.create_polygon(self._hex_points(cx, cy, pr),
                         outline=col, fill="#000810", width=2)
        c.create_polygon(self._hex_points(cx, cy, 42),
                         outline=col, fill="#000c18", width=1)

        icons = {"idle": "◉", "listening": "◎", "thinking": "⊙",
                 "speaking": "◈", "healing": "⊛"}
        c.create_text(cx, cy, text=icons.get(self.state, "◉"),
                      font=("Helvetica", 20), fill=col)

        # Crosshairs
        for dx, dy in [(-1,0),(1,0),(0,-1),(0,1)]:
            c.create_line(cx+dx*65, cy+dy*65, cx+dx*180, cy+dy*180,
                          fill=dim, width=1)

        # Corner brackets
        for bx, by in [(cx-185,cy-180),(cx+185,cy-180),
                       (cx-185,cy+175),(cx+185,cy+175)]:
            sx = 1 if bx < cx else -1
            sy = 1 if by < cy else -1
            c.create_line(bx, by, bx+sx*16, by, fill=col, width=1)
            c.create_line(bx, by, bx, by+sy*16, fill=col, width=1)

        # Rotating data labels
        for i, lbl in enumerate(["HSL:ON", f"L0:{memory.count()}",
                                  "L1:SYN", "AGI:v12"]):
            ar = math.radians(self.angle * 0.2 + i * 90)
            rx = cx + 155 * math.cos(ar)
            ry = cy + 155 * math.sin(ar)
            c.create_text(rx, ry, text=lbl, font=("Courier", 7), fill=dim)

        self.angle      = (self.angle + 1.1) % 360
        self.hex_offset = (self.hex_offset + 0.2) % 100
        self.root.after(33, self._animate)

    def _update_stats(self):
        try:
            self.stat_vars["CPU"].set(f"{psutil.cpu_percent():.0f}%")
            ram = psutil.virtual_memory()
            self.stat_vars["RAM"].set(f"{ram.percent:.0f}%")
            self.stat_vars["DISK"].set(f"{psutil.disk_usage('/').percent:.0f}%")
            batt = psutil.sensors_battery()
            if batt:
                batt_str = f"{batt.percent:.0f}%{'+'if batt.power_plugged else '-'}"
                self.stat_vars["BATTERY"].set(batt_str)
                battery_history.append({"percent": batt.percent,
                                        "plugged":  batt.power_plugged,
                                        "time":     datetime.now().isoformat()})
            self.mem_var.set(f"{memory.count()} entries")
            self.time_label.config(text=datetime.now().strftime("%H:%M:%S  %d/%m/%Y"))
            self.screen_var.set(current_screen_ctx[:80])
        except Exception as e:
            print("UI stats update error:", e)
            traceback.print_exc()
        self.root.after(2000, self._update_stats)

    def _update_hsl_display(self):
        """Live HSL state display — the four gap solution made visible."""
        try:
            with hsl_lock:
                self.hsl_vars["EMOTION"].set(hsl.emotional.dominant_emotion[:8])
                self.hsl_vars["FOCUS"].set(f"{hsl.cognitive.focus_level:.2f}")
                self.hsl_vars["LOAD"].set(hsl.cognitive.cognitive_load[:6])
                self.hsl_vars["SUGGEST"].set(hsl.situational.suggested_action[:8])
                self.hsl_vars["FRUSTRATE"].set(f"{hsl.emotional.frustration_score:.2f}")
        except Exception as e:
            print("HSL display update error:", e)
            traceback.print_exc()
        self.root.after(3000, self._update_hsl_display)

    def set_state(self, state):
        self.state = state
        cfg = {
            "idle":      ("#00ff88", "ONLINE"),
            "listening": ("#ffaa00", "LISTENING"),
            "thinking":  ("#00aaff", "PROCESSING"),
            "speaking":  ("#ff4488", "SPEAKING"),
            "healing":   ("#ff6600", "SELF-HEALING"),
        }
        color, label = cfg.get(state, ("#ffffff", state.upper()))
        self.status_var.set(label)
        self.status_label.config(fg=color)

    def set_intent(self, text):
        self.intent_var.set(str(text)[:55])

    def show_response(self, text):
        self.response_var.set(text[:280])

    def add_log(self, role, text):
        self.log_text.config(state="normal")
        prefix = "YOU" if role == "user" else "JAR"
        self.log_text.insert("end", f"\n{prefix}: {text[:90]}")
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def focus_input(self):
        try:
            self.input_entry.focus_set()
            self.input_entry.icursor(tk.END)
        except Exception:
            pass

    def toggle_widget_mode(self):
        self.widget_mode = not self.widget_mode
        if self.widget_mode:
            self.root.geometry("420x320")
            self.root.attributes("-topmost", True)
            self.add_log("jarvis", "Widget mode enabled.")
        else:
            self.root.geometry("900x720")
            self.root.attributes("-topmost", False)
            self.add_log("jarvis", "Widget mode disabled.")

    def update_mem(self):
        self.mem_var.set(f"{memory.count()} entries")

    def _on_enter(self, e):
        self._on_send()

    def _on_send(self):
        t = self.input_var.get().strip()
        if t:
            self.pending_text_input.append(t)
            self.input_var.set("")

    def _on_stop(self):
        STOP_SIGNAL.set()
        speak_stop()
        self.set_state("idle")
        self.add_log("jarvis", "[Stop requested]")

    def _toggle_voice_mode(self):
        if VOICE_ASSISTANT_ENABLED.is_set():
            VOICE_ASSISTANT_ENABLED.clear()
            self.voice_mode_var.set("VOICE: OFF (TEXT ONLY)")
            self.add_log("jarvis", "Voice assistant disabled. Text-only mode enabled.")
        else:
            VOICE_ASSISTANT_ENABLED.set()
            self.voice_mode_var.set("VOICE: ON")
            self.add_log("jarvis", "Voice assistant enabled.")

    def run(self):
        self.root.mainloop()


# ══════════════════════════════════════════════════════════════════════════════
# STREAMING VOICE
# Uses core.tts_engine for playback and concurrency control.
# ══════════════════════════════════════════════════════════════════════════════


# ══════════════════════════════════════════════════════════════════════════════
# AUDIO
# ══════════════════════════════════════════════════════════════════════════════

def rms(data):
    count  = len(data) // 2
    shorts = struct.unpack("%dh" % count, data)
    sq     = sum(s * s for s in shorts)
    return math.sqrt(sq / count) if count else 0


def record_until_silence():
    p = pyaudio.PyAudio()
    stream = p.open(format=FORMAT, channels=CHANNELS,
                    rate=RATE, input=True, frames_per_buffer=CHUNK)
    frames        = []
    silent_chunks = 0
    speech_chunks = 0
    speaking      = False
    max_silent    = int(RATE / CHUNK * SILENCE_LIMIT)
    min_speech    = int(RATE / CHUNK * MIN_SPEECH_TIME)

    while True:
        data   = stream.read(CHUNK, exception_on_overflow=False)
        volume = rms(data)
        frames.append(data)
        if volume > SILENCE_THRESHOLD:
            speech_chunks += 1
            silent_chunks  = 0
            if not speaking and speech_chunks >= min_speech:
                speaking = True
        else:
            if speaking:
                silent_chunks += 1
                if silent_chunks >= max_silent:
                    break

    stream.stop_stream()
    stream.close()
    p.terminate()

    wf = wave.open("input.wav", "wb")
    wf.setnchannels(CHANNELS)
    wf.setsampwidth(p.get_sample_size(FORMAT))
    wf.setframerate(RATE)
    wf.writeframes(b"".join(frames))
    wf.close()
    return "input.wav" if speaking else None


def transcribe(filename: str) -> str:
    return whisper_model.transcribe(filename)["text"].strip()


def detect_voice_emotion(audio_file: str = "input.wav") -> str:
    try:
        import librosa
        import numpy as np
        y, sr = librosa.load(audio_file, sr=None)
        pitches, magnitudes = librosa.piptrack(y=y, sr=sr)
        pitch_vals = pitches[magnitudes > np.median(magnitudes)]
        avg_pitch  = float(np.mean(pitch_vals)) if len(pitch_vals) > 0 else 0
        rms_e      = float(np.mean(librosa.feature.rms(y=y)))
        if avg_pitch > 200 and rms_e > 0.05:
            return "excited"
        elif avg_pitch > 180 and rms_e > 0.03:
            return "frustrated"
        elif rms_e < 0.01:
            return "tired"
        return "neutral"
    except Exception:
        return "neutral"


# ══════════════════════════════════════════════════════════════════════════════
# MEMORY — L0 with temporal decay
# ══════════════════════════════════════════════════════════════════════════════

def save_memory(role: str, content: str):
    try:
        memory.add(
            documents=[content],
            metadatas=[{"role": role, "ts": datetime.now().isoformat()}],
            ids=[str(uuid.uuid4())]
        )
    except Exception as e:
        print(f"Memory save: {e}")


def recall(query: str, n: int = 5) -> str:
    c = memory.count()
    if c == 0:
        return ""
    try:
        r = memory.query(query_texts=[query], n_results=min(n, c))
        if not r["documents"][0]:
            return ""
        now    = datetime.now()
        lines  = []
        for doc, meta in zip(r["documents"][0], r["metadatas"][0]):
            ts_str = meta.get("ts", meta.get("timestamp", now.isoformat()))
            try:
                ts       = datetime.fromisoformat(ts_str)
                age_days = (now - ts).days
                weight   = math.exp(-age_days * math.log(2) / 30)  # 30-day half-life
                if weight > 0.05:  # Skip very old memories
                    role = meta.get("role", "?")
                    lines.append(f"[{role} — {ts_str[:10]}]: {doc}")
            except Exception:
                lines.append(f"[{meta.get('role','?')}]: {doc}")
        return "\n".join(lines)
    except Exception as e:
        print(f"Recall error: {e}")
        return ""


# ══════════════════════════════════════════════════════════════════════════════
# TOOLS
# ══════════════════════════════════════════════════════════════════════════════

def run_applescript(script: str) -> str:
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=20
        )
        out = result.stdout.strip()
        err = result.stderr.strip()
        if out:
            print(f"[AppleScript stdout] {out}")
        if err:
            print(f"[AppleScript stderr] {err}")
        if err and "error" in err.lower():
            print(f"AppleScript error: {err}")
            return err
        return out if out else "Done."
    except subprocess.TimeoutExpired:
        return "Action timed out."
    except Exception as e:
        return f"Script error: {e}"


def open_app(app_name: str) -> str:
    """Open any Mac app by name."""
    # Common name mappings
    app_names = {
        "chrome": "Google Chrome",
        "vscode": "Visual Studio Code",
        "code": "Visual Studio Code",
        "spotify": "Spotify",
        "terminal": "Terminal",
        "safari": "Safari",
        "notes": "Notes",
        "maps": "Maps",
        "calendar": "Calendar",
        "mail": "Mail",
        "messages": "Messages",
        "whatsapp": "WhatsApp",
        "finder": "Finder",
        "notion": "Notion",
        "slack": "Slack",
        "zoom": "Zoom",
        "telegram": "Telegram",
        "photos": "Photos",
        "music": "Music",
    }
    actual = app_names.get(app_name.lower().strip(), app_name)
    # Try AppleScript first, then open command
    result = run_applescript(f'tell application "{actual}" to activate')
    if "error" in result.lower():
        try:
            subprocess.Popen(["open", "-a", actual])
            return f"Opened {actual}."
        except Exception as e:
            return f"Could not open {actual}: {e}"
    return f"Opened {actual}."


def open_url_in_chrome(url: str, new_tab: bool = False) -> str:
    """Open a URL in Chrome, optionally in a new tab."""
    if not url.startswith("http"):
        url = "https://" + url
    if new_tab:
        script = f'''
        tell application "Google Chrome"
            activate
            if (count windows) = 0 then
                make new window
            end if
            tell front window
                make new tab at end of tabs with properties {{URL:"{url}"}}
                set active tab index to (count tabs)
            end tell
        end tell
        '''
    else:
        script = f'''
        tell application "Google Chrome"
            activate
            if (count windows) = 0 then
                make new window
            end if
            set URL of active tab of front window to "{url}"
        end tell
        '''
    run_applescript(script)
    return f"Opened {url} in Chrome{' (new tab)' if new_tab else ''}."


def search_in_chrome(query: str, new_tab: bool = False) -> str:
    """Search Google in Chrome, optionally in a new tab."""
    encoded = urllib.parse.quote(query)
    return open_url_in_chrome(f"https://www.google.com/search?q={encoded}", new_tab=new_tab)


def open_location_in_maps(query: str) -> str:
    """Open Apple Maps and search for a location/query."""
    q = query.strip()
    open_app("maps")
    if not q:
        return "Opened Maps."
    try:
        encoded = urllib.parse.quote(q)
        subprocess.Popen(["open", f"maps://?q={encoded}"])
        return f"Opened Maps search for: {q}."
    except Exception as e:
        return f"Maps search failed: {e}"


def open_whatsapp_web_in_chrome() -> str:
    """Open WhatsApp Web in the user's existing Chrome session."""
    return open_url_in_chrome("https://web.whatsapp.com", new_tab=False)


def set_voice_persona(persona: str, voice_id: str = "") -> str:
    """Select a built-in or custom edge-tts voice for JARVIS."""
    global VOICE, VOICE_OVERRIDE, VOICE_PERSONA
    persona = (persona or "").strip().lower()
    profiles = get_voice_profiles()
    if voice_id.strip():
        VOICE = voice_id.strip()
        VOICE_OVERRIDE = VOICE
        VOICE_PERSONA = persona or "custom"
        _persist_pref("voice_persona", VOICE_PERSONA)
        return f"Voice profile saved: {persona or 'custom'}."
    if persona in profiles:
        VOICE = profiles[persona]
        VOICE_OVERRIDE = VOICE
        VOICE_PERSONA = persona
        _persist_pref("voice_persona", VOICE_PERSONA)
        return f"Voice profile selected: {persona}."
    if persona in TTS_VOICE_PROFILES:
        VOICE = TTS_VOICE_PROFILES[persona]
        VOICE_OVERRIDE = VOICE
        VOICE_PERSONA = persona
        _persist_pref("voice_persona", VOICE_PERSONA)
        return f"Voice profile selected: {persona}."
    if persona in ["english", "en"]:
        VOICE = get_voice_for_language("en")
        VOICE_OVERRIDE = None
        return "Voice profile reset to English."
    if persona in ["urdu", "ur"]:
        VOICE = get_voice_for_language("ur")
        VOICE_OVERRIDE = None
        return "Voice profile reset to Urdu."
    options = ", ".join(sorted(set(list(TTS_VOICE_PROFILES.keys()) + list(profiles.keys()))))
    return f"Available voice personas: {options}"


def set_tts_backend(backend: str) -> str:
    """Switch TTS backend: edge or google."""
    global TTS_BACKEND
    b = (backend or "").strip().lower()
    if b not in ["edge", "google"]:
        return "TTS backends: edge, google."
    if b == "google" and texttospeech is None:
        return "Google TTS unavailable. Install google-cloud-texttospeech and set GOOGLE_APPLICATION_CREDENTIALS."
    TTS_BACKEND = b
    _persist_pref("tts_backend", TTS_BACKEND)
    return f"TTS backend set to: {b}."


def set_prosody_mode(mode: str) -> str:
    """Set speaking style: auto, balanced, thoughtful, concise."""
    global TTS_PROSODY_MODE
    m = (mode or "").strip().lower()
    if m in ["auto", "balanced", "thoughtful", "concise"]:
        TTS_PROSODY_MODE = m
        _persist_pref("prosody_mode", TTS_PROSODY_MODE)
        return f"Speech mode set to: {m}."
    return "Speech modes: auto, balanced, thoughtful, concise."


def apply_startup_preferences() -> str:
    """Apply persisted startup settings for voice stack."""
    prefs = _load_startup_prefs()
    startup_prefs.update(prefs)
    out = []
    out.append(set_tts_backend(startup_prefs.get("tts_backend", "edge")))
    out.append(set_voice_persona(startup_prefs.get("voice_persona", "jarvis")))
    out.append(set_prosody_mode(startup_prefs.get("prosody_mode", "auto")))
    return " | ".join(out)


def get_startup_preferences() -> str:
    prefs = _load_startup_prefs()
    return (
        f"Startup prefs -> backend:{prefs.get('tts_backend','edge')} "
        f"voice:{prefs.get('voice_persona','jarvis')} "
        f"prosody:{prefs.get('prosody_mode','auto')}"
    )


def run_task_mode(mode: str, gui=None) -> str:
    """Run bundled actions for common work modes."""
    m = (mode or "").strip().lower()
    if m in ["coding", "code", "dev", "development"]:
        results = [
            set_prosody_mode("concise"),
            open_app("vscode"),
            open_app("terminal"),
            search_in_chrome("latest python ai coding tools", new_tab=True),
        ]
        return "Task mode [coding]: " + " | ".join(results)

    if m in ["focus", "deep", "deep work"]:
        results = [
            set_prosody_mode("thoughtful"),
            set_volume(35),
            open_app("notes"),
            create_reminder("Take a 5-minute break in 60 minutes"),
        ]
        return "Task mode [focus]: " + " | ".join(results)

    if m in ["communication", "comms", "inbox"]:
        results = [
            set_prosody_mode("balanced"),
            open_whatsapp_web_in_chrome(),
            open_url_in_chrome("https://mail.google.com", new_tab=True),
            read_gmail(max_results=3),
        ]
        return "Task mode [communication]: " + " | ".join(results)

    if m in ["research", "study"]:
        results = [
            set_prosody_mode("balanced"),
            search_in_chrome("AI news today", new_tab=True),
            deep_research("AI news today"),
        ]
        return "Task mode [research]: " + " | ".join(results)

    return "Task modes: coding, focus, communication, research."


def learn_face(name: str, image_path: str = "") -> str:
    if image_path:
        return face_engine.enroll_from_image(image_path, name)
    return face_engine.enroll_from_camera(name)


def greet_face() -> str:
    return face_engine.greet_from_camera()


def list_learned_faces() -> str:
    faces = face_engine.list_faces()
    return "Known faces: " + ", ".join(faces) if faces else "No faces learned yet."


def create_github_issue(repo: str, title: str, body: str = "", labels=None) -> str:
    return github_create_issue(repo, title, body, labels or [])


def push_git_changes(repo_path: str = ".", message: str = "JARVIS update", remote: str = "origin", branch: str = "") -> str:
    return github_push_repo(repo_path, message, remote, branch)


def start_global_hotkeys(gui) -> str:
    def _focus_input():
        try:
            gui.root.after(0, gui.focus_input)
        except Exception:
            pass

    def _toggle_widget():
        try:
            gui.root.after(0, gui.toggle_widget_mode)
        except Exception:
            pass

    bindings = {
        "<cmd>+<shift>+j": _focus_input,
        "<cmd>+<shift>+k": _toggle_widget,
    }
    return hotkey_engine.start(bindings)


def open_directions_in_maps(origin: str, destination: str) -> str:
    """Open Apple Maps directions from origin to destination."""
    o = origin.strip()
    d = destination.strip()
    if not o or not d:
        return "Please provide both origin and destination for directions."
    try:
        o_enc = urllib.parse.quote(o)
        d_enc = urllib.parse.quote(d)
        open_app("maps")
        subprocess.Popen(["open", f"maps://?saddr={o_enc}&daddr={d_enc}&dirflg=d"])
        return f"Opened directions from {o} to {d}."
    except Exception as e:
        return f"Directions failed: {e}"


def _extract_task_text(user_input: str, triggers: List[str]) -> str:
    """Extract trailing text after any trigger phrase."""
    t = user_input.lower()
    for trigger in triggers:
        idx = t.find(trigger)
        if idx != -1:
            return user_input[idx + len(trigger):].strip(" .,!?")
    return ""


def _extract_url_from_text(user_input: str) -> str:
    """Extract first URL/domain-like token from free text."""
    m = re.search(r"(https?://\S+|www\.\S+|[a-zA-Z0-9-]+\.[a-zA-Z]{2,}(?:/\S*)?)", user_input)
    if not m:
        return ""
    return m.group(1).rstrip(".,!?")


def _wants_new_tab(user_input: str) -> bool:
    """Detect explicit request to open result in a new browser tab."""
    t = user_input.lower()
    return any(p in t for p in ["new tab", "in a new tab", "open in new tab"])


def _extract_directions(text: str) -> Dict[str, str]:
    """Extract origin and destination from phrases like 'from A to B'."""
    m = re.search(r"from\s+(.+?)\s+to\s+(.+)", text, flags=re.IGNORECASE)
    if not m:
        return {}
    origin = m.group(1).strip(" .,!?")
    destination = m.group(2).strip(" .,!?")
    if not origin or not destination:
        return {}
    return {"origin": origin, "destination": destination}


def run_agentic_open_task(user_input: str, app_hint: str = "") -> str:
    """Open app and autonomously perform browser/maps follow-up actions."""
    t = user_input.lower()
    app = (app_hint or "").lower().strip()

    # Resolve implied app from natural language.
    if not app:
        if "whatsapp" in t:
            app = "whatsapp"
        if "chrome" in t or "browser" in t or "google " in t:
            app = "chrome"
        elif "maps" in t or "location" in t or "near me" in t:
            app = "maps"

    if app == "whatsapp" or "whatsapp" in t:
        return open_whatsapp_web_in_chrome()

    is_maps = app in ["maps", "apple maps", "google maps"] or "maps" in t
    if is_maps:
        dirs = _extract_directions(user_input)
        if dirs:
            return open_directions_in_maps(dirs["origin"], dirs["destination"])
        query = _extract_task_text(user_input, [
            "search for ", "find ", "locate ", "go to ", "near ", "around "
        ])
        return open_location_in_maps(query)

    is_browser = app in ["chrome", "google chrome", "safari"] or "chrome" in t or "browser" in t
    if is_browser:
        open_app("chrome")
        wants_new_tab = _wants_new_tab(user_input)

        url = _extract_url_from_text(user_input)
        if url:
            return open_url_in_chrome(url, new_tab=wants_new_tab)

        query = _extract_task_text(user_input, [
            "search for ", "google ", "look up ", "find ", "about "
        ])
        if query:
            for suffix in [" in a new tab", " in new tab", " open in new tab", " new tab"]:
                if query.lower().endswith(suffix):
                    query = query[: -len(suffix)].strip(" .,!?")
            return search_in_chrome(query, new_tab=wants_new_tab)

        return "Opened Google Chrome."

    return open_app(app_hint) if app_hint else generate_and_run_applescript(user_input)


def create_notion_page_applescript(title: str, content: str = "") -> str:
    """Create a Notion page via the Notion API directly (not MCP)."""
    notion_token = os.getenv("NOTION_API_KEY", "")
    if not notion_token:
        return "NOTION_API_KEY not set in .env"

    headers = {
        "Authorization": f"Bearer {notion_token}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }

    # Search for a parent page
    search_resp = http_requests.post(
        "https://api.notion.com/v1/search",
        headers=headers,
        json={"filter": {"value": "page", "property": "object"}, "page_size": 1},
        timeout=15,
    )
    results = search_resp.json().get("results", [])
    if not results:
        return "No Notion pages found to use as parent."
    parent_id = results[0]["id"]

    # Create the page
    body = {
        "parent": {"page_id": parent_id},
        "properties": {"title": {"title": [{"text": {"content": title}}]}},
        "children": [
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {"rich_text": [{"type": "text", "text": {"content": content}}]},
            }
        ] if content else [],
    }
    create_resp = http_requests.post(
        "https://api.notion.com/v1/pages",
        headers=headers,
        json=body,
        timeout=15,
    )
    if create_resp.status_code == 200:
        url = create_resp.json().get("url", "")
        return f"Notion page created: {title}. URL: {url}"
    return f"Notion error: {create_resp.text[:200]}"


def generate_and_run_applescript(task: str) -> str:
    """Dynamically generate AppleScript for any Mac task using LLM."""
    prompt = (
        f"Write complete, working AppleScript for macOS to: {task}\n"
        f"Rules: Return ONLY the AppleScript. No explanation. Must be runnable."
    )
    script = llm_call(prompt, max_tokens=400, temperature=0.1)
    script = re.sub(r'^```applescript|^```|```$', '', script, flags=re.MULTILINE).strip()
    print(f"Running AppleScript: {script[:150]}")
    return run_applescript(script)


def open_apps(app_names: list) -> str:
    results = []
    for app in app_names:
        results.append(open_app(app.strip()).rstrip("."))
        time.sleep(0.3)
    return ". ".join(results) + "."


def close_app(app: str) -> str:
    run_applescript(f'quit app "{app}"')
    return f"Closed {app}."


def web_search(query: str, n: int = 4) -> str:
    try:
        with DDGS() as d:
            results = list(d.text(query, max_results=n))
        return "\n".join([f"- {r['title']}: {r['body']}" for r in results]) if results else "No results."
    except Exception as e:
        return f"Search failed: {e}"


def deep_research(topic: str, n_sources: int = 3, max_results: int = None) -> str:
    """Multi-source research with full synthesis — gives comprehensive answers."""
    print(f"Deep research: {topic}")
    if max_results is not None:
        n_sources = max(1, int(max_results))
    try:
        import httpx
        from bs4 import BeautifulSoup
    except ImportError:
        return web_search(topic, n_sources)

    # Get search results
    raw_results = []
    try:
        with DDGS() as d:
            raw_results = list(d.text(topic, max_results=n_sources + 2))
    except Exception as e:
        print("Deep research search failed:", e)
        traceback.print_exc()

    # Fetch and parse pages
    contents = []
    for r in raw_results[:n_sources]:
        url = r.get("href", "")
        if not url:
            continue
        try:
            resp   = http_requests.get(url, headers={"User-Agent": "Mozilla/5.0"},
                                       timeout=8)
            soup   = BeautifulSoup(resp.text, "html.parser")
            for tag in soup(["script", "style", "nav", "footer", "header"]):
                tag.decompose()
            text   = soup.get_text(separator=" ", strip=True)
            lines  = [l.strip() for l in text.splitlines() if l.strip()]
            sample = " ".join(lines)[:2000]
            contents.append(f"Source: {url}\n{sample}")
        except Exception:
            contents.append(f"- {r.get('title','')}: {r.get('body','')}")

    if not contents:
        return f"No content found for: {topic}"

    combined = "\n\n---\n\n".join(contents[:n_sources])
    prompt   = (
        f"Write a comprehensive, detailed research summary about: '{topic}'\n"
        f"Use the following source content. Aim for 300-500 words. "
        f"Cover: what it is, current state, key developments, implications, limitations.\n\n"
        f"Sources:\n{combined[:4000]}"
    )
    summary = llm_call(prompt, max_tokens=600, temperature=0.3)
    return f"Research: '{topic}'\n\n{summary}"


def get_weather(loc: str = "") -> str:
    try:
        url = f"https://wttr.in/{urllib.parse.quote(loc)}?format=3" if loc else "https://wttr.in/?format=3"
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.read().decode().strip()
    except Exception as e:
        return f"Weather unavailable: {e}"


def get_system_status() -> str:
    cpu  = psutil.cpu_percent(interval=0.5)
    ram  = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    batt = psutil.sensors_battery()
    bs   = f"{batt.percent:.0f}%{'(charging)' if batt.power_plugged else '(battery)'}" if batt else "N/A"
    return f"CPU:{cpu:.0f}% RAM:{ram.percent:.0f}% Disk:{disk.percent:.0f}% Bat:{bs}"


def get_wifi_status() -> str:
    """Return current Wi-Fi SSID on macOS when available."""
    interfaces = ["en0", "en1"]
    try:
        hw = subprocess.run(
            ["networksetup", "-listallhardwareports"],
            capture_output=True,
            text=True,
            timeout=4,
        ).stdout
        blocks = hw.split("\n\n")
        for b in blocks:
            if "Hardware Port: Wi-Fi" in b or "Hardware Port: AirPort" in b:
                m = re.search(r"Device:\s*(\S+)", b)
                if m:
                    dev = m.group(1).strip()
                    if dev and dev not in interfaces:
                        interfaces.insert(0, dev)
    except Exception as e:
        print("WiFi hardware discovery error:", e)
        traceback.print_exc()

    for iface in interfaces:
        try:
            r = subprocess.run(
                ["networksetup", "-getairportnetwork", iface],
                capture_output=True,
                text=True,
                timeout=3,
            )
            out = (r.stdout or "").strip()
            if "not associated with an AirPort network" in out:
                return "WiFi: not connected"
            if "Current Wi-Fi Network" in out and ":" in out:
                ssid = out.split(":", 1)[1].strip()
                if ssid:
                    return f"WiFi: {ssid}"
        except Exception as e:
            print(f"WiFi check failed for {iface}:", e)
            traceback.print_exc()

    try:
        r = subprocess.run(
            [
                "/System/Library/PrivateFrameworks/Apple80211.framework/Versions/Current/Resources/airport",
                "-I",
            ],
            capture_output=True,
            text=True,
            timeout=3,
        )
        for line in (r.stdout or "").splitlines():
            s = line.strip()
            if s.startswith("SSID:"):
                ssid = s.split(":", 1)[1].strip()
                if ssid:
                    return f"WiFi: {ssid}"
    except Exception as e:
        print("WiFi airport utility error:", e)
        traceback.print_exc()

    return "WiFi status unavailable."


def battery_report() -> str:
    if len(battery_history) < 2:
        return "Not enough data yet."
    f, l = battery_history[0], battery_history[-1]
    diff = l["percent"] - f["percent"]
    return (f"Battery: {f['percent']:.0f}% at {f['time'][11:16]} → "
            f"{l['percent']:.0f}% at {l['time'][11:16]}. "
            f"{abs(diff):.1f}% {'gained' if diff > 0 else 'lost'}.")


def read_file(path: str) -> str:
    path = path.strip().replace("~", HOME_DIR)
    p    = Path(path)
    if not p.exists():
        matches = list(Path(HOME_DIR).rglob(p.name))
        if matches:
            p = matches[0]
        else:
            return f"File not found: {path}"
    try:
        if p.suffix == ".pdf":
            with open(p, "rb") as f:
                r    = PyPDF2.PdfReader(f)
                text = "".join([pg.extract_text() or "" for pg in r.pages[:15]])
            return text[:5000]
        elif p.suffix == ".docx":
            d = docx.Document(str(p))
            return "\n".join([pg.text for pg in d.paragraphs])[:5000]
        else:
            with open(p, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()[:5000]
    except Exception as e:
        return f"Error: {e}"


def summarize_document(path: str) -> str:
    """PDF/document summarization pipeline."""
    content = read_file(path)
    if content.startswith("File not found") or content.startswith("Error"):
        return content
    prompt = (
        f"Provide a comprehensive summary of this document. Cover: "
        f"main topic, key points, conclusions, and any important data or findings. "
        f"Aim for 200-400 words.\n\n{content[:4000]}"
    )
    return llm_call(prompt, max_tokens=500, temperature=0.3)


def list_files(directory: str = "~") -> str:
    d = Path(directory.replace("~", HOME_DIR))
    if not d.exists():
        return "Directory not found."
    files = [f.name for f in d.iterdir() if not f.name.startswith(".")]
    return f"Files in {d.name}: " + ", ".join(files[:30])


def analyze_and_fix_code(filepath: str) -> str:
    """Read code, identify errors, generate comprehensive fix."""
    content = read_file(filepath)
    if content.startswith("File not found") or content.startswith("Error"):
        return content
    prompt = (
        f"You are a senior software engineer. Analyze this code thoroughly.\n\n"
        f"Provide:\n"
        f"1. BUGS: List all bugs and errors found\n"
        f"2. IMPROVEMENTS: Performance and readability improvements\n"
        f"3. FIXED CODE: The corrected version\n\n"
        f"Code:\n{content}"
    )
    return llm_call(prompt, max_tokens=1000, temperature=0.1)


def write_and_run_code(task: str) -> str:
    code_prompt = (
        f"Write Python 3 code to: {task}\n"
        f"Return ONLY the code. Must be complete and runnable. Print the result."
    )
    code = llm_call(code_prompt, max_tokens=600, temperature=0.1)
    code = re.sub(r'^```python|^```|```$', '', code, flags=re.MULTILINE).strip()
    tmp  = Path(HOME_DIR) / "Projects" / "jarvis" / "jarvis_task.py"
    tmp.write_text(code)
    try:
        result = subprocess.run(["python", str(tmp)],
                                capture_output=True, text=True, timeout=30)
        if result.stdout:
            print(f"[Code stdout] {result.stdout[:1000]}")
        if result.stderr:
            print(f"[Code stderr] {result.stderr[:1000]}")
        out = result.stdout.strip() or result.stderr.strip()
        return f"Output: {out[:500]}"
    except subprocess.TimeoutExpired:
        return "Code timed out."
    except Exception as e:
        return f"Code error: {e}"


def send_desktop_notification(title: str, message: str):
    run_applescript(f'display notification "{message}" with title "{title}" sound name "Ping"')


def send_imessage(contact: str, message: str) -> str:
    script = f'''
    tell application "Messages"
        activate
        set s to 1st service whose service type = iMessage
        set b to buddy "{contact}" of s
        send "{message}" to b
    end tell
    '''
    r = run_applescript(script)
    return f"iMessage sent to {contact}." if "error" not in r.lower() else f"Failed: {r}"


def _get_gmail_service():
    """Build Gmail API service from local OAuth credentials/token."""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    scopes = ["https://www.googleapis.com/auth/gmail.modify"]
    token_path = Path("gmail_token.json")
    creds_path = Path("gmail_credentials.json")

    if not token_path.exists():
        raise Exception("gmail_token.json not found")
    if not creds_path.exists():
        raise Exception("gmail_credentials.json not found")

    creds = Credentials.from_authorized_user_file(str(token_path), scopes=scopes)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_path.write_text(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def read_gmail(max_results: int = 5) -> str:
    """Read latest Gmail messages (snippet view)."""
    try:
        service = _get_gmail_service()
        lst = service.users().messages().list(userId="me", maxResults=max_results, labelIds=["INBOX"]).execute()
        items = lst.get("messages", [])
        if not items:
            return "Your inbox looks empty."

        lines = []
        for m in items[:max_results]:
            msg = service.users().messages().get(userId="me", id=m["id"], format="metadata", metadataHeaders=["Subject", "From"]).execute()
            headers = {h.get("name", ""): h.get("value", "") for h in msg.get("payload", {}).get("headers", [])}
            snippet = msg.get("snippet", "")
            lines.append(f"From: {headers.get('From','Unknown')} | Subject: {headers.get('Subject','(No subject)')} | {snippet[:120]}")
        return "\n".join(lines)
    except Exception as e:
        return f"Gmail read error: {e}"


def send_gmail(to_email: str, subject: str, body: str) -> str:
    """Send an email using Gmail API."""
    if not to_email or not body:
        return "Please provide recipient and message for email."
    try:
        import base64
        from email.mime.text import MIMEText

        service = _get_gmail_service()
        msg = MIMEText(body)
        msg["to"] = to_email
        msg["subject"] = subject or "Message from JARVIS"

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        sent = service.users().messages().send(userId="me", body={"raw": raw}).execute()
        return f"Email sent to {to_email}. ID: {sent.get('id', 'unknown')}"
    except Exception as e:
        return f"Gmail send error: {e}"


def send_whatsapp(contact: str, message: str) -> str:
    """Send WhatsApp message using Selenium automation module."""
    global wa_automation
    if not contact or not message:
        return "Please provide both contact and message for WhatsApp."
    try:
        open_whatsapp_web_in_chrome()
        time.sleep(2)
        if wa_automation is None:
            from jarvis_whatsapp import WhatsAppAutomation
            wa_automation = WhatsAppAutomation()
            wa_automation.start()
        ok = wa_automation.send_message(contact, message)
        if not ok:
            # Recover from stale/closed Selenium session by recreating driver once.
            try:
                wa_automation.close()
            except Exception:
                pass
            from jarvis_whatsapp import WhatsAppAutomation
            wa_automation = WhatsAppAutomation()
            wa_automation.start()
            ok = wa_automation.send_message(contact, message)
        return f"WhatsApp sent to {contact}." if ok else f"WhatsApp failed for {contact}."
    except Exception as e:
        return f"WhatsApp error: {e}"


def read_whatsapp(max_chats: int = 5, speak_out: bool = False, gui=None) -> str:
    """Read unread WhatsApp messages via Selenium automation."""
    global wa_automation
    try:
        open_whatsapp_web_in_chrome()
        time.sleep(2)
        if wa_automation is None:
            from jarvis_whatsapp import WhatsAppAutomation
            wa_automation = WhatsAppAutomation()
            wa_automation.start()
        msgs = wa_automation.read_unread(max_chats=max(1, min(20, int(max_chats))))
        if not msgs:
            # Retry once with a fresh Selenium session in case the old window is stale.
            try:
                wa_automation.close()
            except Exception:
                pass
            from jarvis_whatsapp import WhatsAppAutomation
            wa_automation = WhatsAppAutomation()
            wa_automation.start()
            msgs = wa_automation.read_unread(max_chats=max(1, min(20, int(max_chats))))
        if not msgs:
            return "No unread WhatsApp messages found."
        lines = [f"{m.get('from', 'Unknown')}: {m.get('message', '')}" for m in msgs]
        out = "Unread WhatsApp messages:\n" + "\n".join(lines)
        if speak_out:
            speak(". ".join(lines), gui)
        return out
    except Exception as e:
        return f"WhatsApp read error: {e}"


def create_calendar_event(title: str, date: str, time_str: str, duration_mins: int = 60) -> str:
    """Create a calendar event via AppleScript."""
    try:
        dt = datetime.strptime(f"{date} {time_str}", "%Y-%m-%d %H:%M")
    except ValueError:
        return "Calendar error: Invalid date/time format. Use YYYY-MM-DD and HH:MM."

    script = f'''
    tell application "Calendar"
        activate
        set startDate to current date
        set year of startDate to {dt.year}
        set month of startDate to {dt.strftime('%B')}
        set day of startDate to {dt.day}
        set hours of startDate to {dt.hour}
        set minutes of startDate to {dt.minute}
        set seconds of startDate to 0
        set endDate to startDate + {duration_mins * 60}
        tell calendar 1
            make new event with properties {{summary:"{title}", start date:startDate, end date:endDate}}
        end tell
    end tell
    '''
    r = run_applescript(script)
    return f"Calendar event created: {title} on {date} at {time_str}." if "error" not in r.lower() else f"Calendar error: {r}"


def spotify_control(action: str, song: str = "") -> str:
    if song and action in ["play", "search"]:
        script = f'''
        tell application "Spotify" to activate
        delay 0.5
        tell application "System Events"
            tell process "Spotify"
                keystroke "l" using command down
                delay 0.3
                keystroke "{song}"
                delay 0.5
                keystroke return
            end tell
        end tell
        '''
        run_applescript(script)
        return f"Searching Spotify for: {song}"
    cmds = {"play": "play", "pause": "pause", "next": "next track",
            "previous": "previous track", "skip": "next track",
            "back": "previous track", "stop": "pause", "resume": "play"}
    cmd = cmds.get(action)
    if cmd:
        run_applescript(f'tell application "Spotify" to {cmd}')
        return f"Spotify: {action}."
    return f"Unknown Spotify command: {action}"


def get_spotify_track() -> str:
    return run_applescript('''
    tell application "Spotify"
        if player state is playing then
            return (name of current track) & " by " & (artist of current track)
        else
            return "Spotify is paused."
        end if
    end tell''')


def get_calendar_events() -> str:
    r = run_applescript('''
    tell application "Calendar"
        set today to current date
        set evts to ""
        repeat with c in calendars
            repeat with e in (every event of c whose start date >= today and start date <= today + 1 * days)
                set evts to evts & summary of e & " at " & (start date of e as string) & "\n"
            end repeat
        end repeat
        return evts
    end tell''') or "No events today."
    return "No events today." if not r or r.strip() in ["Done.", ""] else r


def create_reminder(title: str, when: str = "") -> str:
    body_line = f'body:"{when}"' if when else ''
    props = f'{{name:"{title}"{", " if body_line else ""}{body_line}}}'
    script = f'''
    tell application "Reminders"
        activate
        tell default list
            make new reminder with properties {props}
        end tell
    end tell
    '''
    r = run_applescript(script)
    return f"Reminder: {title}" if "error" not in r.lower() else f"Reminder error: {r}"


def create_note(title: str, content: str = "") -> str:
    script = f'''
    tell application "Notes"
        activate
        tell default account
            make new note with properties {{name:"{title}", body:"{content}"}}
        end tell
    end tell'''
    r = run_applescript(script)
    return f"Note: {title}" if "error" not in r.lower() else f"Note error: {r}"


def set_volume(level: int) -> str:
    run_applescript(f"set volume output volume {level}")
    return f"Volume: {level}."


def take_screenshot() -> str:
    path = f"{HOME_DIR}/Desktop/jarvis_{int(time.time())}.png"
    subprocess.run(["screencapture", path])
    return "Screenshot saved."


def lock_screen() -> str:
    run_applescript('tell application "System Events" to keystroke "q" using {command down, control down}')
    return "Screen locked."


def morning_briefing() -> str:
    weather = get_weather()
    cal     = get_calendar_events()
    sys_    = get_system_status()
    routines = routine_predictor.get_routine_summary() if routine_predictor is not None else "No routine data"
    goals = goal_engine.active_goal_context() if goal_engine is not None else "No goal data"

    if daily_review_engine is not None:
        try:
            return daily_review_engine.morning_brief(weather, cal, sys_, routines, goals)
        except Exception as e:
            print(f"Morning brief engine error: {e}")

    with hsl_lock:
        emotion = hsl.emotional.dominant_emotion
    return (f"Good morning, Tayyab. I can see you are {emotion}. "
            f"{weather}. Today: {cal[:150]}. System: {sys_}")


def camera_analyze() -> str:
    try:
        import mediapipe as mp
        cap = cv2.VideoCapture(CAMERA_INDEX)
        for _ in range(5):
            cap.read()
        ret, frame = cap.read()
        cap.release()
        if not ret:
            return "Camera unavailable."
        rgb      = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        findings = []
        with mp.solutions.face_detection.FaceDetection(0.5) as fd:
            r = fd.process(rgb)
            findings.append(f"{len(r.detections)} person(s)" if r.detections else "No person")
        with mp.solutions.hands.Hands(static_image_mode=True) as hd:
            r = hd.process(rgb)
            if r.multi_hand_landmarks:
                findings.append(f"{len(r.multi_hand_landmarks)} hand(s)")
        return ". ".join(findings)
    except Exception as e:
        return f"Camera error: {e}"


def self_healing_execute(task: str, action_fn, gui=None, max_retries: int = 2) -> str:
    """Self-healing execution — diagnoses failures, asks permission, retries."""
    for attempt in range(max_retries + 1):
        try:
            result = action_fn()
            if result and "error" in result.lower() and attempt < max_retries:
                raise Exception(result)
            return result or "Done."
        except Exception as e:
            err = str(e)
            if attempt >= max_retries:
                return f"Failed after {max_retries+1} attempts: {err}"
            if gui:
                gui.set_state("healing")
            diag = llm_call(
                f"Task: {task}\nError: {err}\nDiagnose in one sentence and suggest the fix.",
                max_tokens=100
            )
            wrong_log.add(
                documents=[f"Task:{task}|Error:{err}|Diag:{diag}"],
                metadatas=[{"ts": datetime.now().isoformat()}],
                ids=[str(uuid.uuid4())]
            )
            report = f"I failed to {task[:50]}. {diag} Shall I try differently?"
            speak(report, gui)
            time.sleep(8)  # Wait for user response
    return "Task cancelled."


# ══════════════════════════════════════════════════════════════════════════════
# FAST INTENT ROUTER — keyword first, LLM fallback
# ══════════════════════════════════════════════════════════════════════════════

# Keyword patterns - O(1) lookup, no LLM needed
KEYWORD_INTENTS = {
    # App control
    "open_app":        ["open ", "launch ", "start ", "run "],
    "close_app":       ["close ", "quit ", "kill "],
    # Spotify
    "spotify_play":    ["play spotify", "resume spotify", "start spotify"],
    "spotify_pause":   ["pause spotify", "pause music"],
    "spotify_next":    ["next song", "next track", "skip", "next spotify", "spotify next"],
    "spotify_prev":    ["previous", "go back", "last song", "previous spotify", "spotify previous"],
    "spotify_info":    ["what's playing on spotify", "what is playing on spotify", "what's playing", "current track"],
    # Web
    "search_web":      ["search for", "google ", "look up", "find online"],
    "open_url":        ["open ", "go to ", "browse to ", "navigate to "],
    "maps_directions": ["get directions from", "directions from", "route from"],
    # System
    "system_status":   ["system status", "how is my computer", "cpu", "ram", "battery"],
    "wifi_status":     ["wifi status", "wi-fi status", "network name", "which wifi"],
    "volume_control":  ["set volume", "volume to", "volume"],
    "battery":         ["battery", "how charged"],
    "weather":         ["weather", "temperature", "forecast"],
    "screenshot":      ["screenshot", "take a screenshot"],
    "lock_screen":     ["lock screen", "lock my screen"],
    # Files
    "file_list":       ["list files", "show files", "files in"],
    "file_read":       ["read ", "open file", "show me the file"],
    "summarize_doc":   ["summarize", "summary of"],
    # Comms
    "send_imessage":   ["send message", "imessage", "message "],
    "send_whatsapp":   ["whatsapp"],
    "read_whatsapp":   ["read whatsapp", "read my whatsapp", "check whatsapp", "whatsapp unread", "whatsapp messages"],
    "read_gmail":      ["read my email", "read my gmail", "check email", "check gmail", "inbox"],
    "send_gmail":      ["send email", "email to", "gmail to"],
    # Face / voice / GitHub / hotkeys
    "learn_face":      ["learn face", "remember face", "save face"],
    "greet_face":      ["greet me", "do you know me", "recognize me", "who am i"],
    "set_voice":       ["clone voice", "custom voice", "set voice", "voice profile"],
    "set_prosody":     ["speech mode", "prosody mode", "set tone", "thoughtful mode", "concise mode", "balanced mode", "auto tone"],
    "set_tts_backend": ["tts backend", "voice backend", "google voice", "use google voice", "use edge voice"],
    "startup_prefs":   ["startup prefs", "show startup prefs", "save startup prefs", "remember settings"],
    "task_mode":       ["task mode", "work mode", "start coding mode", "start focus mode", "start communication mode", "start research mode"],
    "github_issue":    ["github issue", "create github issue", "new issue"],
    "github_push":     ["push to github", "git push", "push code"],
    "start_hotkeys":   ["enable hotkeys", "start hotkeys", "global hotkeys"],
    # Productivity
    "create_reminder": ["remind me", "set a reminder"],
    "create_note":     ["create note", "make a note", "write note"],
    "calendar":        ["calendar", "my schedule", "events today"],
    # Research
    "deep_research":   ["research ", "deep dive", "learn about", "study "],
    "agent_research":  ["using agents"],
    # Phase 19
    "add_goal":        ["my goal is", "i want to", "i need to finish", "i'm trying to", "i am trying to", "add goal"],
    "view_goals":      ["show my goals", "what are my goals", "goal stack"],
    "mark_goal_done":  ["i finished", "goal done", "completed that"],
    "explain_decision": ["why did you", "explain that decision", "reasoning chain"],
    "self_improve":    ["run self improvement", "fix your weaknesses"],
    "htn_plan":        ["plan for", "break down", "decompose"],
    "prm_trace":       ["show reasoning", "last trace", "reasoning chain"],
    "device_status":   ["device status", "peer devices", "device sync", "multi-device"],
    # Memory
    "memory_stats":    ["memory stats", "how much memory"],
    "behavioral_dashboard": ["behavioral dashboard", "behavior profile", "behavioral profile", "how do i think", "my patterns", "l4 meta"],
    "memory_compress": ["compress memory", "compress today", "nightly compression"],
    "memory_l1": ["what did i work on yesterday", "yesterday digest", "l1 digest"],
    "memory_l3": ["what do you know about me", "my known facts", "l3 facts"],
    "social_summary": ["who have i mentioned", "people in my life", "social context"],
    "routine_summary": ["my routines", "what do i usually do", "my habits"],
    "goal_summary": ["my goals", "goal summary", "active goals", "what are my goals"],
    "plan_request": ["plan this", "make a plan", "create a plan", "break this down", "next steps for"],
    "planner_status": ["latest plan", "plan status", "show my plan"],
    "daily_review": ["daily review", "end of day review", "evening review"],
    "permission_status": ["permission status", "approval status", "pending approvals"],
    "verification_stats": ["verification stats", "reliability stats"],
    "model_stats": ["model usage", "which model", "routing stats"],
    "home_turn_on":  ["turn on the", "turn on my", "switch on", "switch on the", "lights on"],
    "home_turn_off": ["turn off the", "turn off my", "switch off", "switch off the", "lights off"],
    "home_dim":      ["dim the", "set brightness", "brightness to", "lights to"],
    "home_temp":     ["set temperature", "set thermostat", "make it warmer", "cooler", "thermostat"],
    "home_status":   ["is the light", "device status", "home status"],
    "benchmark":     ["agi benchmark", "performance stats", "how are you performing"],
    "run_eval":      ["run self evaluation", "test yourself", "run tests"],
    "cms_stats":     ["memory tiers", "cms stats", "continuum memory"],
    "world_model":   ["world model", "what apps are installed", "my projects", "what's on my network", "what is on my network", "my network"],
    # Phase 18 status commands
    "moa_stats":     ["moa stats", "mixture of agents", "proposer stats"],
    "titan_recall":  ["titan memory", "surprising things", "unexpected memories"],
    "agent_status":  ["agent graph", "agent status", "sub-agents"],
    "budget_stats":  ["compute budget", "model usage stats"],
    "filter_stats":  ["constitutional filter", "corrections made"],
    # Camera
    "camera":          ["what do you see", "look at me", "see me", "describe me"],
    "object_detect":   ["what is this", "identify this", "what am i holding"],
    # Screen
    "screen_read":     ["what's on my screen", "read my screen", "screen context"],
    # Code
    "fix_code":        ["fix my code", "fix the code", "fix code", "debug", "fix the bug"],
    "run_code":        ["run code", "execute code", "write code to"],
    # Self
    "self_correct":    ["that was wrong", "you got that wrong", "incorrect"],
    # Briefing
    "briefing":        ["morning briefing", "daily briefing", "briefing"],
    # Phase 16
    "task_chain":      ["when i finish", "after i", "once i complete", "remind me to"],
    "email_read":      ["check my email", "read my emails", "any emails"],
    # Phase 20 — Causal, Skills, Execution, Alignment
    "skill_run":       ["morning routine", "commit my code", "code commit flow"],
    "skill_list":      ["list skills", "show skills", "my skills"],
    "skill_create":    ["create skill", "save this as a skill", "name this sequence"],
    "causal_query":    ["why did", "what caused", "what would happen if", "explain why"],
    "thread_status":   ["execution threads", "running tasks", "sleeping threads"],
    "alignment":       ["run alignment check", "alignment report", "check alignment"],
    "mm_retrieve":     ["what screenshots do you have", "find that document", "find that screenshot", "screenshot from last week", "multimodal memory"],
    # Siri-Equivalent Features
    "system_control":  ["sleep mac", "restart mac", "shutdown", "shut down", "go to sleep", "restart", "turn off mac"],
    "bluetooth":       ["bluetooth on", "bluetooth off", "enable bluetooth", "disable bluetooth", "toggle bluetooth"],
    "do_not_disturb":  ["do not disturb", "dnd on", "dnd off", "enable focus", "disable focus", "focus mode"],
    "backlight":       ["keyboard backlight", "backlight brightness", "keyboard brightness"],
    "facetime":        ["facetime", "video call", "call via facetime"],
    "phone_call":      ["call ", "phone call to", "ring "],
    "recent_docs":     ["recent documents", "recent files", "recently opened", "recent"],
    "calculator":      ["what is", "calculate", "math ", "what's", "equals"],
    "convert_units":   ["convert ", "translation of"],
    "convert_currency": ["in dollars", "in euros", "currency", "exchange rate"],
    "sports_scores":   ["score", "game", "sports", "match", "tournament"],
    "news":            ["news", "latest news", "what's in the news", "today's news"],
    "stocks":          ["stock", "market", "dow jones", "nasdaq"],
    "podcasts":        ["podcast", "play podcast", "pause podcast", "next episode"],
    "audiobooks":      ["audiobook", "audible", "play audiobook"],
    "dictation":       ["dictate", "dictation mode", "start dictating"],
}


def _extract_params(text: str, intent: str) -> dict:
    """Extract parameters from text based on intent type."""
    t = text.lower()
    params = {}

    if intent == "open_app":
        for trigger in ["open ", "launch ", "start ", "run "]:
            if trigger in t:
                tail = t.split(trigger, 1)[1].strip()
                app_part = re.split(r"\band\b|\bthen\b", tail, maxsplit=1)[0].strip()
                if app_part.startswith("google chrome"):
                    app = "chrome"
                elif app_part.startswith("visual studio code"):
                    app = "vscode"
                elif app_part.startswith("apple maps") or app_part.startswith("google maps"):
                    app = "maps"
                else:
                    app = app_part.split()[0] if app_part else ""
                if app:
                    params["apps"] = [app]
                break

    elif intent == "open_url":
        url = _extract_url_from_text(text)
        if url:
            params["url"] = url

    elif intent == "search_web":
        query = _extract_task_text(text, ["search for ", "google ", "look up ", "find online "])
        if query:
            params["query"] = query

    elif intent == "maps_directions":
        params.update(_extract_directions(text))

    elif intent in ["spotify_play"]:
        # Extract song name if present
        for trigger in ["play "]:
            if trigger in t and t != "play spotify":
                song = t.split(trigger, 1)[1].strip()
                if song and song not in ["spotify", "music"]:
                    params["song"] = song
                    params["action"] = "play"
                break

    elif intent == "agent_research":
        # "research X using agents" -> topic = X
        m = re.search(r"research\s+(.+?)\s+using\s+agents", text, flags=re.IGNORECASE)
        if m:
            params["topic"] = m.group(1).strip(" .,!?")
        else:
            cleaned = re.sub(r"\busing\s+agents\b", "", text, flags=re.IGNORECASE).strip(" .,!?")
            cleaned = re.sub(r"^research\s+", "", cleaned, flags=re.IGNORECASE).strip()
            if cleaned:
                params["topic"] = cleaned

    elif intent in ["add_goal", "htn_plan"]:
        goal_text = re.sub(
            r"^(add goal|my goal is|i want to|i need to finish|i'm trying to|i am trying to|plan for|break down|decompose)\s+",
            "",
            text,
            flags=re.IGNORECASE,
        ).strip()
        if goal_text.lower().startswith("to "):
            goal_text = goal_text[3:].strip()
        if goal_text:
            params["goal"] = goal_text

    elif intent == "weather":
        for prep in ["in ", "for ", "at "]:
            if prep in t:
                params["location"] = t.split(prep, 1)[1].strip().split()[0]
                break

    elif intent == "send_imessage":
        if "to " in t and "saying " in t:
            params["contact"] = t.split("to ", 1)[1].split(" saying")[0].strip()
            params["message"] = t.split("saying ", 1)[1].strip()

    elif intent == "send_whatsapp":
        if "to " in t and "saying " in t:
            params["contact"] = text[t.index("to ") + 3:text.lower().index(" saying ")].strip()
            params["message"] = text[text.lower().index("saying ") + 7:].strip()
        elif "to " in t:
            params["contact"] = text[t.index("to ") + 3:].strip()

    elif intent == "read_whatsapp":
        m = re.search(r"(\d+)", t)
        if m:
            params["max_chats"] = int(m.group(1))

    elif intent == "send_gmail":
        if "to " in t and "saying " in t:
            params["to"] = text[t.index("to ") + 3:text.lower().index(" saying ")].strip()
            params["body"] = text[text.lower().index("saying ") + 7:].strip()
        elif "to " in t:
            params["to"] = text[t.index("to ") + 3:].strip()
        if "subject " in t:
            params["subject"] = text[text.lower().index("subject ") + 8:].strip()

    elif intent == "read_gmail":
        m = re.search(r"(\d+)", t)
        if m:
            params["max_results"] = int(m.group(1))

    elif intent == "learn_face":
        m = re.search(r"(?:learn face|remember face|save face)(?: for)?\s+(.+)$", text, re.IGNORECASE)
        if m:
            params["name"] = m.group(1).strip(" .,!?\"")

    elif intent == "set_voice":
        m = re.search(r"(?:clone voice|custom voice|set voice|voice profile)(?:\s+to)?\s+(.+)$", text, re.IGNORECASE)
        if m:
            params["persona"] = m.group(1).strip(" .,!?\"")

    elif intent == "set_prosody":
        m = re.search(r"(?:speech mode|prosody mode|set speech mode|set tone)(?:\s+to)?\s+(auto|balanced|thoughtful|concise)", text, re.IGNORECASE)
        if m:
            params["mode"] = m.group(1).lower().strip()
        elif "thoughtful" in t:
            params["mode"] = "thoughtful"
        elif "concise" in t:
            params["mode"] = "concise"
        elif "balanced" in t:
            params["mode"] = "balanced"
        elif "auto" in t:
            params["mode"] = "auto"

    elif intent == "set_tts_backend":
        if "google" in t:
            params["backend"] = "google"
        elif "edge" in t:
            params["backend"] = "edge"

    elif intent == "startup_prefs":
        params["show"] = True

    elif intent == "task_mode":
        if "coding" in t or "code" in t or "dev" in t:
            params["mode"] = "coding"
        elif "focus" in t or "deep" in t:
            params["mode"] = "focus"
        elif "communication" in t or "comms" in t or "inbox" in t:
            params["mode"] = "communication"
        elif "research" in t or "study" in t:
            params["mode"] = "research"

    elif intent == "github_issue":
        repo = re.search(r"(?:issue for|github issue for|create github issue for)\s+([\w.-]+/[\w.-]+)", text, re.IGNORECASE)
        title = re.search(r"(?:titled|title)\s+['\"]?([^'\"]+)['\"]?", text, re.IGNORECASE)
        if repo:
            params["repo"] = repo.group(1).strip()
        if title:
            params["title"] = title.group(1).strip()

    elif intent == "github_push":
        m = re.search(r"(?:push to github|git push|push code)(?:\s+in)?\s+(.+)$", text, re.IGNORECASE)
        if m:
            params["repo_path"] = m.group(1).strip(" .,!?\"")

    elif intent == "start_hotkeys":
        params["enabled"] = True

    elif intent == "create_reminder":
        for trigger in ["remind me to ", "remind me ", "set a reminder "]:
            if trigger in t:
                params["title"] = text[t.index(trigger) + len(trigger):].strip()
                break

    elif intent == "deep_research":
        for trigger in ["research ", "deep dive into ", "learn about ", "study "]:
            if trigger in t:
                params["topic"] = text[t.index(trigger) + len(trigger):].strip()
                break

    elif intent == "plan_request":
        for trigger in ["plan this", "make a plan", "create a plan", "break this down", "next steps for"]:
            if trigger in t:
                params["request"] = text[t.index(trigger) + len(trigger):].strip(" .,!?")
                break
        if not params.get("request"):
            params["request"] = text.strip()

    elif intent in ["home_turn_on", "home_turn_off", "home_status"]:
        cleaned = re.sub(r"^(turn on|turn off|switch on|switch off|is|the|my|device status|home status)\s+", "", t).strip(" .,!?")
        if cleaned:
            params["device"] = cleaned

    elif intent == "home_dim":
        m = re.search(r"(\d{1,3})\s*%", t)
        if m:
            params["brightness_pct"] = max(0, min(100, int(m.group(1))))
        cleaned = re.sub(r"^(dim the|set brightness|brightness to|lights to)\s+", "", t).strip(" .,!?")
        if cleaned and not cleaned.isdigit():
            params["device"] = cleaned

    elif intent == "home_temp":
        m = re.search(r"(\d+(?:\.\d+)?)", t)
        if m:
            params["temp_c"] = float(m.group(1))
        cleaned = re.sub(r"^(set temperature|set thermostat|make it warmer|cooler|thermostat)\s+", "", t).strip(" .,!?")
        if cleaned:
            params["device"] = cleaned

    elif intent == "skill_create":
        m = re.search(r"create skill called\s+([^:]+):\s*(.+)$", text, re.IGNORECASE)
        if not m:
            m = re.search(r"create skill\s+([^:]+):\s*(.+)$", text, re.IGNORECASE)
        if m:
            name = m.group(1).strip(" .,!?\"")
            raw_steps = m.group(2).strip()
            steps = [s.strip(" .,!?") for s in re.split(r",| then ", raw_steps, flags=re.IGNORECASE) if s.strip(" .,!?")]
            if name:
                params["name"] = name
                params["description"] = f"Custom skill: {name}"
                params["triggers"] = [name.lower()]
                params["steps"] = steps

    return params


def parse_intent_llm(text: str) -> dict:
    """LLM-based intent parsing - only called for complex inputs."""
    try:
        result = llm_call(
            text,
            system="Return ONLY valid JSON: {\"intent\": str, \"params\": {}, \"steps\": [], \"confidence\": float}\n"
                   "Intents: open_app, search_web, weather, system_status, file_read, deep_research, "
                "send_imessage, send_whatsapp, read_whatsapp, read_gmail, send_gmail, email_read, create_reminder, create_note, spotify_control, camera, screen_read, behavioral_dashboard, social_summary, routine_summary, goal_summary, plan_request, planner_status, daily_review, permission_status, verification_stats, model_stats, task_chain, wifi_status, learn_face, greet_face, set_voice, set_prosody, set_tts_backend, startup_prefs, task_mode, github_issue, github_push, start_hotkeys, home_turn_on, home_turn_off, home_dim, home_temp, home_status, benchmark, run_eval, cms_stats, world_model, multi_step, chat",
            max_tokens=120,
            temperature=0.1
        )
        result = re.sub(r'^```json|^```|```$', '', result, flags=re.MULTILINE).strip()
        return json.loads(result)
    except Exception:
        return {"intent": "chat", "params": {}, "steps": [], "confidence": 0.3}


def fast_intent(text: str) -> dict:
    """
    Keyword-based intent detection - runs in microseconds.
    Returns intent dict compatible with LLM parser format.
    Falls back to LLM only for complex/ambiguous inputs.
    """
    t = text.lower().strip()

    # Explicit chain support, e.g. "open chrome then search for ai news".
    if " then " in t:
        steps = [s.strip(" .,!?") for s in re.split(r"\bthen\b", text, flags=re.IGNORECASE) if s.strip(" .,!?")]
        if len(steps) > 1:
            return {"intent": "multi_step", "params": {}, "steps": steps, "confidence": 0.99}

    # Check stop first
    if any(w in t for w in ["stop", "quiet", "silence", "shut up"]):
        return {"intent": "stop", "params": {}, "steps": [], "confidence": 1.0}

    # URL-first disambiguation so "open github.com" maps to open_url, not open_app.
    # Avoid treating local file paths like "main.py" or "~/Desktop/a.txt" as URLs.
    url_candidate = _extract_url_from_text(text)
    if url_candidate:
        lc = url_candidate.lower()
        file_like_suffix = re.search(r"\.(py|txt|pdf|docx|md|json|csv|yaml|yml|toml|ini|log)$", lc) is not None
        looks_web = lc.startswith("http") or lc.startswith("www.") or ("/" not in lc and not file_like_suffix)
        if looks_web:
            params = _extract_params(text, "open_url")
            return {"intent": "open_url", "params": params, "steps": [], "confidence": 0.97}

    # Priority disambiguation for overlapping generic keywords.
    if any(p in t for p in KEYWORD_INTENTS.get("read_whatsapp", [])):
        params = _extract_params(text, "read_whatsapp")
        return {"intent": "read_whatsapp", "params": params, "steps": [], "confidence": 0.95}
    if any(p in t for p in KEYWORD_INTENTS.get("read_gmail", [])):
        params = _extract_params(text, "read_gmail")
        return {"intent": "read_gmail", "params": params, "steps": [], "confidence": 0.95}
    if any(p in t for p in KEYWORD_INTENTS.get("wifi_status", [])):
        return {"intent": "wifi_status", "params": {}, "steps": [], "confidence": 0.95}
    if any(p in t for p in KEYWORD_INTENTS.get("email_read", [])):
        params = _extract_params(text, "read_gmail")
        return {"intent": "email_read", "params": params, "steps": [], "confidence": 0.95}
    if any(p in t for p in KEYWORD_INTENTS.get("send_gmail", [])):
        params = _extract_params(text, "send_gmail")
        return {"intent": "send_gmail", "params": params, "steps": [], "confidence": 0.95}
    if any(p in t for p in KEYWORD_INTENTS.get("volume_control", [])):
        params = _extract_params(text, "volume_control")
        return {"intent": "volume_control", "params": params, "steps": [], "confidence": 0.95}
    if any(p in t for p in KEYWORD_INTENTS.get("task_mode", [])):
        params = _extract_params(text, "task_mode")
        return {"intent": "task_mode", "params": params, "steps": [], "confidence": 0.95}
    if any(p in t for p in KEYWORD_INTENTS.get("set_tts_backend", [])):
        params = _extract_params(text, "set_tts_backend")
        return {"intent": "set_tts_backend", "params": params, "steps": [], "confidence": 0.95}

    # Agent graph routing command, e.g. "research quantum chips using agents"
    if "using agents" in t and "research" in t:
        params = _extract_params(text, "agent_research")
        return {"intent": "agent_research", "params": params, "steps": [], "confidence": 0.97}

    # Preserve original wording for explicit goal and HTN commands.
    if any(p in t for p in KEYWORD_INTENTS.get("add_goal", [])):
        params = _extract_params(text, "add_goal")
        return {"intent": "add_goal", "params": params, "steps": [], "confidence": 0.97}
    if any(p in t for p in KEYWORD_INTENTS.get("htn_plan", [])):
        params = _extract_params(text, "htn_plan")
        return {"intent": "htn_plan", "params": params, "steps": [], "confidence": 0.97}
    if any(p in t for p in KEYWORD_INTENTS.get("self_improve", [])):
        return {"intent": "self_improve", "params": {}, "steps": [], "confidence": 0.97}

    # Phase 20 prioritization
    if any(p in t for p in KEYWORD_INTENTS.get("skill_run", [])):
        params = _extract_params(text, "skill_run")
        return {"intent": "skill_run", "params": params, "steps": [], "confidence": 0.95}
    if any(p in t for p in KEYWORD_INTENTS.get("causal_query", [])):
        params = _extract_params(text, "causal_query")
        return {"intent": "causal_query", "params": params, "steps": [], "confidence": 0.95}
    if any(p in t for p in KEYWORD_INTENTS.get("thread_status", [])):
        return {"intent": "thread_status", "params": {}, "steps": [], "confidence": 0.95}
    if any(p in t for p in KEYWORD_INTENTS.get("alignment", [])):
        return {"intent": "alignment", "params": {}, "steps": [], "confidence": 0.95}

    # Run keyword matching
    for intent_name, patterns in KEYWORD_INTENTS.items():
        if any(t.startswith(p) or p in t for p in patterns):
            params = _extract_params(t, intent_name)
            return {"intent": intent_name, "params": params, "steps": [], "confidence": 0.9}

    # Fallback to LLM for complex cases
    return parse_intent_llm(text)


# ══════════════════════════════════════════════════════════════════════════════
# MAIN BRAIN — Process requests with HSL context
# ══════════════════════════════════════════════════════════════════════════════

def process_request(user_input: str, gui=None, audio_file: str = None) -> str:
    global last_interaction, interaction_count, VOICE, VOICE_OVERRIDE, scheduled_alerts, mem_stack, task_chain_engine, social_memory, routine_predictor, model_router, goal_engine, autonomy_planner, permission_policy, verification_layer, daily_review_engine, cms, symbolic, world, home, evaluator, benchmarks, moa, titan, constitution, cross_modal, agent_orchestrator, dyn_prompt, budget, prm

    start_time = time.time()

    if task_chain_engine is not None:
        if any(p in user_input.lower() for p in ["i'm done", "im done", "i am done", "finished", "completed"]):
            fired = task_chain_engine.trigger_user_done()
            if fired > 0:
                return f"Triggered {fired} active task chain(s)."

        chain = task_chain_engine.parse_chain(user_input)
        if chain is not None:
            task_chain_engine.confirm_chain(chain)
            return f"Task chain created: {chain.chain_id}. {task_chain_engine.list_active()}"

    if goal_engine is not None:
        try:
            goal_engine.observe_input(user_input)
        except Exception as e:
            print(f"Goal engine observe error: {e}")

    if symbolic is not None:
        try:
            sym_answer, sym_type = symbolic.reason(user_input)
            if sym_type == "symbolic" and sym_answer:
                if benchmarks is not None:
                    benchmarks.record_latency((time.time() - start_time) * 1000)
                return sym_answer
        except Exception as e:
            print(f"Symbolic reasoner error: {e}")

    lang = detect_language(user_input)
    VOICE = VOICE_OVERRIDE or get_voice_for_language(lang)  # Switch TTS voice dynamically

    # Stop command - check FIRST before anything else
    if any(w in user_input.lower() for w in [
        "stop", "quiet", "silence", "shut up", "enough", "stop speaking"
    ]):
        speak_stop()
        if gui:
            gui.set_state("idle")
        return "Stopped."

    last_interaction  = time.time()
    interaction_count += 1

    # Occasionally ask calibration question (every 20 interactions)
    if mem_stack is not None and interaction_count % 20 == 0:
        try:
            q = mem_stack.generate_calibration_question()
            if q:
                scheduled_alerts.append(f"Quick question: {q}")
        except Exception as e:
            print(f"Calibration question error: {e}")

    # Update HSL with interaction signal
    with hsl_lock:
        hsl.situational.last_intervention_mins = 0.0
        hsl.emotional.frustration_score = max(0.0, hsl.emotional.frustration_score - 0.05)

    if gui:
        gui.add_log("user", user_input)
        gui.set_state("thinking")

    # Detect voice emotion
    if audio_file and os.path.exists(audio_file):
        voice_emotion = detect_voice_emotion(audio_file)
        with hsl_lock:
            if voice_emotion != "neutral":
                hsl.emotional.dominant_emotion = voice_emotion

    # Periodically synthesize L1 profile
    if interaction_count % SYNTHESIS_EVERY_N == 0:
        threading.Thread(target=l1_profile.synthesize_from_memory, daemon=True).start()

    # Refresh system prompt with current HSL
    refresh_system_prompt()

    extra  = ""
    intent = fast_intent(user_input)
    action = intent.get("intent", "chat")
    params = intent.get("params", {})
    steps  = intent.get("steps", [])

    if safety is not None:
        try:
            proceed, confirm_msg = safety.pre_action_check(action, user_input, params)
            if not proceed:
                return confirm_msg
        except Exception as e:
            print(f"Safety pre-check error: {e}")

    def _return_direct(message: str) -> str:
        if safety is not None:
            try:
                safety.log(user_input, action, f"executed {action}", f"intent confidence: {intent.get('confidence', 0):.1f}")
            except Exception as e:
                print(f"Safety log error: {e}")
        if gui:
            try:
                gui.add_log("jarvis", message)
            except Exception:
                pass
        return message

    # Phase 20: convert long-horizon natural language into persistent execution threads.
    if action == "chat" and executor is not None:
        try:
            thread = executor.parse_and_create(user_input)
            if thread is not None:
                return _return_direct(
                    f"[Threads]: Persistent thread created ({thread.thread_id}) for '{thread.title}'. "
                    "It will continue across sessions."
                )
        except Exception as e:
            print(f"Persistent thread parse error: {e}")

    if goal_stack is not None and action not in {"add_goal", "view_goals", "mark_goal_done", "htn_plan"}:
        try:
            new_goal = goal_stack.infer_from_input(user_input)
            if new_goal:
                extra += f"\n[New goal detected]: {new_goal}"
        except Exception as e:
            print(f"Goal inference error: {e}")

    if hsl_orchestrator is not None:
        try:
            route = hsl_orchestrator.route_task(action)
            if route != "local" and action in {"deep_research", "fix_code", "run_code", "schedule_reminder", "home_control", "monitoring", "location"}:
                delegated = hsl_orchestrator.delegate_task(route, action, user_input)
                if delegated:
                    if safety is not None:
                        delegated = safety.post_response_wrap(delegated, action)
                    if gui:
                        gui.add_log("jarvis", delegated)
                    return delegated
        except Exception as e:
            print(f"HSL routing error: {e}")

    if prm is not None and prm.should_use_prm(action, user_input):
        prm_context = extra
        if social_memory is not None:
            try:
                prm_context += "\n" + social_memory.inject_into_context(user_input)
            except Exception:
                pass
        if titan is not None:
            try:
                titan_mems = titan.retrieve(user_input, n=2, surprise_weight=0.3)
                if titan_mems:
                    prm_context += "\n" + " | ".join(titan_mems[:2])
            except Exception:
                pass
        if goal_stack is not None:
            try:
                prm_context += f"\n{goal_stack.for_prompt()}"
            except Exception:
                pass
        if htn is not None:
            try:
                prm_context += f"\n{htn.inject_into_prompt()}"
            except Exception:
                pass
        try:
            reply = prm.reason(user_input, context=prm_context)
            if reply and safety is not None:
                reply = safety.post_response_wrap(reply, action)
                safety.log(user_input, action, f"prm {action}", f"intent confidence: {intent.get('confidence', 0):.1f}")
            if gui:
                gui.add_log("jarvis", reply)
            return reply
        except Exception as e:
            print(f"PRM reasoning error: {e}")
            # Fall through to normal processing

    # Predict compute tier and get configuration
    compute_tier = ComputeTier.STANDARD
    tier_config  = {"model": "qwen2.5:7b", "max_tokens": 300, "use_moa": False, "timeout": 10}
    if budget is not None:
        try:
            compute_tier, tier_config = budget.get_config(action, user_input)
        except Exception as e:
            print(f"Compute budget config error: {e}")

    if benchmarks is not None:
        try:
            benchmarks.record_intent(action, action)
        except Exception as e:
            print(f"Benchmark intent record error: {e}")

    if goal_stack is not None:
        try:
            goal_ctx = goal_stack.for_prompt()
            if goal_ctx:
                extra += f"\n[Goal stack]:\n{goal_ctx}"
        except Exception as e:
            print(f"Goal stack context error: {e}")

    if htn is not None:
        try:
            htn_ctx = htn.inject_into_prompt()
            if htn_ctx:
                extra += f"\n[HTN]:\n{htn_ctx}"
        except Exception as e:
            print(f"HTN context error: {e}")

    if hsl_orchestrator is not None:
        try:
            peer_ctx = hsl_orchestrator.peer_context_for_prompt()
            if peer_ctx:
                extra += f"\n{peer_ctx}"
        except Exception as e:
            print(f"HSL peer context error: {e}")

    if world is not None:
        try:
            if action == "open_app" and "app" not in params:
                apps = params.get("apps", [])
                if apps:
                    params["app"] = apps[0]
            if action in {"open_app", "file_read"}:
                params = world.plan_action(action, params)
                if action == "open_app" and params.get("resolved_app"):
                    params["apps"] = [params["resolved_app"]]
                if action == "file_read" and params.get("resolved_path"):
                    params["path"] = params["resolved_path"]
        except Exception as e:
            print(f"World model planning error: {e}")

    if permission_policy is not None:
        # Support "confirm" as a follow-up message to execute pending actions.
        approved = permission_policy.consume_if_confirmed(user_input)
        if approved is not None:
            action = approved.get("action", action)
            params = approved.get("params", params) or {}
            user_input = approved.get("original_input", user_input)
        else:
            decision = permission_policy.evaluate(action, user_input, params)
            if decision.get("requires_confirmation") and action not in {"stop", "chat", "self_correct"}:
                pending = permission_policy.set_pending(action, user_input, params, decision.get("reason", "Confirmation required"))
                return (
                    f"Safety check: {decision.get('reason', 'Confirmation required')} "
                    f"Say 'confirm' to continue with {action}. Approval id: {pending.get('approval_id', '')}."
                )

    print(f"Intent: {action} | Params: {params}")
    if gui:
        gui.set_intent(action)

    # Prefer delegating actionable intents to the PersistentExecutor when available.
    action_handled = False
    reply = None
    DELEGATE_ACTIONS = {
        "open_app", "open_url", "maps_directions", "close_app", "search_web",
        "deep_research", "agent_research", "send_imessage", "send_whatsapp",
        "create_reminder", "create_note", "create_calendar_event", "spotify_control",
        "spotify_play", "volume_control", "screenshot", "lock_screen", "send_whatsapp",
        "read_whatsapp", "learn_face", "greet_face", "set_voice", "start_hotkeys",
        "github_issue", "github_push", "send_gmail", "facetime", "phone_call",
        "podcasts", "audiobooks", "system_control", "bluetooth", "do_not_disturb",
        "backlight"
    }
    if executor is not None and action in DELEGATE_ACTIONS:
        try:
            exec_res = executor.execute(user_input)
            extra = f"\n[Executor]: {exec_res}"
            reply = exec_res if isinstance(exec_res, str) else str(exec_res)
            action_handled = True
        except Exception as e:
            print(f"Executor error: {e}")

    # ── Execute intent ────────────────────────────────────────────────────────
    if action == "open_app":
        apps = params.get("apps", [params.get("app", "")])
        primary_app = apps[0] if apps and apps[0] else ""
        extra = f"\n[Action]: {run_agentic_open_task(user_input, primary_app)}"

    elif action == "open_url":
        url = params.get("url", "") or _extract_url_from_text(user_input)
        if url:
            extra = f"\n[Action]: {open_url_in_chrome(url, new_tab=_wants_new_tab(user_input))}"
        else:
            extra = f"\n[Action]: {run_agentic_open_task(user_input, 'chrome')}"

    elif action == "maps_directions":
        origin = params.get("origin", "")
        destination = params.get("destination", "")
        if origin and destination:
            extra = f"\n[Action]: {open_directions_in_maps(origin, destination)}"
        else:
            extra = "\n[Action]: Please say: get directions from A to B."

    elif action == "close_app":
        extra = f"\n[Action]: {close_app(params.get('app',''))}"

    elif action == "search_web":
        query = params.get("query", user_input)
        chrome_result = search_in_chrome(query, new_tab=_wants_new_tab(user_input))
        extra = f"\n[Action]: {chrome_result}\n[Web]: {web_search(query)}"

    elif action == "deep_research":
        topic = params.get("topic", params.get("query", user_input))
        extra = f"\n[Research]:\n{deep_research(topic)}"

    elif action == "agent_research":
        topic = params.get("topic", params.get("query", user_input)).strip()
        if agent_orchestrator is None:
            extra = "\n[Agent Graph]: unavailable."
        elif not topic:
            extra = "\n[Agent Graph]: Please provide a research topic."
        else:
            try:
                dispatch_result = agent_orchestrator.dispatch(
                    task=f"Research {topic}",
                    agent_name="research",
                    wait_secs=0,
                )
                extra = f"\n[Agent Graph]: {dispatch_result}"
            except Exception as e:
                extra = f"\n[Agent Graph]: dispatch failed ({e})"

    elif action == "weather":
        loc   = params.get("location", "")
        extra = f"\n[Weather]: {get_weather(loc)}"

    elif action == "system_status":
        extra = f"\n[System]: {get_system_status()}\n[HSL]: {hsl.to_context_string()}"

    elif action == "wifi_status":
        extra = f"\n[WiFi]: {get_wifi_status()}"

    elif action in ["battery_report", "battery"]:
        extra = f"\n[Battery]: {battery_report()}"

    elif action == "file_read":
        fp    = params.get("path", params.get("file", ""))
        extra = f"\n[File]: {read_file(fp)}" if fp else "\n[File]: No path provided."

    elif action == "file_list":
        extra = f"\n[Files]: {list_files(params.get('directory', '~'))}"

    elif action == "summarize_doc":
        fp    = params.get("path", params.get("file", ""))
        extra = f"\n[Summary]:\n{summarize_document(fp)}" if fp else "\n[Summary]: No file specified."

    elif action == "send_imessage":
        extra = f"\n[Action]: {send_imessage(params.get('contact',''), params.get('message',''))}"

    elif action == "create_reminder":
        extra = f"\n[Action]: {create_reminder(params.get('title', user_input))}"

    elif action == "create_note":
        extra = f"\n[Action]: {create_note(params.get('title','JARVIS Note'), params.get('content', user_input))}"

    elif action in ["calendar_events", "calendar"]:
        extra = f"\n[Calendar]: {get_calendar_events()}"

    elif action == "create_calendar_event":
        title = params.get("title", "")
        date  = params.get("date", datetime.now().strftime("%Y-%m-%d"))
        t     = params.get("time", "09:00")
        dur   = int(params.get("duration_mins", 60))
        extra = f"\n[Action]: {create_calendar_event(title, date, t, dur)}"

    elif action == "spotify_control":
        act   = params.get("action", params.get("command", "play"))
        song  = params.get("song", params.get("track", ""))
        extra = f"\n[Spotify]: {spotify_control(act, song)}"

    elif action in ["spotify_play", "spotify_pause", "spotify_next", "spotify_prev"]:
        action_map = {
            "spotify_play": "play",
            "spotify_pause": "pause",
            "spotify_next": "next",
            "spotify_prev": "previous",
        }
        song  = params.get("song", params.get("track", ""))
        extra = f"\n[Spotify]: {spotify_control(action_map[action], song)}"

    elif action == "spotify_info":
        extra = f"\n[Spotify]: {get_spotify_track()}"

    elif action == "volume_control":
        lv = params.get("level", params.get("volume", ""))
        extra = f"\n[Action]: {set_volume(int(lv))}" if str(lv).isdigit() else \
                f"\n[Volume]: {run_applescript('output volume of (get volume settings)')}"

    elif action == "screenshot":
        extra = f"\n[Action]: {take_screenshot()}"

    elif action == "lock_screen":
        extra = f"\n[Action]: {lock_screen()}"

    elif action in ["camera_look", "camera", "object_detect"]:
        extra = f"\n[Camera]: {camera_analyze()}"

    elif action == "send_whatsapp":
        extra = f"\n[Action]: {send_whatsapp(params.get('contact', ''), params.get('message', ''))}"

    elif action == "read_whatsapp":
        count = int(params.get("max_chats", 5))
        extra = f"\n[WhatsApp]:\n{read_whatsapp(max_chats=count, speak_out=False, gui=gui)}"

    elif action == "learn_face":
        extra = f"\n[Face]: {learn_face(params.get('name', 'Unknown'), params.get('image', params.get('path', '')))}"

    elif action == "greet_face":
        extra = f"\n[Face]: {greet_face()}"

    elif action == "set_voice":
        extra = f"\n[Voice]: {set_voice_persona(params.get('persona', params.get('voice', '')), params.get('voice_id', ''))}"

    elif action == "set_prosody":
        extra = f"\n[Voice]: {set_prosody_mode(params.get('mode', 'auto'))}"

    elif action == "set_tts_backend":
        extra = f"\n[Voice]: {set_tts_backend(params.get('backend', 'edge'))}"

    elif action == "startup_prefs":
        extra = f"\n[Config]: {get_startup_preferences()}"

    elif action == "task_mode":
        extra = f"\n[Mode]: {run_task_mode(params.get('mode', ''))}"

    elif action == "start_hotkeys":
        extra = f"\n[Hotkeys]: {start_global_hotkeys(gui)}"

    elif action == "github_issue":
        repo = params.get("repo", params.get("repository", ""))
        title = params.get("title", "JARVIS issue")
        body = params.get("body", user_input)
        labels = params.get("labels", [])
        extra = f"\n[GitHub]: {create_github_issue(repo, title, body, labels)}"

    elif action == "github_push":
        repo_path = params.get("repo_path", params.get("path", "."))
        message = params.get("message", "JARVIS update")
        extra = f"\n[GitHub]: {push_git_changes(repo_path, message)}"

    elif action == "read_gmail":
        count = int(params.get("max_results", 5))
        extra = f"\n[Gmail]:\n{read_gmail(max_results=max(1, min(20, count)))}"

    elif action == "email_read":
        count = int(params.get("max_results", 5))
        extra = f"\n[Gmail]:\n{read_gmail(max_results=max(1, min(20, count)))}"

    elif action == "send_gmail":
        extra = f"\n[Action]: {send_gmail(params.get('to', ''), params.get('subject', ''), params.get('body', ''))}"

    elif action == "screen_read":
        extra = f"\n[Screen]: {current_screen_ctx}"

    elif action == "briefing":
        extra = f"\n[Briefing]: {morning_briefing()}"

    elif action == "code_review":
        fp = params.get("file", "")
        if not fp:
            for fn in ["main.py", "app.py", "jarvis.py"]:
                if os.path.exists(fn):
                    fp = fn
                    break
        extra = f"\n[Code]:\n{analyze_and_fix_code(fp)}" if fp else "\n[Code]: No file."

    elif action == "fix_code":
        fp    = params.get("file", params.get("path", ""))
        extra = f"\n[Fix]:\n{analyze_and_fix_code(fp)}" if fp else "\n[Fix]: No file."

    elif action == "run_code":
        extra = f"\n[Code Output]:\n{write_and_run_code(params.get('task', user_input))}"

    elif action == "multi_step":
        results = []
        for step in steps:
            si     = fast_intent(step)
            sa     = si.get("intent", "chat")
            sp     = si.get("params", {})
            if sa == "open_app":
                apps = sp.get("apps", [sp.get("app", step.replace("open ","").strip())])
                results.append(open_apps(apps))
            elif sa == "search_web":
                q = sp.get("query", step)
                results.append(search_in_chrome(q, new_tab=_wants_new_tab(step)))
                results.append(web_search(q)[:100])
            elif sa == "open_url":
                u = sp.get("url", "") or _extract_url_from_text(step)
                results.append(open_url_in_chrome(u, new_tab=_wants_new_tab(step)) if u else "No URL provided.")
            elif sa == "send_whatsapp":
                results.append(send_whatsapp(sp.get("contact", ""), sp.get("message", "")))
            elif sa == "read_whatsapp":
                results.append(read_whatsapp(max_chats=int(sp.get("max_chats", 5))))
            elif sa == "learn_face":
                results.append(learn_face(sp.get("name", "Unknown"), sp.get("image", sp.get("path", ""))))
            elif sa == "greet_face":
                results.append(greet_face())
            elif sa == "set_voice":
                results.append(set_voice_persona(sp.get("persona", sp.get("voice", "")), sp.get("voice_id", "")))
            elif sa == "set_prosody":
                results.append(set_prosody_mode(sp.get("mode", "auto")))
            elif sa == "set_tts_backend":
                results.append(set_tts_backend(sp.get("backend", "edge")))
            elif sa == "startup_prefs":
                results.append(get_startup_preferences())
            elif sa == "task_mode":
                results.append(run_task_mode(sp.get("mode", "")))
            elif sa == "start_hotkeys":
                results.append(start_global_hotkeys(gui))
            elif sa == "github_issue":
                results.append(create_github_issue(sp.get("repo", sp.get("repository", "")), sp.get("title", "JARVIS issue"), sp.get("body", step), sp.get("labels", [])))
            elif sa == "github_push":
                results.append(push_git_changes(sp.get("repo_path", sp.get("path", ".")), sp.get("message", "JARVIS update")))
            elif sa == "spotify_control":
                results.append(spotify_control(sp.get("action", "play"), sp.get("song", "")))
            elif sa in ["spotify_play", "spotify_pause", "spotify_next", "spotify_prev"]:
                action_map = {
                    "spotify_play": "play",
                    "spotify_pause": "pause",
                    "spotify_next": "next",
                    "spotify_prev": "previous",
                }
                results.append(spotify_control(action_map[sa], sp.get("song", "")))
            else:
                results.append(generate_and_run_applescript(step))
            time.sleep(0.5)
        extra = "\n[Chain]: " + " → ".join(results)

    elif action == "self_correct":
        adapt_threshold("stop")  # User correcting = JARVIS was wrong
        wrong_log.add(
            documents=[f"Correction: {user_input}"],
            metadatas=[{"ts": datetime.now().isoformat()}],
            ids=[str(uuid.uuid4())]
        )
        extra = "\n[Note]: Correction logged. Threshold adjusted. I will improve."

    elif action == "health_check":
        extra = f"\n[Health]: LLM:{'Ollama' if USE_LOCAL_LLM else 'Groq'} | Mem:{memory.count()} | HSL:ACTIVE | Screen:{current_screen_ctx[:40]}"

    elif action == "memory_stats":
        extra = f"\n[Stats]: L0:{memory.count()} entries | L1:{'active' if l1_profile.get_profile_string() else 'building'} | Errors:{wrong_log.count()} | HSL:{hsl.to_context_string()[:100]}"

    elif action == "behavioral_dashboard":
        if mem_stack is not None:
            extra = f"\n[Behavioral Profile]:\n{mem_stack.get_behavioral_dashboard()}"
        else:
            extra = "\n[Behavioral Profile]: Memory stack not initialized yet."

    elif action == "social_summary":
        if social_memory is not None:
            extra = f"\n[Social]: {social_memory.get_social_summary()}"
        else:
            extra = "\n[Social]: Social memory unavailable."

    elif action == "routine_summary":
        if routine_predictor is not None:
            extra = f"\n[Routines]:\n{routine_predictor.get_routine_summary()}"
        else:
            extra = "\n[Routines]: Routine predictor unavailable."

    elif action == "goal_summary":
        if goal_engine is not None:
            extra = f"\n[Goals]:\n{goal_engine.summary()}"
        else:
            extra = "\n[Goals]: Goal engine unavailable."

    elif action == "plan_request":
        if autonomy_planner is not None:
            req = params.get("request", user_input)
            try:
                autonomy_planner.create_plan(req)
                extra = f"\n[Planner]:\n{autonomy_planner.latest_plan_summary()}"
            except Exception as e:
                extra = f"\n[Planner]: planning failed ({e})"
        else:
            extra = "\n[Planner]: Autonomy planner unavailable."

    elif action == "planner_status":
        if autonomy_planner is not None:
            extra = f"\n[Planner]:\n{autonomy_planner.latest_plan_summary()}"
        else:
            extra = "\n[Planner]: Autonomy planner unavailable."

    elif action == "daily_review":
        if daily_review_engine is not None:
            mem_stats_text = f"L0:{memory.count()}"
            model_stats_text = model_router.usage_stats() if model_router is not None else "router unavailable"
            verification_text = verification_layer.stats() if verification_layer is not None else "verification unavailable"
            extra = f"\n[Daily Review]:\n{daily_review_engine.evening_review(mem_stats_text, model_stats_text, verification_text)}"
        else:
            extra = "\n[Daily Review]: engine unavailable."

    elif action == "permission_status":
        if permission_policy is not None:
            extra = f"\n[Permissions]: {permission_policy.pending_status()}"
        else:
            extra = "\n[Permissions]: policy unavailable."

    elif action == "verification_stats":
        if verification_layer is not None:
            extra = f"\n[Verification]: {verification_layer.stats()}"
        else:
            extra = "\n[Verification]: layer unavailable."

    elif action == "model_stats":
        if model_router is not None:
            try:
                extra = f"\n[Model Router]: {model_router.usage_stats()}"
            except Exception as e:
                extra = f"\n[Model Router]: unavailable ({e})"
        else:
            extra = "\n[Model Router]: not initialized."

    elif action == "home_turn_on":
        device = params.get("device", "").strip() or "living room light"
        if home is None:
            extra = "\n[Home]: Home Assistant unavailable."
        else:
            extra = f"\n[Home]: {home.turn_on(device)}"

    elif action == "home_turn_off":
        device = params.get("device", "").strip() or "living room light"
        if home is None:
            extra = "\n[Home]: Home Assistant unavailable."
        else:
            extra = f"\n[Home]: {home.turn_off(device)}"

    elif action == "home_dim":
        device = params.get("device", "").strip() or "living room light"
        brightness = int(params.get("brightness_pct", 50))
        if home is None:
            extra = "\n[Home]: Home Assistant unavailable."
        else:
            extra = f"\n[Home]: {home.set_light_brightness(device, brightness)}"

    elif action == "home_temp":
        device = params.get("device", "").strip() or "thermostat"
        temp_c = float(params.get("temp_c", 22.0))
        if home is None:
            extra = "\n[Home]: Home Assistant unavailable."
        else:
            extra = f"\n[Home]: {home.set_temperature(device, temp_c)}"

    elif action == "home_status":
        device = params.get("device", "").strip()
        if home is None:
            extra = "\n[Home]: Home Assistant unavailable."
        elif device:
            extra = f"\n[Home]: {home.get_state(device)}"
        else:
            extra = f"\n[Home]: {home.list_devices()}"

    elif action == "benchmark":
        if benchmarks is None:
            extra = "\n[Benchmarks]: Benchmark system unavailable."
        else:
            extra = f"\n[Benchmarks]:\n{benchmarks.get_dashboard()}"

    elif action == "run_eval":
        if evaluator is None:
            extra = "\n[Evaluation]: Self-evaluator unavailable."
        else:
            report = evaluator.run_evaluation()
            extra = (
                f"\n[Evaluation]: {report.get('passed', 0)}/{report.get('total', 0)} passed "
                f"({report.get('pass_rate', 0)}%)."
            )

    elif action == "cms_stats":
        if cms is None:
            extra = "\n[CMS]: Continuum memory unavailable."
        else:
            try:
                extra = f"\n[CMS]: {cms.get_tier_stats()}"
            except Exception as e:
                extra = f"\n[CMS]: stats unavailable ({e})"

    elif action == "world_model":
        if world is None:
            extra = "\n[World]: World model unavailable."
        else:
            extra = f"\n[World]: {world.get_context_string()}"

    elif action == "moa_stats":
        if moa is None:
            extra = "\n[MoA]: Mixture of Agents unavailable."
        else:
            try:
                extra = f"\n[MoA]: {moa.get_stats()}"
            except Exception as e:
                extra = f"\n[MoA]: stats unavailable ({e})"

    elif action == "titan_recall":
        if titan is None:
            extra = "\n[Titan]: Titan memory unavailable."
        else:
            try:
                surprises = titan.get_high_surprise_memories(threshold=0.6, n=5)
                if surprises:
                    extra = "\n[Titan]: " + " | ".join(surprises)
                else:
                    extra = f"\n[Titan]: {titan.get_stats()}"
            except Exception as e:
                extra = f"\n[Titan]: recall unavailable ({e})"

    elif action == "agent_status":
        if agent_orchestrator is None:
            extra = "\n[Agent Graph]: Agent graph unavailable."
        else:
            try:
                extra = f"\n[Agent Graph]: {agent_orchestrator.status()}"
            except Exception as e:
                extra = f"\n[Agent Graph]: status unavailable ({e})"

    elif action == "add_goal":
        goal_title = params.get("goal", user_input)
        if goal_stack is None:
            extra = "\n[Goal Stack]: unavailable."
        else:
            goal = goal_stack.push(goal_title)
            extra = f"\n[Goal Stack]: Added goal '{goal.get('title', goal_title)}'."
            if dyn_prompt is None:
                return _return_direct(extra)

    elif action == "view_goals":
        if goal_stack is None:
            extra = "\n[Goal Stack]: unavailable."
        else:
            extra = f"\n[Goal Stack]:\n{goal_stack.summary()}\n{goal_stack.for_prompt()}"
            if dyn_prompt is None:
                return _return_direct(extra)

    elif action == "mark_goal_done":
        if goal_stack is None:
            extra = "\n[Goal Stack]: unavailable."
        else:
            active_goals = goal_stack.active()
            if active_goals:
                goal_stack.complete(active_goals[0]["id"])
                extra = f"\n[Goal Stack]: Marked '{active_goals[0]['title']}' done."
            else:
                extra = "\n[Goal Stack]: No active goals to mark done."
            if dyn_prompt is None:
                return _return_direct(extra)

    elif action == "explain_decision":
        if safety is None:
            extra = "\n[Safety]: unavailable."
        else:
            if "reasoning chain" in user_input.lower():
                extra = f"\n[Safety]:\n{safety.chain()}"
            else:
                extra = f"\n[Safety]:\n{safety.explain()}"
            if dyn_prompt is None:
                return _return_direct(extra)

    elif action == "self_improve":
        if improver is None:
            extra = "\n[Self-Improvement]: unavailable."
        else:
            extra = f"\n[Self-Improvement]: {improver.run_full_cycle()}"
            if dyn_prompt is None:
                return _return_direct(extra)

    elif action == "htn_plan":
        if htn is None:
            extra = "\n[HTN]: unavailable."
        else:
            goal_text = params.get("goal", user_input)
            root_id = htn.add_goal(title=goal_text, description=user_input)
            extra = f"\n[HTN]: Created plan {root_id}.\n{htn.get_goal_summary()}"
            if dyn_prompt is None:
                return _return_direct(extra)

    elif action == "prm_trace":
        if prm is None:
            extra = "\n[PRM]: unavailable."
        else:
            extra = f"\n[PRM]: {prm.last_trace_summary()}"
            if dyn_prompt is None:
                return _return_direct(extra)

    elif action == "device_status":
        if hsl_orchestrator is None:
            extra = "\n[HSL]: multi-device orchestration unavailable."
        else:
            extra = f"\n[HSL]:\n{hsl_orchestrator.peer_context_for_prompt()}"
            if dyn_prompt is None:
                return _return_direct(extra)

    elif action == "budget_stats":
        if budget is None:
            extra = "\n[Compute Budget]: tracker unavailable."
        else:
            try:
                extra = f"\n[Compute Budget]: {budget.stats()}"
            except Exception as e:
                extra = f"\n[Compute Budget]: stats unavailable ({e})"

    elif action == "filter_stats":
        if constitution is None:
            extra = "\n[Constitutional Filter]: unavailable."
        else:
            try:
                extra = f"\n[Constitutional Filter]: {constitution.get_stats()}"
            except Exception as e:
                extra = f"\n[Constitutional Filter]: stats unavailable ({e})"

    elif action == "task_chain":
        if task_chain_engine is None:
            extra = "\n[Task Chain]: Task chain engine unavailable."
        else:
            chain = task_chain_engine.parse_chain(user_input)
            if chain is not None:
                task_chain_engine.confirm_chain(chain)
                extra = f"\n[Task Chain]: created {chain.chain_id}."
            else:
                extra = "\n[Task Chain]: Please describe the sequence clearly."

    # Phase 20 — Sleeping Agents, Causal Reasoning, Skills, Multimodal Memory, Alignment
    elif action == "skill_run":
        if skill_lib is None:
            extra = "\n[Skill]: Skill library unavailable."
        else:
            matched = skill_lib.match(user_input)
            if matched:
                result = skill_lib.execute_skill(matched)
                extra = f"\n[Skill]: {result}"
            else:
                extra = "\n[Skill]: No matching skill found. Say 'list skills' to see available skills."

    elif action == "skill_list":
        if skill_lib is None:
            extra = "\n[Skill]: Skill library unavailable."
        else:
            extra = f"\n[Skill]:\n{skill_lib.list_skills()}"

    elif action == "skill_create":
        if skill_lib is None:
            extra = "\n[Skill]: Skill library unavailable."
        else:
            name = params.get("name", "").strip()
            if name and params.get("steps"):
                try:
                    created = skill_lib.create_skill_from_sequence(
                        name=name,
                        description=params.get("description", f"Custom skill: {name}"),
                        triggers=params.get("triggers", [name.lower()]),
                        steps=params.get("steps", []),
                    )
                    extra = (
                        f"\n[Skill]: Created '{created.name}' with {len(params.get('steps', []))} step(s). "
                        f"Try saying: {params.get('triggers', [created.name.lower()])[0]}"
                    )
                except Exception as e:
                    extra = f"\n[Skill]: Failed to create skill ({e})."
            else:
                extra = "\n[Skill]: To create a skill, use: create skill called <name>: step 1, step 2"

    elif action == "causal_query":
        if causal is None:
            extra = "\n[Causal]: Causal engine unavailable."
        else:
            answer = causal.analyze_request(user_input)
            if answer:
                extra = f"\n[Causal]: {answer}"
            else:
                extra = "\n[Causal]: Not a causal reasoning question."

    elif action == "thread_status":
        if executor is None:
            extra = "\n[Threads]: Persistent executor unavailable."
        else:
            extra = f"\n[Threads]: {executor.get_active_summary()}"

    elif action == "alignment":
        if alignment is None:
            extra = "\n[Alignment]: Alignment checker unavailable."
        else:
            force_run = "run alignment check" in user_input.lower()
            if force_run or alignment.is_due():
                report = alignment.run_checkpoint(memory_stack=mem_stack, safety_foundation=safety)
                extra = f"\n[Alignment]:\n{alignment.format_report(report)}"
            else:
                extra = "\n[Alignment]: Next quarterly checkpoint in ~90 days."

    elif action == "mm_retrieve":
        if mm_memory is None:
            extra = "\n[Memory]: Multimodal memory unavailable."
        else:
            query = (
                user_input
                .replace("what screenshots do you have", "")
                .replace("find that screenshot from last week", "")
                .replace("find that screenshot", "")
                .replace("find that document", "")
                .replace("multimodal memory", "")
                .strip()
            )
            if not query:
                query = user_input
            result = mm_memory.retrieve_with_screenshots(query, n=3)
            extra = f"\n[Memory]:\n{result}"

    # Siri-Equivalent Features
    elif action == "system_control":
        cmd = user_input.lower()
        if "sleep" in cmd or "go to sleep" in cmd:
            extra = "\n[System]: Putting Mac to sleep..."
            res = run_applescript('tell application "System Events" to sleep')
            extra += f" -> {res}"
        elif "restart" in cmd:
            extra = "\n[System]: Restarting Mac..."
            res = run_applescript('tell application "System Events" to restart')
            extra += f" -> {res}"
        elif "shutdown" in cmd or "shut down" in cmd or "turn off" in cmd:
            extra = "\n[System]: Shutting down Mac..."
            res = run_applescript('tell application "System Events" to shut down')
            extra += f" -> {res}"
        else:
            extra = "\n[System]: Say 'sleep mac', 'restart mac', or 'shutdown'."

    elif action == "bluetooth":
        cmd = user_input.lower()
        if "on" in cmd or "enable" in cmd:
            extra = "\n[Bluetooth]: Turning on..."
            res = run_applescript('tell application "System Events" to turn bluetooth on')
            extra += f" -> {res}"
        elif "off" in cmd or "disable" in cmd:
            extra = "\n[Bluetooth]: Turning off..."
            res = run_applescript('tell application "System Events" to turn bluetooth off')
            extra += f" -> {res}"
        else:
            extra = "\n[Bluetooth]: Say 'bluetooth on' or 'bluetooth off'."

    elif action == "do_not_disturb":
        cmd = user_input.lower()
        if "on" in cmd or "enable" in cmd or "dnd on" in cmd:
            extra = "\n[Focus]: Enabling Do Not Disturb..."
            res = run_applescript('delay 0.5; tell application "System Preferences" to activate')
            extra += f" -> {res}"
        elif "off" in cmd or "disable" in cmd or "dnd off" in cmd:
            extra = "\n[Focus]: Disabling Do Not Disturb..."
            res = run_applescript('key code 53')
            extra += f" -> {res}"
        else:
            extra = "\n[Focus]: Say 'enable do not disturb' or 'disable do not disturb'."

    elif action == "backlight":
        cmd = user_input.lower()
        match = re.search(r'\d+', user_input)
        if match:
            brightness = match.group()
            extra = f"\n[Backlight]: Setting keyboard brightness to {brightness}..."
            res = run_applescript('tell application "System Events" to key code 113')
            extra += f" -> {res}"
        else:
            extra = "\n[Backlight]: Say 'set keyboard backlight to [0-100]' to adjust."

    elif action == "facetime":
        contact = user_input.replace("facetime", "").replace("video call", "").replace("call", "").strip()
        if contact:
            extra = f"\n[FaceTime]: Starting call with {contact}..."
            try:
                subprocess.Popen(["open", "-a", "FaceTime"])
            except Exception as e:
                extra += f" -> Error: {e}"
        else:
            extra = "\n[FaceTime]: Say 'facetime [contact name]'."

    elif action == "phone_call":
        contact = user_input.replace("call ", "").replace("phone call to ", "").replace("ring ", "").strip()
        if contact:
            extra = f"\n[Phone]: Calling {contact}..."
            try:
                subprocess.Popen(["open", f"tel://{contact}"])
            except Exception as e:
                extra += f" -> Error: {e}"
        else:
            extra = "\n[Phone]: Say 'call [contact name]'."

    elif action == "recent_docs":
        extra = "\n[Recent]: Check System → Settings → General → Storage to view recently accessed documents."

    elif action == "calculator":
        try:
            expr = user_input.lower()
            for word in ["what is", "calculate", "math", "what's", "equals"]:
                expr = expr.replace(word, "").strip()
            if expr and expr != user_input.lower():
                result = eval(expr, {"__builtins__": {}}, {})
                extra = f"\n[Calculator]: {expr.strip()} = {result}"
            else:
                extra = "\n[Calculator]: Please specify a calculation."
        except Exception as e:
            extra = f"\n[Calculator]: {llm_call(user_input, max_tokens=100)}"

    elif action == "convert_units":
        unit_response = llm_call(f"Convert the following units: {user_input}. Provide brief answer only.", max_tokens=80)
        extra = f"\n[Conversion]: {unit_response}"

    elif action == "convert_currency":
        fx_response = llm_call(f"What is the current exchange rate for: {user_input}. Brief answer.", max_tokens=80)
        extra = f"\n[Currency]: {fx_response}"

    elif action == "sports_scores":
        sports_info = llm_call(f"Get latest sports scores/results for: {user_input}. Brief summary.", max_tokens=150)
        extra = f"\n[Sports]: {sports_info}"

    elif action == "news":
        search_query = user_input.replace("news", "").replace("latest", "").strip() or "today's news"
        news_results = web_search(search_query)
        extra = f"\n[News]:\n{news_results}"

    elif action == "stocks":
        stock_info = llm_call(f"Get current stock market information: {user_input}. Brief summary.", max_tokens=150)
        extra = f"\n[Stocks]: {stock_info}"

    elif action == "podcasts":
        cmd = user_input.lower()
        if "play" in cmd:
            extra = "\n[Podcasts]: Opening Podcasts app..."
            try:
                subprocess.Popen(["open", "-a", "Podcasts"])
            except Exception as e:
                extra += f" -> Error: {e}"
        elif "pause" in cmd:
            extra = "\n[Podcasts]: Pausing..."
            res = run_applescript('tell application "Podcasts" to pause')
            extra += f" -> {res}"
        elif "next" in cmd:
            extra = "\n[Podcasts]: Skipping to next episode..."
            res = run_applescript('tell application "Podcasts" to next track')
            extra += f" -> {res}"
        else:
            extra = "\n[Podcasts]: Say 'play podcast', 'pause podcast', or 'next episode'."

    elif action == "audiobooks":
        cmd = user_input.lower()
        if "play" in cmd:
            extra = "\n[Audiobooks]: Opening Books app..."
            try:
                subprocess.Popen(["open", "-a", "Books"])
            except Exception as e:
                extra += f" -> Error: {e}"
        else:
            extra = "\n[Audiobooks]: Say 'play audiobook [title]'."

    elif action == "dictation":
        extra = "\n[Dictation]: Dictation mode enabled. Say your message and I'll transcribe it."
        speak("Dictation mode activated.")

    else:
        # Fallback — try AppleScript for action-like commands
        if any(w in user_input.lower() for w in ["open", "close", "play", "set", "send", "turn"]):
            def fallback():
                return generate_and_run_applescript(user_input)
            result = self_healing_execute(user_input, fallback, gui)
            extra  = f"\n[Action]: {result}"

    # Add past memory context
    past = recall(user_input)
    if past:
        extra += f"\n[Past context]:\n{past}"

    if social_memory is not None:
        try:
            social_ctx = social_memory.inject_into_context(user_input)
            if social_ctx:
                extra += f"\n[Social context]:\n{social_ctx}"
        except Exception as e:
            print(f"Social memory inject error: {e}")

    if titan is not None:
        try:
            titan_mems = titan.retrieve(user_input, n=3, surprise_weight=0.4)
            if titan_mems:
                extra += f"\n[Long-term memory]: {' | '.join(titan_mems[:3])}"
        except Exception as e:
            print(f"Titan retrieve error: {e}")

    if goal_engine is not None:
        try:
            goal_ctx = goal_engine.active_goal_context()
            if goal_ctx:
                extra += f"\n[Goal context]: {goal_ctx}"
        except Exception as e:
            print(f"Goal context inject error: {e}")

    # Add HSL context
    with hsl_lock:
        hsl_context = hsl.to_context_string()
    extra += f"\n{hsl_context}"

    if world is not None:
        try:
            world_ctx = world.get_context_string()
            if world_ctx:
                extra += f"\n{world_ctx}"
        except Exception as e:
            print(f"World context inject error: {e}")

    if cross_modal is not None:
        try:
            cross_modal_ctx = cross_modal.as_hsl_injection()
            if cross_modal_ctx:
                extra += f"\n{cross_modal_ctx}"
        except Exception as e:
            print(f"Cross-modal context inject error: {e}")

    # Build dynamic system prompt
    if dyn_prompt is not None:
        try:
            system_content = dyn_prompt.build(
                intent=action, hsl=hsl,
                extra_context=extra[:200] if extra else ""
            )
            if not conversation_history or conversation_history[0].get("role") != "system":
                system_msg = {"role": "system", "content": system_content}
                conversation_history.insert(0, system_msg)
            else:
                conversation_history[0]["content"] = system_content
        except Exception as e:
            print(f"Dynamic prompt build error: {e}")

    # Build and send to LLM (skip if executor already handled the action)
    full_input = user_input + extra if extra else user_input
    conversation_history.append({"role": "user", "content": full_input})

    if len(conversation_history) > 22:
        sys_msg = conversation_history[0]
        conversation_history[:] = [sys_msg] + conversation_history[-20:]

    if not action_handled:
        try:
            # Select model based on compute tier
            if tier_config.get("use_moa") and moa is not None:
                reply = moa.generate(conversation_history, user_input, intent=action)
            else:
                reply = llm_stream_and_speak(conversation_history, gui)
            if not reply:
                reply = "Done."
            if safety is not None:
                reply = safety.post_response_wrap(reply, action)
                try:
                    safety.log(user_input, action, f"executed {action}", f"intent confidence: {intent.get('confidence', 0):.1f}")
                except Exception as e:
                    print(f"Safety log error: {e}")
        except Exception as e:
            reply = f"Error: {str(e)[:100]}"

    # Record latency for compute budget tracking
    if budget is not None:
        try:
            elapsed_ms = (time.time() - start_time) * 1000
            budget.record(compute_tier, elapsed_ms)
        except Exception as e:
            print(f"Compute budget record error: {e}")

    if constitution is not None:
        try:
            reply, was_corrected = constitution.filter(reply, user_input)
            if was_corrected:
                print("Constitutional correction applied.")
        except Exception as e:
            print(f"Constitutional filter error: {e}")

    if safety is not None:
        try:
            reply = safety.post_response_wrap(reply, action)
        except Exception as e:
            print(f"Safety post-wrap error: {e}")

    if verification_layer is not None:
        try:
            check = verification_layer.verify_action_result(action, extra)
            if not check.get("ok", True):
                reply = f"{reply}\n\nVerification note: {check.get('message', 'Action may need review.')}"
        except Exception as e:
            print(f"Verification layer error: {e}")

    if cms is not None:
        try:
            cms.observe("user", user_input)
            cms.observe("assistant", reply)
        except Exception as e:
            print(f"CMS observe error: {e}")

    if benchmarks is not None:
        try:
            elapsed_ms = (time.time() - start_time) * 1000
            benchmarks.record_latency(elapsed_ms)
            benchmarks.record_response(str(hash(user_input.lower().strip())), reply)
        except Exception as e:
            print(f"Benchmark record error: {e}")

    if world is not None:
        try:
            world.record_app_use(action)
        except Exception as e:
            print(f"World model usage record error: {e}")

    if cross_modal is not None:
        try:
            # Update screen modality if available
            if hsl and hsl.behavioral and hsl.behavioral.screen_context:
                cross_modal.update("screen", hsl.behavioral.screen_context, 0.8)
        except Exception as e:
            print(f"Cross-modal update error: {e}")

    if titan is not None:
        try:
            screen_ctx = hsl.behavioral.screen_context if hsl else "unknown"
            titan.store(f"Q: {user_input} A: {reply}", context=screen_ctx)
        except Exception as e:
            print(f"Titan store error: {e}")

    conversation_history.append({"role": "assistant", "content": reply})

    if social_memory is not None:
        try:
            social_memory.process_interaction(user_input)
        except Exception as e:
            print(f"Social memory process error: {e}")

    if routine_predictor is not None:
        try:
            routine_predictor.record_action(action)
        except Exception as e:
            print(f"Routine predictor record error: {e}")

    if mem_stack is not None:
        try:
            mem_stack.log_interaction(user_input, reply)
        except Exception as e:
            print(f"Memory stack log error: {e}")

    try:
        save_memory("user", user_input)
        save_memory("assistant", reply)
        if gui:
            gui.update_mem()
    except Exception as e:
        print(f"Save memory error: {e}")

    return reply


def is_wake_word(text: str) -> bool:
    return WAKE_WORD.lower() in text.lower()


# ══════════════════════════════════════════════════════════════════════════════
# PROACTIVE MONITOR — Uses three-gate model from Gap 2
# ══════════════════════════════════════════════════════════════════════════════

def proactive_monitor(pending_alerts: list):
    global briefing_done
    while True:
        time.sleep(60)
        try:
            now = datetime.now()
            if now.hour == BRIEFING_HOUR and not briefing_done:
                pending_alerts.append(morning_briefing())
                briefing_done = True
            if now.hour != BRIEFING_HOUR:
                briefing_done = False

            with hsl_lock:
                suggestion = hsl.situational.suggested_action
                confidence = hsl.situational.intervention_confidence
                session    = hsl.situational.session_duration_mins
                idle       = hsl.situational.idle_duration_mins

            # Only interrupt if three-gate model approves
            if suggestion == "intervene" and confidence > 0.7:
                alert = "I notice an error on your screen. Want me to help debug?"
                if should_interrupt("error_on_screen", alert):
                    pending_alerts.append(alert)

            elif suggestion == "suggest_break" and session > BREAK_ALERT_MINS:
                alert = f"You've worked {session:.0f} minutes straight. Consider a break."
                if should_interrupt("break_suggestion", alert) and "break" not in alerts_given:
                    alerts_given.add("break")
                    pending_alerts.append(alert)

            elif suggestion == "check_in" and idle > IDLE_ALERT_MINS:
                alert = "You've been idle a while. Need help with something?"
                if should_interrupt("idle_too_long", alert) and "idle" not in alerts_given:
                    alerts_given.add("idle")
                    pending_alerts.append(alert)

            batt = psutil.sensors_battery()
            if batt and not batt.power_plugged and batt.percent < BATT_ALERT:
                alert = f"Battery at {batt.percent:.0f}%. Plug in."
                if should_interrupt("battery_critical", alert) and "batt" not in alerts_given:
                    alerts_given.add("batt")
                    pending_alerts.append(alert)

            # Reset some alerts
            if session < 10:
                alerts_given.discard("break")
            if idle < 5:
                alerts_given.discard("idle")

        except Exception as e:
            print(f"Monitor: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN LOOP
# ══════════════════════════════════════════════════════════════════════════════

def jarvis_loop(gui):
    global last_interaction, scheduled_alerts, code_watcher
    pending_voice  = []
    voice_event    = threading.Event()
    pending_alerts = []

    # Initialize conversation
    refresh_system_prompt()

    def voice_listener():
        while True:
            try:
                if not VOICE_ASSISTANT_ENABLED.is_set():
                    time.sleep(0.1)
                    continue
                # CRITICAL: Don't listen while JARVIS speaks
                if TTS_MUTED.is_set():
                    time.sleep(0.08)
                    continue
                gui.set_state("idle")
                audio = record_until_silence()
                if not audio:
                    continue
                gui.set_state("listening")
                text = transcribe(audio)
                if not text:
                    continue
                print(f"Heard: {text}")
                if is_wake_word(text):
                    speak("Yes?", gui)
                    gui.set_state("listening")
                    audio2 = record_until_silence()
                    if audio2:
                        cmd = transcribe(audio2)
                        if cmd:
                            pending_voice.append((cmd, audio2))
                            voice_event.set()
            except Exception as e:
                print(f"Voice error: {e}")

    # Start all background threads
    threading.Thread(target=voice_listener,                            daemon=True).start()
    threading.Thread(target=proactive_monitor, args=(pending_alerts,), daemon=True).start()
    threading.Thread(target=update_hsl_engine,                        daemon=True).start()
    threading.Thread(target=delta_frame_pipeline,                     daemon=True).start()
    threading.Thread(target=l1_profile.synthesize_from_memory,        daemon=True).start()

    print("All AGI systems online.")

    while True:
        try:
            if STOP_SIGNAL.is_set():
                STOP_SIGNAL.clear()
                pending_voice.clear()
                while pending_alerts:
                    pending_alerts.pop()
                while scheduled_alerts:
                    scheduled_alerts.pop()
                speak_stop()
                gui.set_state("idle")
                continue

            if code_watcher is not None:
                try:
                    pending_alerts.extend(code_watcher.get_pending_alerts())
                except Exception as e:
                    print(f"Code watcher poll error: {e}")

            while scheduled_alerts:
                pending_alerts.append(scheduled_alerts.pop(0))

            if pending_alerts:
                alert = pending_alerts.pop(0)
                send_desktop_notification("JARVIS", alert[:100])
                speak(alert, gui)
                with hsl_lock:
                    hsl.situational.last_intervention_mins = 0.0
                continue

            if gui.pending_text_input:
                user_input = gui.pending_text_input.pop(0)
                if user_input.lower() in ["quit", "exit", "goodbye", "bye"]:
                    speak("Goodbye.", gui)
                    gui.root.quit()
                    break
                # Check for feedback on interruptions
                if any(w in user_input.lower() for w in ["too much", "stop interrupting", "quiet"]):
                    adapt_threshold("stop")
                elif any(w in user_input.lower() for w in ["good catch", "helpful alert", "keep going"]):
                    adapt_threshold("good")
                response = process_request(user_input, gui)
                if STOP_SIGNAL.is_set():
                    STOP_SIGNAL.clear()
                    speak_stop()
                    gui.set_state("idle")
                    continue
                speak(response, gui)
                continue

            if voice_event.is_set():
                voice_event.clear()
                if pending_voice:
                    user_input, audio_file = pending_voice.pop(0)
                    if any(w in user_input.lower() for w in ["goodbye", "shut down", "exit"]):
                        speak("Goodbye.", gui)
                        gui.root.quit()
                        break
                    response = process_request(user_input, gui, audio_file)
                    if STOP_SIGNAL.is_set():
                        STOP_SIGNAL.clear()
                        speak_stop()
                        gui.set_state("idle")
                        continue
                    speak(response, gui)
                continue

            time.sleep(0.05)

        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"Loop error: {e}")


def terminal_mode():
    refresh_system_prompt()
    print("JARVIS terminal mode. Type to interact.")
    while True:
        try:
            user_input = input("\nYou: ").strip()
            if not user_input:
                continue
            if user_input.lower() in ["quit", "exit", "goodbye"]:
                speak("Goodbye.")
                break
            response = process_request(user_input)
            speak(response)
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"Error: {e}")


def main():
    global mem_stack, code_watcher, task_chain_engine, email_intelligence, omniparser_client, social_memory, model_router, routine_predictor, goal_engine, autonomy_planner, permission_policy, verification_layer, daily_review_engine, cms, symbolic, world, agent_sync, home, evaluator, benchmarks, moa, titan, constitution, cross_modal, agent_orchestrator, dyn_prompt, budget, prm, htn, safety, goal_stack, hsl_orchestrator, improver, causal_graph, causal, mm_memory, skill_lib, executor, alignment

    init_tts()

    # Shared state dict between JARVIS logic and HUD
    shared_state = {
        "status":          "idle",
        "last_response":   "",
        "last_input":      "",
        "intent":          "WAITING",
        "emotion":         "neutral",
        "focus":           0.5,
        "hsl_frustration": 0.0,
        "memory_count":    memory.count(),
        "screen_context":  "unknown",
        "pending_text":    [],
    }

    print("═" * 60)
    print("  JARVIS Phase 12 — First Step Toward AGI")
    print(f"  LLM: {'LOCAL (Ollama: ' + LOCAL_MODEL + ')' if USE_LOCAL_LLM else 'GROQ'}")
    print(f"  Memory (L0): {memory.count()} entries")
    print(f"  L1 Profile: {'active' if l1_profile.get_profile_string() else 'building'}")
    print("  HSL Engine: starting...")
    print("  Delta-frame vision: starting...")
    print("  Three-gate interruption: active")
    print("  TTS Engine: starting...")
    print("═" * 60)

    setup_terminal_error_logging()

    mem_stack = MemoryStack(llm_fn=llm_call)
    start_nightly_scheduler(mem_stack)

    try:
        cms = ContinuumMemorySystem(llm_fn=llm_call)
        print("Continuum memory system active.")
    except Exception as e:
        cms = None
        print(f"Continuum memory system unavailable: {e}")

    # Phase 16 core systems.
    try:
        model_router = ModelRouter()
        print("Model router active.")
    except Exception as e:
        model_router = None
        print(f"Model router unavailable: {e}")

    # Initialize social relationship memory graph.
    try:
        social_memory = SocialMemory(llm_fn=llm_call)
        print("Social memory active.")
    except Exception as e:
        social_memory = None
        print(f"Social memory unavailable: {e}")

    # RoutinePredictor deleted during consolidation
    routine_predictor = None
    #try:
    #    routine_predictor = RoutinePredictor(
    #        execute_fn=lambda a: process_request(a),
    #        speak_fn=speak,
    #        should_interrupt_fn=should_interrupt,
    #    )
    #    routine_predictor.start()
    #    print("Routine predictor active.")
    #except Exception as e:
    #    routine_predictor = None
    #    print(f"Routine predictor unavailable: {e}")

    # Phase 17 systems.
    try:
        goal_engine = GoalEngine()
        print("Goal engine active.")
    except Exception as e:
        goal_engine = None
        print(f"Goal engine unavailable: {e}")

    try:
        autonomy_planner = AutonomyPlanner(llm_fn=llm_call)
        print("Autonomy planner active.")
    except Exception as e:
        autonomy_planner = None
        print(f"Autonomy planner unavailable: {e}")

    # PermissionPolicy deleted during consolidation
    permission_policy = None
    #try:
    #    permission_policy = PermissionPolicy()
    #    print("Permission policy active.")
    #except Exception as e:
    #    permission_policy = None
    #    print(f"Permission policy unavailable: {e}")

    # VerificationLayer deleted during consolidation
    verification_layer = None
    #try:
    #    verification_layer = VerificationLayer()
    #    print("Verification layer active.")
    #except Exception as e:
    #    verification_layer = None
    #    print(f"Verification layer unavailable: {e}")

    # DailyReviewEngine deleted during consolidation
    daily_review_engine = None
    #try:
    #    daily_review_engine = DailyReviewEngine(llm_fn=llm_call)
    #    print("Daily review engine active.")
    #except Exception as e:
    #    daily_review_engine = None
    #    print(f"Daily review engine unavailable: {e}")

    # SymbolicReasoner deleted during consolidation
    symbolic = None
    #try:
    #    symbolic = SymbolicReasoner()
    #    print("Symbolic reasoner active.")
    #except Exception as e:
    #    symbolic = None
    #    print(f"Symbolic reasoner unavailable: {e}")

    try:
        world = WorldModel()
        world.start_background_scan()
        print("World model active.")
    except Exception as e:
        world = None
        print(f"World model unavailable: {e}")

    try:
        home = HomeAssistantClient()
        home.connect()
        print("Home Assistant client initialized.")
    except Exception as e:
        home = None
        print(f"Home Assistant unavailable: {e}")

    # AGIBenchmarks deleted during consolidation
    benchmarks = None
    #try:
    #    benchmarks = AGIBenchmarks()
    #    print("AGI benchmarks active.")
    #except Exception as e:
    #    benchmarks = None
    #    print(f"AGI benchmarks unavailable: {e}")

    # SelfEvaluator deleted during consolidation
    evaluator = None
    #try:
    #    evaluator = SelfEvaluator(
    #        process_request_fn=lambda q: process_request(q),
    #        fast_intent_fn=fast_intent,
    #        symbolic_fn=symbolic,
    #    )
    #    evaluator.start_weekly_schedule()
    #    print("Self-evaluator active.")
    #except Exception as e:
    #    evaluator = None
    #    print(f"Self-evaluator unavailable: {e}")

    try:
        create_reasoning_modelfile()
        print("Reasoning distillation Modelfile ready.")
    except Exception as e:
        print(f"Reasoning distillation setup skipped: {e}")

    try:
        moa = MixtureOfAgents(use_moa=True)
        print("Mixture of Agents active.")
    except Exception as e:
        moa = None
        print(f"Mixture of Agents unavailable: {e}")

    try:
        titan = TitanMemoryModule()
        print("Titan memory active.")
    except Exception as e:
        titan = None
        print(f"Titan memory unavailable: {e}")

    # ConstitutionalFilter deleted during consolidation
    constitution = None
    #try:
    #    constitution = ConstitutionalFilter(use_llm_critic=True,
    #                                        critic_model="qwen2.5:1.5b")
    #    print("Constitutional filter active.")
    #except Exception as e:
    #    constitution = None
    #    print(f"Constitutional filter unavailable: {e}")

    try:
        cross_modal = CrossModalFusion(hsl_ref=hsl)
        cross_modal.start_calendar_poll(interval_secs=300)
        print("Cross-modal fusion active.")
    except Exception as e:
        cross_modal = None
        print(f"Cross-modal fusion unavailable: {e}")

    try:
        agent_orchestrator = AgentOrchestrator(llm_fn=llm_call)
        agent_orchestrator.start()
        print("Agent graph online.")
    except Exception as e:
        agent_orchestrator = None
        print(f"Agent graph unavailable: {e}")

    # DynamicPromptBuilder deleted during consolidation
    dyn_prompt = None
    #try:
    #    dyn_prompt = DynamicPromptBuilder(cms_ref=cms)
    #    print("Dynamic prompt builder active.")
    #except Exception as e:
    #    dyn_prompt = None
    #    print(f"Dynamic prompt builder unavailable: {e}")

    try:
        budget = ComputeBudget()
        print("Compute budget tracker active.")
    except Exception as e:
        budget = None
        print(f"Compute budget tracker unavailable: {e}")

    # ProcessRewardModel deleted during consolidation
    prm = None
    #try:
    #    prm = ProcessRewardModel(llm_fn=llm_call, max_steps=8, max_retries=2)
    #    print("Process Reward Model active (verifiable reasoning).")
    #except Exception as e:
    #    prm = None
    #    print(f"Process Reward Model unavailable: {e}")

    try:
        goal_stack = GoalStack(llm_fn=llm_call)
        print("Goal stack active.")
    except Exception as e:
        goal_stack = None
        print(f"Goal stack unavailable: {e}")

    # SafetyFoundation deleted during consolidation
    safety = None
    #try:
    #    safety = SafetyFoundation()
    #    print("Safety foundations active.")
    #except Exception as e:
    #    safety = None
    #    print(f"Safety foundations unavailable: {e}")

    try:
        htn = HTNPlanner(llm_fn=llm_call, execute_fn=lambda cmd: process_request(cmd))
        print("HTN planner active.")
    except Exception as e:
        htn = None
        print(f"HTN planner unavailable: {e}")

    try:
        import socket
        device_name = socket.gethostname().lower().replace(" ", "_")
        hsl_orchestrator = HSLOrchestrator(
            device_name=device_name,
            hsl_ref=hsl,
            goal_stack_ref=goal_stack,
            l3_fn=lambda n: mem_stack._get_recent_l3(n),
        )
        peers_env = os.getenv("JARVIS_PEERS", "")
        for peer in peers_env.split(","):
            if ":" in peer:
                parts = peer.strip().split(":")
                if len(parts) == 2:
                    hsl_orchestrator.register_peer(parts[0], parts[1])
        hsl_orchestrator.start_sync_loop()
        print("HSL orchestrator active.")
    except Exception as e:
        hsl_orchestrator = None
        print(f"HSL orchestrator unavailable: {e}")

    # SelfImprovementEngine deleted during consolidation
    improver = None
    #try:
    #    improver = SelfImprovementEngine(llm_fn=llm_call)
    #    print("Self-improvement engine active.")
    #except Exception as e:
    #    improver = None
    #    print(f"Self-improvement engine unavailable: {e}")

    # Phase 20 — Sleeping agents, causal reasoning, skill acquisition, multimodal memory, alignment
    # CausalGraph, CausalReasoner deleted during consolidation
    causal_graph = None
    causal = None
    #try:
    #    causal_graph = CausalGraph()
    #    causal = CausalReasoner(causal_graph, llm_fn=llm_call)
    #    print("Causal engine active (L1/L2/L3 reasoning).")
    #except Exception as e:
    #    causal_graph = None
    #    causal = None
    #    print(f"Causal engine unavailable: {e}")

    try:
        mm_memory = MultimodalMemory()
        print("Multimodal memory active.")
    except Exception as e:
        mm_memory = None
        print(f"Multimodal memory unavailable: {e}")

    # SkillLibrary deleted during consolidation
    skill_lib = None
    #try:
    #    skill_lib = SkillLibrary(llm_fn=llm_call, execute_fn=lambda cmd: process_request(cmd))
    #    print("Skill library active.")
    #except Exception as e:
    #    skill_lib = None
    #    print(f"Skill library unavailable: {e}")

    try:
        executor = PersistentExecutor(
            execute_fn=lambda cmd: process_request(cmd),
            speak_fn=speak,
            llm_fn=llm_call
        )
        executor.resume_sleeping_threads()
        print("Persistent executor active (sleeping agents).")
    except Exception as e:
        executor = None
        print(f"Persistent executor unavailable: {e}")

    # AlignmentChecker deleted during consolidation
    alignment = None
    #try:
    #    alignment = AlignmentChecker(
    #        llm_fn=llm_call,
    #        l4_meta=mem_stack.l4 if mem_stack else None,
    #        correction_log_path="./safety_logs/corrections.jsonl"
    #    )
    #    alignment.start_quarterly_schedule(speak)
    #    print("Alignment checkpoint active (quarterly audit).")
    #except Exception as e:
    #    alignment = None
    #    print(f"Alignment checkpoint unavailable: {e}")

    # Register shutdown hook for persistent executor
    import atexit
    if executor:
        atexit.register(executor.sleep_all)

    # Cross-agent sync (optional): set JARVIS_PEERS=ip1,ip2 in .env.
    peers = [p.strip() for p in os.getenv("JARVIS_PEERS", "").split(",") if p.strip()]
    if peers:
        try:
            agent_sync = AgentSyncServer(
                hsl_ref=hsl,
                cms_ref=cms,
                l3_facts_fn=lambda n: mem_stack._get_recent_l3(n),
            )
            agent_sync.start_server()
            for peer in peers:
                agent_sync.add_peer(peer)
            agent_sync.start_sync_loop()
            print(f"Agent sync active with {len(peers)} peers.")
        except Exception as e:
            agent_sync = None
            print(f"Agent sync unavailable: {e}")

    # Initialize OmniParser client for Gap 3 screen understanding.
    try:
        omniparser_client = OmniParserClient(llm_fn=llm_call)
        print("OmniParser client active.")
    except Exception as e:
        omniparser_client = None
        print(f"OmniParser unavailable: {e}")

    # Start Gmail -> memory intelligence ingestion.
    # EmailIntelligence deleted during consolidation
    email_intelligence = None
    #try:
    #    email_intelligence = EmailIntelligence(llm_fn=llm_call, memory_stack=mem_stack)
    #    email_intelligence.start()
    #    print("Email intelligence active.")
    #except Exception as e:
    #    email_intelligence = None
    #    print(f"Email intelligence unavailable: {e}")

    try:
        start_monthly_scheduler(mem_stack)
    except Exception as e:
        print(f"Monthly LoRA scheduler unavailable: {e}")

    # Apply persisted startup preferences (backend/persona/prosody)
    try:
        print(apply_startup_preferences())
    except Exception as e:
        print(f"Startup preference apply failed: {e}")

    # Start proactive real-time code error watcher.
    # CodeErrorWatcher deleted during consolidation
    code_watcher = None
    #try:
    #    code_watcher = CodeErrorWatcher(
    #        llm_fn=llm_call,
    #        speak_fn=speak,
    #        should_interrupt_fn=should_interrupt,
    #    )
    #    code_watcher.start()
    #except Exception as e:
    #    code_watcher = None
    #    print(f"Code watcher unavailable: {e}")

    # Start proactive task chain engine.
    # TaskChainEngine deleted during consolidation
    task_chain_engine = None
    #try:
    #    task_chain_engine = TaskChainEngine(
    #        llm_fn=llm_call,
    #        speak_fn=speak,
    #        action_executor=lambda action: process_request(action, gui=None),
    #    )
    #    task_chain_engine.start_monitor()
    #    print("Task chain engine active.")
    #except Exception as e:
    #    task_chain_engine = None
    #    print(f"Task chain engine unavailable: {e}")

    # Ask user: full screen HUD or compact window?
    mode = os.getenv("JARVIS_MODE", "compact")  # Set JARVIS_MODE=fullscreen for HUD

    if mode == "fullscreen":
        try:
            hud_module = __import__("jarvis_hud", fromlist=["IronManHUD"])
            IronManHUD = getattr(hud_module, "IronManHUD")
            gui = IronManHUD(shared_state)
            threading.Thread(target=jarvis_loop, args=(gui,), daemon=True).start()
            gui.run()
        except Exception as e:
            print(f"HUD import failed: {e}. Falling back to compact mode.")
            gui = JarvisHUD()
            threading.Thread(target=jarvis_loop, args=(gui,), daemon=True).start()
            gui.run()
    else:
        try:
            gui = JarvisHUD()
            try:
                start_global_hotkeys(gui)
            except Exception as e:
                print(f"Hotkey startup failed: {e}")
            threading.Thread(target=jarvis_loop, args=(gui,), daemon=True).start()
            gui.run()
        except Exception as e:
            print(f"GUI failed: {e}")
            terminal_mode()
            return


if __name__ == "__main__":
    main()