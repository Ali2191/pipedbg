"""
True cross-modal understanding.
JARVIS sees screen, hears voice, reads calendar SIMULTANEOUSLY.
All three are fused into HSL before response generation.
"""

import threading
import time
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime
import subprocess


@dataclass
class ModalState:
    """State from a single modality."""
    modality:     str    # screen / voice / calendar / system
    content:      str
    confidence:   float
    timestamp:    float  = field(default_factory=time.time)
    is_fresh:     bool   = True  # False if >60s old


@dataclass
class FusedContext:
    """Unified multi-modal context fused for HSL injection."""
    screen_ctx:   str    = ""
    voice_emotion: str   = "neutral"
    calendar_ctx: str    = ""
    system_ctx:   str    = ""
    fused_summary: str   = ""
    fused_at:     str    = ""
    dominant_modality: str = "voice"  # Which modality drove the context


class CrossModalFusion:
    """
    Continuously collects signals from all modalities.
    Fuses them into a unified context for each LLM call.
    """

    def __init__(self, hsl_ref):
        self.hsl    = hsl_ref
        self._lock  = threading.Lock()
        self._states: dict = {}  # modality -> ModalState
        self._running = False

    def update(self, modality: str, content: str, confidence: float = 0.8):
        """Update a single modality's state."""
        with self._lock:
            self._states[modality] = ModalState(
                modality=modality, content=content,
                confidence=confidence, timestamp=time.time()
            )

    def fuse(self) -> FusedContext:
        """
        Fuse all current modal states into unified context.
        Staleness check: modality states >60s old are discarded.
        """
        now  = time.time()
        ctx  = FusedContext(fused_at=datetime.now().isoformat())

        with self._lock:
            states = dict(self._states)

        for modality, state in states.items():
            age = now - state.timestamp
            if age > 120:  # >2 minutes = stale
                continue

            if modality == "screen":
                ctx.screen_ctx = state.content[:200]
            elif modality == "voice_emotion":
                ctx.voice_emotion = state.content
            elif modality == "calendar":
                ctx.calendar_ctx = state.content[:200]
            elif modality == "system":
                ctx.system_ctx = state.content[:150]

        # Determine dominant modality (most recent with high confidence)
        if states:
            freshest = max(states.values(), key=lambda s: s.timestamp)
            ctx.dominant_modality = freshest.modality

        # Build fused summary
        parts = []
        if ctx.screen_ctx:
            parts.append(f"Screen: {ctx.screen_ctx}")
        if ctx.voice_emotion and ctx.voice_emotion != "neutral":
            parts.append(f"Mood: {ctx.voice_emotion}")
        if ctx.calendar_ctx:
            parts.append(f"Calendar: {ctx.calendar_ctx}")
        if ctx.system_ctx:
            parts.append(f"System: {ctx.system_ctx}")

        ctx.fused_summary = " | ".join(parts)
        return ctx

    def as_hsl_injection(self) -> str:
        """Build the cross-modal string for HSL/prompt injection."""
        fused = self.fuse()
        if not fused.fused_summary:
            return ""
        return f"[CROSS-MODAL] {fused.fused_summary}"

    def start_calendar_poll(self, interval_secs: int = 300):
        """Poll calendar every 5 minutes and update modal state."""
        def _run():
            while True:
                try:
                    cal = subprocess.run(
                        ["osascript", "-e",
                         'tell application "Calendar" to '
                         'return (summary of events of today of '
                         'calendar 1 as string)'],
                        capture_output=True, text=True, timeout=10
                    ).stdout.strip()
                    if cal:
                        self.update("calendar", cal[:200], 0.9)
                except Exception:
                    pass
                time.sleep(interval_secs)
        threading.Thread(target=_run, daemon=True).start()
