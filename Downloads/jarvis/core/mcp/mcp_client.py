"""
Correct MCP client. Servers launch via npx/uvx (Node.js stdio transport).
HSL context injected into every tool call - our Universal Context Schema contribution.
"""

import subprocess
import json
import threading
import os
import time
from pathlib import Path


class MCPClient:
    def __init__(self, config_path="config/mcp_servers.json"):
        self.config = self._load(config_path)
        self.servers = {}  # name -> Popen
        self._locks = {}  # name -> Lock
        self._id = 0
        self._idlock = threading.Lock()

    def _load(self, path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception as e:
            print(f"MCP config: {e}")
            return {"servers": {}}

    def _next_id(self):
        with self._idlock:
            self._id += 1
            return self._id

    def start(self, name: str) -> bool:
        cfg = self.config["servers"].get(name)
        if not cfg:
            return False
        env = os.environ.copy()
        for k, v in cfg.get("env", {}).items():
            resolved = v.replace("${HOME}", str(Path.home()))
            if resolved.startswith("${") and resolved.endswith("}"):
                resolved = os.getenv(resolved[2:-1], "")
            env[k] = resolved
        args = [a.replace("${HOME}", str(Path.home())) for a in cfg["args"]]
        try:
            proc = subprocess.Popen(
                [cfg["command"]] + args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                text=True,
            )
            self.servers[name] = proc
            self._locks[name] = threading.Lock()
            # Initialize handshake
            self._send(
                name,
                {
                    "jsonrpc": "2.0",
                    "method": "initialize",
                    "id": self._next_id(),
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "JARVIS", "version": "14.0"},
                    },
                },
            )
            print(f"MCP server started: {name}")
            return True
        except Exception as e:
            print(f"MCP {name} failed: {e}")
            return False

    def start_all(self):
        for name in self.config["servers"]:
            threading.Thread(target=self.start, args=(name,), daemon=True).start()
            time.sleep(0.3)

    def _send(self, server, msg) -> dict:
        proc = self.servers.get(server)
        if not proc or proc.poll() is not None:
            return {"error": f"{server} not running"}
        lock = self._locks.get(server, threading.Lock())
        with lock:
            try:
                proc.stdin.write(json.dumps(msg) + "\n")
                proc.stdin.flush()
                line = proc.stdout.readline()
                return json.loads(line) if line else {"error": "empty response"}
            except Exception as e:
                return {"error": str(e)}

    def call_tool(self, server: str, tool: str, params: dict, hsl=None) -> dict:
        """Call MCP tool, injecting HSL context (our contribution to MCP)."""
        if server not in self.servers:
            self.start(server)
            time.sleep(1)
        args = dict(params)
        if hsl:  # Universal Context Schema extension
            args["_hsl"] = hsl
        msg = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "id": self._next_id(),
            "params": {"name": tool, "arguments": args},
        }
        result = self._send(server, msg)
        return result.get("result", result)

    def list_tools(self, server: str) -> list:
        if server not in self.servers:
            self.start(server)
            time.sleep(1)
        r = self._send(
            server,
            {"jsonrpc": "2.0", "method": "tools/list", "id": self._next_id(), "params": {}},
        )
        return r.get("result", {}).get("tools", [])

    def status(self) -> str:
        parts = []
        for name, proc in self.servers.items():
            parts.append(f"{name}:{'OK' if proc.poll() is None else 'STOPPED'}")
        return " | ".join(parts) if parts else "No servers started"
