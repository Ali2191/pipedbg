"""
JARVIS internal world model.
Builds a persistent map of the user's digital environment.
Actions planned against world model before execution.
This is the shift from reactive to anticipatory intelligence.
"""

import json
import threading
import subprocess
import time
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional

WM_FILE = Path("./memory_stack/world_model.json")


class WorldModel:
    """
    Persistent model of the user's digital world.
    Survives across sessions. Updates continuously.

    Tracks:
      - Installed applications (with usage frequency)
      - Active projects (directories with recent activity)
      - File locations (frequently referenced)
      - Schedule structure (calendar pattern)
      - Network context (home/work/travel)
      - App co-occurrence (what apps are used together)
    """

    def __init__(self):
        self._lock = threading.Lock()
        self.model = self._load()
        self._update_task = None

    def _load(self) -> dict:
        if WM_FILE.exists():
            try:
                return json.loads(WM_FILE.read_text())
            except Exception:
                pass
        return {
            "apps": {},           # {app_name: {uses: int, last_used: str, category: str}}
            "projects": {},       # {path: {name: str, last_active: str, language: str}}
            "files": {},          # {path: {accessed: int, last: str, type: str}}
            "schedule_pattern": {},  # {"09:00": ["usually works", "in meetings"]}
            "network": {"current": "unknown", "home_ssid": ""},
            "app_cooccurrence": {},  # {app: [co-opened-apps]}
            "last_full_scan": ""
        }

    def _save(self):
        WM_FILE.parent.mkdir(exist_ok=True)
        WM_FILE.write_text(json.dumps(self.model, indent=2))

    def scan_environment(self):
        """Full scan of digital environment. Runs at startup and hourly."""
        self._scan_apps()
        self._scan_projects()
        self._scan_network()
        with self._lock:
            self.model["last_full_scan"] = datetime.now().isoformat()
        self._save()
        print(
            f"World model updated: {len(self.model['apps'])} apps, "
            f"{len(self.model['projects'])} projects"
        )

    def _scan_apps(self):
        """Discover installed Mac apps."""
        try:
            result = subprocess.run(
                ["ls", "/Applications"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            apps = [a.replace(".app", "") for a in result.stdout.split("\n") if a.endswith(".app")]
            with self._lock:
                for app in apps:
                    if app not in self.model["apps"]:
                        category = self._categorize_app(app)
                        self.model["apps"][app] = {
                            "uses": 0,
                            "last_used": "",
                            "category": category,
                        }
        except Exception as e:
            print(f"App scan: {e}")

    def _scan_projects(self):
        """Find active project directories."""
        try:
            projects_dir = Path.home() / "Projects"
            if projects_dir.exists():
                with self._lock:
                    for d in projects_dir.iterdir():
                        if d.is_dir() and not d.name.startswith("."):
                            # Detect primary language
                            lang = self._detect_language(d)
                            if str(d) not in self.model["projects"]:
                                self.model["projects"][str(d)] = {
                                    "name": d.name,
                                    "last_active": datetime.fromtimestamp(d.stat().st_mtime).isoformat(),
                                    "language": lang,
                                }
        except Exception as e:
            print(f"Project scan: {e}")

    def _scan_network(self):
        """Detect current network."""
        try:
            result = subprocess.run(
                ["networksetup", "-getairportnetwork", "en0"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            ssid = result.stdout.strip().replace("Current Wi-Fi Network: ", "")
            with self._lock:
                self.model["network"]["current"] = ssid
                if not self.model["network"]["home_ssid"] and ssid:
                    self.model["network"]["home_ssid"] = ssid  # First seen = home
        except Exception:
            pass

    def _categorize_app(self, app_name: str) -> str:
        categories = {
            "development": [
                "Code", "Xcode", "Terminal", "iTerm", "Cursor",
                "Sublime", "vim", "Neovim", "PyCharm", "WebStorm",
            ],
            "browser": ["Chrome", "Safari", "Firefox", "Arc", "Brave"],
            "communication": [
                "Slack", "Teams", "Messages", "WhatsApp",
                "Telegram", "Discord", "Zoom", "Mail",
            ],
            "productivity": [
                "Notion", "Obsidian", "Notes", "Calendar",
                "Reminders", "Bear",
            ],
            "creative": [
                "Figma", "Photoshop", "Illustrator", "Final Cut",
                "Logic", "GarageBand",
            ],
            "media": ["Spotify", "Music", "YouTube", "VLC", "IINA"],
        }
        for cat, apps in categories.items():
            if any(a.lower() in app_name.lower() for a in apps):
                return cat
        return "other"

    def _detect_language(self, directory: Path) -> str:
        counts = {
            "Python": len(list(directory.glob("**/*.py"))),
            "JavaScript": len(list(directory.glob("**/*.js"))) + len(list(directory.glob("**/*.jsx"))),
            "TypeScript": len(list(directory.glob("**/*.ts"))) + len(list(directory.glob("**/*.tsx"))),
            "Rust": len(list(directory.glob("**/*.rs"))),
            "Go": len(list(directory.glob("**/*.go"))),
        }
        if max(counts.values(), default=0) == 0:
            return "unknown"
        return max(counts, key=counts.get)

    def record_app_use(self, app_name: str):
        """Record that an app was used."""
        with self._lock:
            if app_name not in self.model["apps"]:
                self.model["apps"][app_name] = {
                    "uses": 0,
                    "last_used": "",
                    "category": self._categorize_app(app_name),
                }
            self.model["apps"][app_name]["uses"] += 1
            self.model["apps"][app_name]["last_used"] = datetime.now().isoformat()
        self._save()

    def plan_action(self, action_type: str, params: dict) -> dict:
        """
        Plan an action against the world model before execution.
        Returns augmented params with world model context.
        """
        augmented = dict(params)

        if action_type == "open_app":
            app = params.get("app", "")
            with self._lock:
                # Find closest installed app
                installed = list(self.model["apps"].keys())
                matches = [a for a in installed if app.lower() in a.lower()]
                if matches:
                    augmented["resolved_app"] = matches[0]
                    augmented["confidence"] = "high"
                else:
                    augmented["confidence"] = "low"

        elif action_type == "file_read":
            path = params.get("path", "")
            # Resolve relative paths against known projects
            if not path.startswith("/") and not path.startswith("~"):
                with self._lock:
                    for proj_path in self.model["projects"]:
                        candidate = Path(proj_path) / path
                        if candidate.exists():
                            augmented["resolved_path"] = str(candidate)
                            break

        return augmented

    def get_context_string(self) -> str:
        """Build world model context for LLM injection."""
        with self._lock:
            # Top 5 most-used apps
            apps_sorted = sorted(
                self.model["apps"].items(),
                key=lambda x: x[1].get("uses", 0),
                reverse=True,
            )[:5]
            top_apps = ", ".join(a[0] for a in apps_sorted) if apps_sorted else "none"

            # Active projects
            projects = list(self.model["projects"].values())[:3]
            proj_names = ", ".join(p["name"] for p in projects) if projects else "none"

            network = self.model["network"].get("current", "unknown")

        return f"[WORLD MODEL] Apps: {top_apps} | Projects: {proj_names} | Network: {network}"

    def start_background_scan(self):
        """Scan environment at startup and then hourly."""

        def _run():
            self.scan_environment()
            while True:
                time.sleep(3600)
                self.scan_environment()

        threading.Thread(target=_run, daemon=True).start()
