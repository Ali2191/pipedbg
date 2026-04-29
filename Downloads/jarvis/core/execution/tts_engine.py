"""
Fixes: double audio, asyncio overhead, inter-sentence gap
Single worker thread + audio mutex = zero overlap guaranteed
"""

import asyncio
import os
import re
import threading
import tempfile
import subprocess
import edge_tts
from queue import Queue, Empty

# Global audio state
_audio_lock = threading.Lock()
_is_playing = threading.Event()
_play_queue = Queue()
_current_proc = None
_tts_muted = threading.Event()  # Mutes wake-word during TTS
_loop = None
_initialized = False

VOICE_PROFILES = {
    "jarvis": "en-US-AndrewNeural",
    "friday": "en-US-JennyNeural",
    "british": "en-GB-RyanNeural",
    "aria": "en-US-AriaNeural",
    "guy": "en-US-GuyNeural",
}
current_voice = "en-US-AndrewNeural"
current_persona = "jarvis"


def initialize():
    """Start persistent event loop + playback worker. Call once at startup."""
    global _loop, _initialized
    if _initialized:
        return
    _loop = asyncio.new_event_loop()
    threading.Thread(target=_loop.run_forever, daemon=True).start()
    threading.Thread(target=_playback_worker, daemon=True).start()
    _initialized = True
    print("TTS engine initialized.")


async def _gen_audio(sentence: str, voice: str) -> str:
    tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    tmp.close()
    await edge_tts.Communicate(sentence.strip(), voice=voice).save(tmp.name)
    return tmp.name


def _playback_worker():
    global _current_proc
    while True:
        try:
            filepath, callback = _play_queue.get(timeout=0.5)
        except Empty:
            continue
        try:
            _is_playing.set()
            _tts_muted.set()  # Mute wake-word
            _current_proc = subprocess.Popen(
                ["afplay", filepath],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            _current_proc.wait()
        except Exception as e:
            print(f"Playback error: {e}")
        finally:
            try:
                os.unlink(filepath)
            except Exception:
                pass
            _current_proc = None
            if _play_queue.empty():
                _is_playing.clear()
                _tts_muted.clear()  # Re-enable wake-word
            if callback:
                callback()
            _play_queue.task_done()


def _interrupt_current():
    while not _play_queue.empty():
        try:
            fp, _ = _play_queue.get_nowait()
            try:
                os.unlink(fp)
            except Exception:
                pass
        except Empty:
            break
    with _audio_lock:
        if _current_proc and _current_proc.poll() is None:
            _current_proc.terminate()


def stop():
    """Immediately stop current playback and clear queued audio."""
    _interrupt_current()
    _is_playing.clear()
    _tts_muted.clear()


def speak(text: str, gui=None, interrupt: bool = False, on_done=None):
    """
    Speak text with zero double-audio guarantee.
    All sentences generated in parallel, played sequentially by single worker.
    """
    if not text or text.strip() in ["None", ""]:
        return
    if not _initialized:
        initialize()

    print(f"JARVIS: {text}")
    if gui:
        gui.set_state("speaking")
        gui.show_response(text)
        gui.add_log("jarvis", text)

    sentences = [
        s.strip()
        for s in re.split(r"(?<=[.!?])\s+", text.strip())
        if s.strip() and len(s.strip()) > 2
    ]
    if not sentences:
        sentences = [text.strip()]

    if interrupt:
        _interrupt_current()

    voice = current_voice

    async def _gen_all():
        return await asyncio.gather(*[_gen_audio(s, voice) for s in sentences])

    try:
        audio_files = asyncio.run_coroutine_threadsafe(_gen_all(), _loop).result(timeout=20)
    except Exception as e:
        print(f"TTS generation failed: {e}")
        if gui:
            gui.set_state("idle")
        return

    for i, fpath in enumerate(audio_files):
        cb = None
        if i == len(audio_files) - 1:

            def cb(g=gui, d=on_done):
                if g:
                    g.set_state("idle")
                if d:
                    d()

        _play_queue.put((fpath, cb))


def set_voice(persona: str) -> str:
    global current_voice, current_persona
    persona = persona.lower().strip()
    if persona in VOICE_PROFILES:
        current_voice, current_persona = VOICE_PROFILES[persona], persona
        return f"Voice: {persona}."
    return f"Options: {', '.join(VOICE_PROFILES)}"


def is_muted() -> bool:
    """True when TTS playing - suppress wake-word detection."""
    return _tts_muted.is_set()
