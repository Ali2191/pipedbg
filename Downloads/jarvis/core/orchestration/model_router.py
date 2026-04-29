"""
Automatic task-to-model routing.
Simple -> Ollama local. Complex reasoning -> Claude API. Code -> CodeLlama.
"""

import os
from enum import Enum


class ModelTier(Enum):
    LOCAL_FAST = "local_fast"      # Qwen2.5:1.5b - simple tasks, instant
    LOCAL_SMART = "local_smart"    # Qwen2.5:7b - standard tasks
    CLOUD_SMART = "cloud_smart"    # Claude Sonnet - complex reasoning
    CODE = "code"                   # CodeLlama - code tasks


ROUTING_RULES = {
    # Simple tasks - local fast
    "open_app": ModelTier.LOCAL_FAST,
    "close_app": ModelTier.LOCAL_FAST,
    "spotify_play": ModelTier.LOCAL_FAST,
    "spotify_pause": ModelTier.LOCAL_FAST,
    "spotify_next": ModelTier.LOCAL_FAST,
    "screenshot": ModelTier.LOCAL_FAST,
    "lock_screen": ModelTier.LOCAL_FAST,
    "set_volume": ModelTier.LOCAL_FAST,
    "weather": ModelTier.LOCAL_FAST,
    "system_status": ModelTier.LOCAL_FAST,
    "home_turn_on": ModelTier.LOCAL_FAST,
    "home_turn_off": ModelTier.LOCAL_FAST,
    "home_dim": ModelTier.LOCAL_FAST,
    "home_temp": ModelTier.LOCAL_FAST,
    "home_status": ModelTier.LOCAL_FAST,
    "benchmark": ModelTier.LOCAL_FAST,
    "run_eval": ModelTier.LOCAL_FAST,
    "cms_stats": ModelTier.LOCAL_FAST,
    "world_model": ModelTier.LOCAL_FAST,
    # Standard tasks - local smart
    "search_web": ModelTier.LOCAL_SMART,
    "send_imessage": ModelTier.LOCAL_SMART,
    "create_reminder": ModelTier.LOCAL_SMART,
    "create_note": ModelTier.LOCAL_SMART,
    "calendar": ModelTier.LOCAL_SMART,
    "file_read": ModelTier.LOCAL_SMART,
    "camera": ModelTier.LOCAL_SMART,
    "chat": ModelTier.LOCAL_SMART,
    "goal_summary": ModelTier.LOCAL_SMART,
    "plan_request": ModelTier.LOCAL_SMART,
    "planner_status": ModelTier.LOCAL_SMART,
    "daily_review": ModelTier.LOCAL_SMART,
    "permission_status": ModelTier.LOCAL_SMART,
    "verification_stats": ModelTier.LOCAL_SMART,
    # Complex reasoning - cloud
    "deep_research": ModelTier.CLOUD_SMART,
    "summarize_doc": ModelTier.CLOUD_SMART,
    "behavioral_dashboard": ModelTier.CLOUD_SMART,
    # Code tasks - code model
    "fix_code": ModelTier.CODE,
    "run_code": ModelTier.CODE,
    "code_review": ModelTier.CODE,
}


class ModelRouter:
    def __init__(self):
        self.ollama_base = "http://localhost:11434"
        self.fast_model = "qwen2.5:1.5b"         # Or qwen2.5:0.5b for Pi
        self.smart_model = "qwen2.5:7b"
        self.code_model = "qwen2.5-coder:7b"     # ollama pull qwen2.5-coder:7b
        self.cloud_key = os.getenv("ANTHROPIC_API_KEY", "")
        self._stats = {}  # Track usage per model

    def route(self, intent: str) -> ModelTier:
        """Determine which model to use for this intent."""
        return ROUTING_RULES.get(intent, ModelTier.LOCAL_SMART)

    def call(self, messages: list, intent: str = "chat", max_tokens: int = 300) -> str:
        """Route to the appropriate model and call it."""
        tier = self.route(intent)

        if tier == ModelTier.LOCAL_FAST:
            return self._call_ollama(messages, self.fast_model, max_tokens)

        if tier == ModelTier.LOCAL_SMART:
            return self._call_ollama(messages, self.smart_model, max_tokens)

        if tier == ModelTier.CODE:
            # Try code model, fall back to smart.
            result = self._call_ollama(messages, self.code_model, max_tokens)
            if "error" in result.lower() or not result:
                return self._call_ollama(messages, self.smart_model, max_tokens)
            return result

        if tier == ModelTier.CLOUD_SMART:
            if self.cloud_key:
                return self._call_claude(messages, max_tokens)
            # Fall back to local smart.
            return self._call_ollama(messages, self.smart_model, max_tokens)

        return self._call_ollama(messages, self.smart_model, max_tokens)

    def _call_ollama(self, messages: list, model: str, max_tokens: int) -> str:
        import requests as req

        try:
            # Convert to Ollama format.
            system = next((m["content"] for m in messages if m["role"] == "system"), "")
            history = [(m["role"], m["content"]) for m in messages if m["role"] != "system"]
            prompt = "\n".join(f"{role.capitalize()}: {content}" for role, content in history)
            payload = {
                "model": model,
                "prompt": f"{system}\n\n{prompt}\n\nAssistant:" if system else prompt,
                "stream": False,
                "options": {"num_predict": max_tokens, "temperature": 0.7},
            }
            resp = req.post(f"{self.ollama_base}/api/generate", json=payload, timeout=60)
            resp.raise_for_status()
            result = resp.json().get("response", "").strip()
            self._stats[model] = self._stats.get(model, 0) + 1
            return result
        except Exception as e:
            return f"Model error ({model}): {e}"

    def _call_claude(self, messages: list, max_tokens: int) -> str:
        try:
            import anthropic

            client = anthropic.Anthropic(api_key=self.cloud_key)
            system = next((m["content"] for m in messages if m["role"] == "system"), "")
            msgs = [m for m in messages if m["role"] != "system"]
            resp = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=max_tokens,
                system=system,
                messages=msgs,
            )
            self._stats["claude"] = self._stats.get("claude", 0) + 1
            return resp.content[0].text
        except Exception as e:
            return f"Claude error: {e}"

    def usage_stats(self) -> str:
        if not self._stats:
            return "No model calls yet."
        return " | ".join(f"{model}: {count} calls" for model, count in self._stats.items())
