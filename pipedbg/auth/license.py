"""
License validation and storage.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import jwt

LICENSE_DIR = Path.home() / ".pipedbg"
LICENSE_PATH = LICENSE_DIR / "license.json"

# Public key for offline validation (RS256)
PUBLIC_KEY = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAmNxcGifrpn12wwgU1ra9
kr7tmeqBcDEZ3XM95fMl/TXX3UUBUtbXBp6XDRxmmaPCvw9LOnvbk8QBBRpXWn/L
baTSfNqFeFoR/FMSnpesfniwZD3BJH30rmW9gYMg4AthVcQ0vXUysIXFjRqsWbQS
WrYD9H47eAS8/zOE3EC5jK3odbbD20U9rWqwA5UyOIPNnRr3HUEBYdnRmzy7xvym
GyOdzBnxHz4WAvp6iBRE3XvVMJQHq+epXqeMwPBeE/scXg9dRzPNpersGO1j1Gok
rdZcMIznvg+GddS1xg1e3VOBldJMpEDQLU7EEh45a8N8+BrY42FcarYLA5JKFoO6
twIDAQAB
-----END PUBLIC KEY-----"""


class LicenseError(Exception):
    pass


@dataclass
class License:
    email: str
    tier: str
    exp: int
    iat: int
    features: list[str]

    @property
    def expires_at(self) -> datetime:
        return datetime.fromtimestamp(self.exp, tz=timezone.utc)

    @property
    def issued_at(self) -> datetime:
        return datetime.fromtimestamp(self.iat, tz=timezone.utc)

    def is_expired(self) -> bool:
        return datetime.now(tz=timezone.utc).timestamp() > self.exp


def _load_license_file() -> dict[str, Any] | None:
    if not LICENSE_PATH.exists():
        return None
    try:
        return json.loads(LICENSE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_license_file(data: dict[str, Any]) -> None:
    LICENSE_DIR.mkdir(parents=True, exist_ok=True)
    LICENSE_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def validate_license_token(token: str) -> License:
    try:
        payload = jwt.decode(token, PUBLIC_KEY, algorithms=["RS256"], options={"verify_aud": False})
    except Exception as e:
        raise LicenseError(f"Invalid license token: {e}")

    required = {"sub", "tier", "exp", "iat"}
    if not required.issubset(payload.keys()):
        raise LicenseError("License payload missing required fields.")

    return License(
        email=str(payload.get("sub")),
        tier=str(payload.get("tier", "free")),
        exp=int(payload.get("exp")),
        iat=int(payload.get("iat")),
        features=list(payload.get("features", []) or []),
    )


def load_license() -> License | None:
    env_token = None
    try:
        import os
        env_token = os.environ.get("PIPEDBG_LICENSE_KEY")
    except Exception:
        env_token = None

    token = env_token
    if not token:
        data = _load_license_file()
        token = data.get("token") if isinstance(data, dict) else None

    if not token:
        return None

    try:
        lic = validate_license_token(token)
    except LicenseError:
        return None

    if lic.is_expired():
        return None
    return lic


def save_license(token: str) -> License:
    lic = validate_license_token(token)
    _write_license_file({"token": token})
    return lic


def get_license() -> License | None:
    return load_license()


def is_pro() -> bool:
    lic = load_license()
    if not lic:
        return False
    return lic.tier.lower() == "pro"
