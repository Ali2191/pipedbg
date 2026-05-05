"""
Session sharing via ngrok or localtunnel.
"""
from __future__ import annotations

import os
import re
import socket
import subprocess
from typing import Optional

from ..auth.gate import require_pro


_tunnel_process: subprocess.Popen | None = None


@require_pro("session_sharing")
def get_share_url(port: int) -> str:
    url = _try_ngrok(port)
    if url:
        return url

    url = _try_localtunnel(port)
    if url:
        return url

    return _local_lan_url(port)


def _try_ngrok(port: int) -> Optional[str]:
    token = os.environ.get("NGROK_AUTH_TOKEN")
    if not token:
        return None
    try:
        from pyngrok import ngrok  # type: ignore
    except Exception:
        return None

    try:
        ngrok.set_auth_token(token)
        tunnel = ngrok.connect(port, "http")
        return tunnel.public_url
    except Exception:
        return None


def _try_localtunnel(port: int) -> Optional[str]:
    global _tunnel_process
    try:
        _tunnel_process = subprocess.Popen(
            ["npx", "localtunnel", "--port", str(port)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except Exception:
        return None

    if not _tunnel_process.stdout:
        return None

    pattern = re.compile(r"https?://[\w.-]+")
    for _ in range(20):
        line = _tunnel_process.stdout.readline().strip()
        if not line:
            continue
        match = pattern.search(line)
        if match:
            return match.group(0)

    return None


def _local_lan_url(port: int) -> str:
    ip = "127.0.0.1"
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        sock.close()
    except Exception:
        pass

    return f"http://{ip}:{port}"
