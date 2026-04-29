"""
JARVIS MCP Tool Network
Connects JARVIS to 100+ tools via standard MCP protocol.
Our HSL schema is injected into every tool call as context.
"""

import subprocess
import json
import threading
from pathlib import Path

# MCP Server registry — all free/open source
MCP_SERVERS = {
    # Filesystem & Code
    "filesystem": {
        "command": ["npx", "-y", "@modelcontextprotocol/server-filesystem",
                    str(Path.home())],
        "description": "Read/write files on Mac"
    },
    # Web
    "puppeteer": {
        "command": ["npx", "-y", "@modelcontextprotocol/server-puppeteer"],
        "description": "Full browser automation"
    },
    "fetch": {
        "command": ["uvx", "mcp-server-fetch"],
        "description": "Fetch web pages"
    },
    # Data
    "sqlite": {
        "command": ["uvx", "mcp-server-sqlite", "--db-path", "./jarvis.db"],
        "description": "SQLite database operations"
    },
    # Productivity
    "github": {
        "command": ["npx", "-y", "@modelcontextprotocol/server-github"],
        "description": "GitHub repos, issues, PRs",
        "env": {"GITHUB_TOKEN": "$GITHUB_TOKEN"}
    },
    "notion": {
        "command": ["npx", "-y", "@modelcontextprotocol/server-notion"],
        "description": "Notion pages and databases",
        "env": {"NOTION_API_KEY": "$NOTION_API_KEY"}
    },
    # Memory
    "memory": {
        "command": ["npx", "-y", "@modelcontextprotocol/server-memory"],
        "description": "Persistent key-value memory"
    },
}


class MCPOrchestrator:
    """
    JARVIS MCP Orchestrator.
    Manages connections to all MCP servers.
    Injects HSL context into every tool call.
    This is where our Universal Context Schema contributes to MCP.
    """

    def __init__(self, hsl_ref, hsl_lock_ref=None):
        """
        hsl_ref: reference to the global HumanStateLayer object.
        hsl_lock_ref: optional threading.Lock for HSL access.
        Every tool call is enriched with current HSL state.
        """
        self.hsl      = hsl_ref
        self.hsl_lock = hsl_lock_ref or threading.Lock()
        self.servers  = {}
        self.running  = False

    def start(self):
        """Start all configured MCP servers."""
        self.running = True
        for name, config in MCP_SERVERS.items():
            threading.Thread(
                target=self._start_server,
                args=(name, config),
                daemon=True
            ).start()

    def _start_server(self, name: str, config: dict):
        try:
            import os
            env = os.environ.copy()
            for key, val in config.get("env", {}).items():
                if val.startswith("$"):
                    env[key] = os.getenv(val[1:], "")
                else:
                    env[key] = val

            proc = subprocess.Popen(
                config["command"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env
            )
            self.servers[name] = proc
            print(f"MCP server started: {name}")
        except Exception as e:
            print(f"MCP server failed ({name}): {e}")

    def call_tool(self, server: str, tool: str, params: dict) -> dict:
        """
        Call a tool on an MCP server.
        Automatically injects HSL context into every call.
        This is our original contribution: HSL rides on top of standard MCP.
        """
        # Inject HSL context into params
        with self.hsl_lock:
            hsl_context = {
                "_hsl_emotion":      self.hsl.emotional.dominant_emotion,
                "_hsl_focus":        self.hsl.cognitive.focus_level,
                "_hsl_screen":       self.hsl.behavioral.screen_context,
                "_hsl_task":         self.hsl.behavioral.active_task_type,
                "_hsl_frustration":  self.hsl.emotional.frustration_score,
            }
        params.update(hsl_context)

        # Send MCP JSON-RPC call
        call = {
            "jsonrpc": "2.0",
            "method":  "tools/call",
            "id":      1,
            "params":  {"name": tool, "arguments": params}
        }

        proc = self.servers.get(server)
        if not proc:
            return {"error": f"Server {server} not running"}

        try:
            proc.stdin.write((json.dumps(call) + "\n").encode())
            proc.stdin.flush()
            line   = proc.stdout.readline()
            result = json.loads(line)
            return result.get("result", {})
        except Exception as e:
            return {"error": str(e)}

    def list_tools(self, server: str) -> list:
        """List available tools on an MCP server."""
        call = {"jsonrpc": "2.0", "method": "tools/list", "id": 1, "params": {}}
        proc = self.servers.get(server)
        if not proc:
            return []
        try:
            proc.stdin.write((json.dumps(call) + "\n").encode())
            proc.stdin.flush()
            line   = proc.stdout.readline()
            result = json.loads(line)
            return result.get("result", {}).get("tools", [])
        except Exception:
            return []