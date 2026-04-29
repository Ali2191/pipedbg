"""
Multi-device HSL orchestration.
Mac JARVIS, Pi JARVIS, phone JARVIS share:
  - One HumanStateLayer
  - One goal stack
  - One L3 fact base
Tasks auto-migrate to the best device.
"""

import json
import threading
import time
from pathlib import Path
from datetime import datetime
from typing import Optional
import requests as http_req

HSL_SYNC_DIR = Path("./memory_stack/hsl_sync")
HSL_SYNC_DIR.mkdir(parents=True, exist_ok=True)


DEVICE_CAPABILITIES = {
    "mac":   {"compute": "high",   "display": True,  "camera": True, "mic": True,  "always_on": False, "gpu": True},
    "pi":    {"compute": "medium", "display": False, "camera": True, "mic": True,  "always_on": True,  "gpu": False},
    "phone": {"compute": "medium", "display": True,  "camera": True, "mic": True,  "always_on": True,  "gpu": False},
}

TASK_DEVICE_PREFERENCE = {
    "deep_research":     "mac",
    "fix_code":          "mac",
    "run_code":          "mac",
    "lora_training":     "mac",
    "schedule_reminder": "pi",
    "home_control":      "pi",
    "monitoring":        "pi",
    "location":          "phone",
}


class HSLOrchestrator:
    def __init__(self, device_name: str, hsl_ref, goal_stack_ref, l3_fn):
        self.device_name = device_name
        self.hsl = hsl_ref
        self.goal_stack = goal_stack_ref
        self.get_l3 = l3_fn
        self._peers = {}
        self._lock = threading.Lock()
        self._local_file = HSL_SYNC_DIR / f"{device_name}_hsl.json"

    def register_peer(self, device_name: str, ip: str, port: int = 8766):
        with self._lock:
            self._peers[device_name] = {"ip": ip, "port": port, "last_seen": None}
        print(f"HSL Orchestrator: peer registered: {device_name} @ {ip}")

    def build_shared_state(self) -> dict:
        state = {
            "device": self.device_name,
            "timestamp": datetime.now().isoformat(),
            "hsl": {},
            "goals": [g["title"] for g in self.goal_stack.active()[:5]] if self.goal_stack else [],
            "l3_facts": self.get_l3(5) if self.get_l3 else [],
        }
        try:
            state["hsl"] = {
                "emotion": self.hsl.emotional.dominant_emotion,
                "focus": self.hsl.cognitive.focus_level,
                "frustration": self.hsl.emotional.frustration_score,
                "screen": self.hsl.behavioral.screen_context[:100],
                "task": self.hsl.behavioral.active_task_type,
            }
        except Exception:
            pass
        return state

    def broadcast_state(self):
        state = self.build_shared_state()
        self._local_file.write_text(json.dumps(state, indent=2))
        with self._lock:
            peers = dict(self._peers)
        for device, info in peers.items():
            try:
                http_req.post(f"http://{info['ip']}:{info['port']}/sync", json=state, timeout=3)
                with self._lock:
                    self._peers[device]["last_seen"] = datetime.now().isoformat()
            except Exception:
                pass

    def get_peer_state(self, device_name: str) -> Optional[dict]:
        peer_file = HSL_SYNC_DIR / f"{device_name}_hsl.json"
        if peer_file.exists():
            try:
                return json.loads(peer_file.read_text())
            except Exception:
                pass
        return None

    def route_task(self, intent: str) -> str:
        preferred = TASK_DEVICE_PREFERENCE.get(intent, self.device_name)
        if preferred == self.device_name:
            return "local"
        with self._lock:
            peer = self._peers.get(preferred)
        if peer and peer.get("last_seen"):
            return preferred
        return "local"

    def delegate_task(self, device_name: str, intent: str, user_input: str) -> Optional[str]:
        with self._lock:
            peer = self._peers.get(device_name)
        if not peer:
            return None
        try:
            resp = http_req.post(f"http://{peer['ip']}:{peer['port']}/task", json={"intent": intent, "input": user_input}, timeout=30)
            return resp.json().get("result", "")
        except Exception as e:
            print(f"Task delegation to {device_name} failed: {e}")
            return None

    def start_sync_loop(self):
        def _run():
            while True:
                self.broadcast_state()
                time.sleep(300)
        threading.Thread(target=_run, daemon=True).start()
        print(f"HSL Orchestrator sync started (device: {self.device_name})")

    def peer_context_for_prompt(self) -> str:
        with self._lock:
            peer_names = list(self._peers.keys())
        parts = []
        for name in peer_names:
            state = self.get_peer_state(name)
            if state:
                hsl = state.get("hsl", {})
                parts.append(f"{name}: emotion={hsl.get('emotion','?')} task={hsl.get('task','?')}")
        return ("[PEER DEVICES] " + " | ".join(parts)) if parts else ""
