"""
Predictive compute budgeting.
Predicts task difficulty at intent-parse time.
Allocates compute proportionally.
  TRIVIAL:  symbolic rule    — 0ms    (time, math, date)
  SIMPLE:   local fast model — 100ms  (open_app, spotify)
  STANDARD: local smart model— 1-3s   (search, chat)
  COMPLEX:  MoA 3-proposer   — 3-8s   (research, code)
  CRITICAL: MoA + cloud      — 5-15s  (deep analysis)
"""

from enum import Enum
from typing import Tuple


class ComputeTier(Enum):
    TRIVIAL  = 0   # Symbolic rules only
    SIMPLE   = 1   # Single fast local model
    STANDARD = 2   # Single smart local model
    COMPLEX  = 3   # MoA three-proposer
    CRITICAL = 4   # MoA + cloud fallback


INTENT_BUDGETS = {
    # Trivial — handled by symbolic rules, no LLM needed
    "time_query":      ComputeTier.TRIVIAL,
    "date_query":      ComputeTier.TRIVIAL,
    "math_query":      ComputeTier.TRIVIAL,
    "file_exists":     ComputeTier.TRIVIAL,
    "battery_check":   ComputeTier.TRIVIAL,

    # Simple — single fast model
    "open_app":        ComputeTier.SIMPLE,
    "close_app":       ComputeTier.SIMPLE,
    "spotify_play":    ComputeTier.SIMPLE,
    "spotify_next":    ComputeTier.SIMPLE,
    "spotify_prev":    ComputeTier.SIMPLE,
    "spotify_pause":   ComputeTier.SIMPLE,
    "screenshot":      ComputeTier.SIMPLE,
    "lock_screen":     ComputeTier.SIMPLE,
    "volume_control":  ComputeTier.SIMPLE,
    "stop":            ComputeTier.SIMPLE,

    # Standard — single smart model
    "chat":            ComputeTier.STANDARD,
    "search_web":      ComputeTier.STANDARD,
    "weather":         ComputeTier.STANDARD,
    "system_status":   ComputeTier.STANDARD,
    "create_reminder": ComputeTier.STANDARD,
    "create_note":     ComputeTier.STANDARD,
    "send_imessage":   ComputeTier.STANDARD,
    "calendar":        ComputeTier.STANDARD,
    "camera":          ComputeTier.STANDARD,
    "screen_read":     ComputeTier.STANDARD,
    "file_read":       ComputeTier.STANDARD,
    "file_list":       ComputeTier.STANDARD,

    # Complex — MoA three-proposer
    "deep_research":   ComputeTier.COMPLEX,
    "fix_code":        ComputeTier.COMPLEX,
    "run_code":        ComputeTier.COMPLEX,
    "code_review":     ComputeTier.COMPLEX,
    "summarize_doc":   ComputeTier.COMPLEX,
    "multi_step":      ComputeTier.COMPLEX,

    # Critical — MoA + cloud if available
    "behavioral_dashboard": ComputeTier.CRITICAL,
    "research_topic":       ComputeTier.CRITICAL,
}

TIER_CONFIGS = {
    ComputeTier.TRIVIAL:  {"model": "symbolic",      "max_tokens": 0,   "use_moa": False, "timeout": 0.1},
    ComputeTier.SIMPLE:   {"model": "qwen2.5:1.5b",  "max_tokens": 100, "use_moa": False, "timeout": 2},
    ComputeTier.STANDARD: {"model": "qwen2.5:7b",    "max_tokens": 300, "use_moa": False, "timeout": 10},
    ComputeTier.COMPLEX:  {"model": "qwen2.5:7b",    "max_tokens": 600, "use_moa": True,  "timeout": 20},
    ComputeTier.CRITICAL: {"model": "cloud_or_local", "max_tokens": 900, "use_moa": True,  "timeout": 30},
}


class ComputeBudget:
    def __init__(self):
        self._usage = {}   # tier -> [latency_ms]

    def get_tier(self, intent: str, user_input: str = "") -> ComputeTier:
        """Predict compute tier for this intent."""
        base_tier = INTENT_BUDGETS.get(intent, ComputeTier.STANDARD)

        # Upgrade tier for complex user inputs
        word_count = len(user_input.split())
        if word_count > 20 and base_tier == ComputeTier.STANDARD:
            base_tier = ComputeTier.COMPLEX

        # Keywords that always need complex reasoning
        complex_keywords = [
            "deeply", "thoroughly", "comprehensive", "detailed",
            "explain everything", "full analysis", "complete"
        ]
        if any(kw in user_input.lower() for kw in complex_keywords):
            if base_tier.value < ComputeTier.COMPLEX.value:
                base_tier = ComputeTier.COMPLEX

        return base_tier

    def get_config(self, intent: str,
                   user_input: str = "") -> Tuple[ComputeTier, dict]:
        """Get tier and configuration for this intent."""
        tier   = self.get_tier(intent, user_input)
        config = TIER_CONFIGS[tier].copy()
        return tier, config

    def record(self, tier: ComputeTier, latency_ms: float):
        key = tier.name
        if key not in self._usage:
            self._usage[key] = []
        self._usage[key].append(latency_ms)
        self._usage[key] = self._usage[key][-50:]

    def stats(self) -> str:
        lines = []
        for tier_name, latencies in self._usage.items():
            if latencies:
                avg = sum(latencies) / len(latencies)
                lines.append(f"{tier_name}: avg {avg:.0f}ms ({len(latencies)} calls)")
        return "Compute budget: " + " | ".join(lines) if lines else "No data yet"
