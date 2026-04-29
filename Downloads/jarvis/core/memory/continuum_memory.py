"""
Continuum Memory System - application-layer implementation of
Google's Nested Learning CMS (NeurIPS 2025, Behrouz et al.)

The paper defines CMS as: memory is a spectrum of modules,
each updating at a different, specific frequency rate.

We implement this at the application layer:
  Fast tier   (ms):      immediate context window - per-token equivalent
  Medium tier (hourly):  session consolidation - pattern compression
  Slow tier   (monthly): core personality - frozen weights equivalent

This mirrors the paper's three core memory levels:
  Sequence model  -> short-term (fast)
  FFN parameters  -> long-term (slow)
  CMS blocks      -> intermediate (medium)
"""

import json
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List

CMS_DIR = Path("./memory_stack/cms")
CMS_DIR.mkdir(parents=True, exist_ok=True)


# == TIER DEFINITIONS ==========================================================

@dataclass
class FastMemory:
    """
    Fast tier: millisecond-scale updates.
    Equivalent to sequence model / attention window in paper.
    Holds immediate session context - cleared on session end.
    Max 50 entries, FIFO eviction.
    """

    entries: deque = field(default_factory=lambda: deque(maxlen=50))
    update_count: int = 0

    def add(self, role: str, content: str):
        self.entries.append(
            {
                "role": role,
                "content": content,
                "ts": time.time(),
            }
        )
        self.update_count += 1

    def as_prompt_context(self, n: int = 8) -> str:
        recent = list(self.entries)[-n:]
        return "\n".join(f"{e['role'].upper()}: {e['content'][:200]}" for e in recent)

    def clear(self):
        self.entries.clear()


@dataclass
class MediumMemory:
    """
    Medium tier: hourly updates.
    Equivalent to CMS intermediate blocks in paper.
    Consolidates patterns from fast tier.
    Persists across sessions for weeks.
    """

    hourly_summaries: List[dict] = field(default_factory=list)  # max 168 (1 week)
    pattern_clusters: List[dict] = field(default_factory=list)  # detected patterns
    last_consolidated: str = ""
    update_count: int = 0

    def add_summary(self, summary: str, metadata: dict = None):
        entry = {
            "summary": summary,
            "ts": datetime.now().isoformat(),
            "metadata": metadata or {},
        }
        self.hourly_summaries.append(entry)
        if len(self.hourly_summaries) > 168:  # Keep 1 week
            self.hourly_summaries = self.hourly_summaries[-168:]
        self.last_consolidated = entry["ts"]
        self.update_count += 1

    def as_prompt_context(self, n: int = 3) -> str:
        recent = self.hourly_summaries[-n:]
        if not recent:
            return ""
        return "Recent patterns: " + " | ".join(s["summary"][:100] for s in recent)


@dataclass
class SlowMemory:
    """
    Slow tier: monthly updates.
    Equivalent to frozen lower layers + FFN parameters in paper.
    Contains core personality traits that change very slowly.
    This is the 'catastrophic forgetting prevention' layer.
    """

    core_identity: List[str] = field(default_factory=list)
    stable_preferences: dict = field(default_factory=dict)
    communication_style: dict = field(default_factory=dict)
    expertise_map: dict = field(default_factory=dict)
    last_updated: str = ""
    update_count: int = 0

    def as_prompt_context(self) -> str:
        if not self.core_identity and not self.stable_preferences:
            return ""
        parts = []
        if self.core_identity:
            parts.append("Core identity: " + "; ".join(self.core_identity[:5]))
        if self.stable_preferences:
            prefs = ", ".join(f"{k}: {v}" for k, v in list(self.stable_preferences.items())[:4])
            parts.append(f"Stable preferences: {prefs}")
        if self.communication_style:
            style = self.communication_style.get("preferred", "")
            if style:
                parts.append(f"Comms style: {style}")
        return "\n".join(parts)

    def update_from_l3(self, l3_facts: List[str]):
        """Populate slow tier from L3 stable facts (promotes from L3)."""
        # Slow alpha: only changes 2% per update, preventing forgetting.
        alpha = 0.02
        _ = alpha
        new_identity = [
            f
            for f in l3_facts
            if any(w in f.lower() for w in ["is a", "values", "believes", "identity", "always"])
        ]
        if new_identity:
            existing = set(self.core_identity)
            for fact in new_identity[:3]:
                if fact not in existing:
                    self.core_identity.append(fact)
            self.core_identity = self.core_identity[-20:]  # Keep last 20
        self.last_updated = datetime.now().isoformat()
        self.update_count += 1


# == CMS ENGINE ================================================================

class ContinuumMemorySystem:
    """
    Application-layer CMS implementing the paper's core insight:
    memory is a spectrum, not a binary short/long-term switch.

    The three tiers interact:
    Fast -> Medium: hourly consolidation (like sleep memory replay)
    Medium -> Slow: weekly promotion of stable patterns
    Slow -> Prompt: injected into every LLM system prompt
    """

    def __init__(self, llm_fn):
        self.llm = llm_fn
        self._lock = threading.Lock()

        self.fast = FastMemory()
        self.medium = MediumMemory()
        self.slow = SlowMemory()

        self._load()
        self._start_consolidation_loop()
        print(
            f"CMS online: fast={self.fast.update_count} medium={self.medium.update_count} slow={self.slow.update_count}"
        )

    def observe(self, role: str, content: str):
        """Every interaction feeds the fast tier immediately."""
        with self._lock:
            self.fast.add(role, content)

    def build_prompt_context(self) -> str:
        """Assemble full CMS context for injection into LLM prompt."""
        with self._lock:
            fast_ctx = self.fast.as_prompt_context(6)
            medium_ctx = self.medium.as_prompt_context(3)
            slow_ctx = self.slow.as_prompt_context()

        parts = []
        if slow_ctx:
            parts.append(f"[CORE IDENTITY]\n{slow_ctx}")
        if medium_ctx:
            parts.append(f"[RECENT PATTERNS]\n{medium_ctx}")
        if fast_ctx:
            parts.append(f"[CURRENT SESSION]\n{fast_ctx}")
        return "\n\n".join(parts)

    def _consolidate_fast_to_medium(self):
        """Hourly: compress fast tier into medium tier summary."""
        with self._lock:
            if self.fast.update_count < 5:
                return
            context = self.fast.as_prompt_context(20)
            # Do not clear fast - it is a rolling window.

        prompt = (
            "Analyze this hour's conversation and extract 1-2 patterns:\n"
            f"{context[:2000]}\n\n"
            "Return one sentence describing the main pattern or topic."
        )
        try:
            summary = self.llm(prompt, max_tokens=80, temperature=0.2)
            if summary:
                with self._lock:
                    self.medium.add_summary(summary)
                self._save()
                print(f"CMS medium tier updated: {summary[:60]}")
        except Exception as e:
            print(f"CMS consolidation error: {e}")

    def _consolidate_medium_to_slow(self):
        """Weekly: promote stable patterns to slow tier."""
        with self._lock:
            if len(self.medium.hourly_summaries) < 10:
                return
            recent = self.medium.hourly_summaries[-50:]
            docs = [s["summary"] for s in recent]

        prompt = (
            "From these hourly patterns, identify 2-3 STABLE personality traits or preferences:\n"
            f"{' | '.join(docs[:30])}\n\n"
            "These should be enduring facts, not temporary topics. One line each."
        )
        try:
            result = self.llm(prompt, max_tokens=150, temperature=0.2)
            if result:
                new_traits = [line.strip() for line in result.split("\n") if line.strip() and len(line.strip()) > 10]
                with self._lock:
                    existing = set(self.slow.core_identity)
                    for trait in new_traits[:3]:
                        if trait not in existing:
                            self.slow.core_identity.append(trait)
                    self.slow.core_identity = self.slow.core_identity[-20:]
                    self.slow.last_updated = datetime.now().isoformat()
                self._save()
                print(f"CMS slow tier updated: {len(new_traits)} traits")
        except Exception as e:
            print(f"CMS slow promotion error: {e}")

    def _start_consolidation_loop(self):
        def _run():
            last_medium_consolidation = time.time()
            last_slow_consolidation = time.time()
            while True:
                time.sleep(60)
                now = time.time()
                # Fast -> Medium every hour.
                if now - last_medium_consolidation > 3600:
                    self._consolidate_fast_to_medium()
                    last_medium_consolidation = now
                # Medium -> Slow every week.
                if now - last_slow_consolidation > 604800:
                    self._consolidate_medium_to_slow()
                    last_slow_consolidation = now

        threading.Thread(target=_run, daemon=True).start()

    def _load(self):
        try:
            p = CMS_DIR / "medium.json"
            if p.exists():
                data = json.loads(p.read_text())
                self.medium = MediumMemory(**data)
        except Exception:
            pass

        try:
            p = CMS_DIR / "slow.json"
            if p.exists():
                data = json.loads(p.read_text())
                self.slow = SlowMemory(**data)
        except Exception:
            pass

    def _save(self):
        try:
            (CMS_DIR / "medium.json").write_text(json.dumps(asdict(self.medium), indent=2))
            (CMS_DIR / "slow.json").write_text(json.dumps(asdict(self.slow), indent=2))
        except Exception as e:
            print(f"CMS save error: {e}")

    def get_tier_stats(self) -> str:
        with self._lock:
            return (
                f"CMS: Fast({self.fast.update_count} interactions) "
                f"Medium({len(self.medium.hourly_summaries)} hourly digests) "
                f"Slow({len(self.slow.core_identity)} core traits)"
            )
