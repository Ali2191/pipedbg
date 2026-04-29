"""
Cross-device JARVIS agent synchronization.
Mac JARVIS, Pi JARVIS, and phone JARVIS share:
  - HSL state (emotional, cognitive, situational)
  - L3 stable facts
  - CMS slow tier (core identity)
  - Active task chains
Uses simple HTTP server - no cloud dependency.
"""

import json
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import requests as http_req

SYNC_PORT = 8766
SYNC_STATE = {}  # Shared state across all agents


class AgentSyncServer:
    """Runs on each JARVIS instance. Serves HSL + L3 state."""

    def __init__(self, hsl_ref, cms_ref, l3_facts_fn):
        self.hsl = hsl_ref
        self.cms = cms_ref
        self.get_l3_facts = l3_facts_fn
        self._server = None
        self._peers = []  # [(ip, port)] of other JARVIS instances
        self._lock = threading.Lock()
        self._last_sync = 0

    def add_peer(self, ip: str, port: int = SYNC_PORT):
        self._peers.append((ip, port))
        print(f"Agent peer added: {ip}:{port}")

    def start_server(self):
        sync_server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/state":
                    state = sync_server._build_state()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps(state).encode())
                else:
                    self.send_response(404)
                    self.end_headers()

            def do_POST(self):
                if self.path == "/sync":
                    length = int(self.headers.get("Content-Length", 0))
                    body = self.rfile.read(length)
                    data = json.loads(body)
                    sync_server._apply_peer_state(data)
                    self.send_response(200)
                    self.end_headers()

            def log_message(self, *args):
                pass  # Suppress HTTP logs

        self._server = HTTPServer(("0.0.0.0", SYNC_PORT), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        print(f"Agent sync server on port {SYNC_PORT}")

    def _build_state(self) -> dict:
        """Build the shared HSL + L3 state to broadcast."""
        from core.memory.continuum_memory import ContinuumMemorySystem

        _ = ContinuumMemorySystem
        state = {
            "device": self._get_device_id(),
            "timestamp": datetime.now().isoformat(),
            "hsl": {},
            "l3_facts": self.get_l3_facts(5) if self.get_l3_facts else [],
            "cms_slow": {},
        }
        # Add HSL if available
        try:
            state["hsl"] = {
                "emotion": self.hsl.emotional.dominant_emotion,
                "focus": self.hsl.cognitive.focus_level,
                "frustration": self.hsl.emotional.frustration_score,
                "screen": self.hsl.behavioral.screen_context,
                "task": self.hsl.behavioral.active_task_type,
            }
        except Exception:
            pass
        # Add CMS slow tier
        try:
            state["cms_slow"] = {
                "core_identity": self.cms.slow.core_identity[:10],
                "stable_prefs": dict(list(self.cms.slow.stable_preferences.items())[:5]),
                "comms_style": self.cms.slow.communication_style,
            }
        except Exception:
            pass
        return state

    def _apply_peer_state(self, peer_state: dict):
        """Integrate peer JARVIS state."""
        with self._lock:
            peer_id = peer_state.get("device", "unknown")
            SYNC_STATE[peer_id] = peer_state
        print(f"Synced with peer JARVIS: {peer_id}")

    def sync_with_peers(self):
        """Push current state to all peer instances."""
        state = self._build_state()
        for ip, port in self._peers:
            try:
                http_req.post(f"http://{ip}:{port}/sync", json=state, timeout=3)
            except Exception as e:
                print(f"Peer sync failed ({ip}:{port}): {e}")

    def start_sync_loop(self):
        """Sync with peers every 5 minutes."""

        def _run():
            while True:
                time.sleep(300)
                self.sync_with_peers()

        threading.Thread(target=_run, daemon=True).start()

    def _get_device_id(self) -> str:
        try:
            import socket

            return socket.gethostname()
        except Exception:
            return "unknown"

    def get_peer_context(self) -> str:
        """Get context from peer JARVIS instances for prompt injection."""
        with self._lock:
            if not SYNC_STATE:
                return ""
            parts = []
            for device, state in SYNC_STATE.items():
                hsl = state.get("hsl", {})
                if hsl:
                    parts.append(f"{device}: emotion={hsl.get('emotion','?')}, task={hsl.get('task','?')}")
            return ("[PEER AGENTS] " + " | ".join(parts)) if parts else ""
