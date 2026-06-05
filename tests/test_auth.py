"""
Tests for license validation and usage limits.
"""
from __future__ import annotations

import json
from pathlib import Path
import os

import jwt
import pytest

from pipedbg.auth import LicenseError
from pipedbg.auth import limits
from pipedbg.auth.license import License, load_license, save_license, validate_license_token
from pipedbg.auth.gate import ProFeatureError, render_pro_message, require_pro

TEST_PRIVATE_KEY = os.environ.get('TEST_PRIVATE_KEY')


def _make_token(tier: str = "pro") -> str:
    payload = {
        "sub": "tester@example.com",
        "tier": tier,
        "features": ["multi_platform", "session_sharing"],
        "iat": 1714867200,
        "exp": 1893456000,
    }
    return jwt.encode(payload, TEST_PRIVATE_KEY, algorithm="RS256")


@pytest.fixture(autouse=True)
def isolate_paths(tmp_path, monkeypatch):
    limits.USAGE_DIR = tmp_path / ".pipedbg"
    limits.USAGE_PATH = limits.USAGE_DIR / "usage.json"
    limits.AUDIT_PATH = limits.USAGE_DIR / "audit.jsonl"

    from pipedbg.auth import license as lic
    lic.LICENSE_DIR = tmp_path / ".pipedbg"
    lic.LICENSE_PATH = lic.LICENSE_DIR / "license.json"
    yield


def test_license_validation_roundtrip():
    token = _make_token()
    lic = save_license(token)
    assert lic.tier == "pro"

    loaded = load_license()
    assert loaded is not None
    assert loaded.tier == "pro"


def test_invalid_license_rejected():
    with pytest.raises(LicenseError):
        validate_license_token("bad.token")


def test_usage_limits_free():
    token = _make_token(tier="free")
    save_license(token)
    limits.FREE_AI_LIMIT = 1
    limits.check_ai_limit()
    with pytest.raises(limits.UsageLimitError):
        limits.check_ai_limit()


def test_pro_feature_decorator():
    @require_pro("session_sharing")
    def protected():
        return True

    with pytest.raises(ProFeatureError):
        protected()

    msg = render_pro_message("session_sharing")
    assert "Upgrade" in msg