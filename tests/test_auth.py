"""
Tests for license validation and usage limits.
"""
from __future__ import annotations

import json
from pathlib import Path

import jwt
import pytest

from pipedbg.auth import LicenseError
from pipedbg.auth import limits
from pipedbg.auth.license import License, load_license, save_license, validate_license_token
from pipedbg.auth.gate import ProFeatureError, render_pro_message, require_pro

TEST_PRIVATE_KEY = """-----BEGIN PRIVATE KEY-----
MIIEvAIBADANBgkqhkiG9w0BAQEFAASCBKYwggSiAgEAAoIBAQCY3FwaJ+umfXbD
CBTWtr2Svu2Z6oFwMRndcz3l8yX9NdfdRQFS1tcGnpcNHGaZo8K/D0s6e9uTxAEF
Gldaf8ttpNJ82oV4WhH8UxKel6x+eLBkPcEkffSuZb2BgyDgC2FVxDS9dTKwhcWN
GqxZtBJatgP0fjt4BLz/M4TcQLmMreh1tsPbRT2tarADlTI4g82dGvcdQQFh2dGb
PLvG/KYbI53MGfEfPhYC+nqIFETde9UwlAer56lep4zA8F4T+xxeD11HM82l6uwY
7WPUaiSt1lwwjOe+D4Z11LXGDV7dU4GV0kykQNAtTsQSHjlrw3z4GtjjYVxqtgsD
kkoWg7q3AgMBAAECggEAOjzGKD7qVFF7lD15dv5DRmvQaTIDY4OJd6nGvNuArzI6
zjXSlcV9QavdH6Ug38sY0KLahesXUno76z5IZpXGorzHZsL4U8x5Crl5oAtoL/z6
Mw6mDamhNWpUo0mallEvobXxY/cJO2CTzbkKTdjBn2a2JgmLzaN8f/wYU7OjHZmy
7gl/zhZJVEX45FOCxAcCDZZHMhKYpEVIxoBdM8N00ya/4W8tUi2nF52dYjaE1Gv7
bRKk/YaDWFU+UN/YvbCK82HGkTPGFR5Wz21dQsM2NXQ1k9GmQxqvhwrn1GvnweIk
MrfydlkKA+21bIX8B7bdXxqEHIMup0H6IZVeD8+gkQKBgQDXXkRvr/Jqmip0SkIW
2q5lTGqaML9bQFsCV834Gxa4EK0gJRrKZgqEbsRK8zYfr4vBtS8bOauXJZAabZ09
cQD8LF1lGBG+vFvSeLwdOgoPFtq8PHplNvOlnf5D6gMJhbPWhgbbXIRhu5qYqfTb
r98Nb6nwygFaYU3ipKxRhwmSMQKBgQC1sySc//0XKLpJl9MAOLcaqeqrHohWN0C3
rHOvBAKXSjG5Y5qJaMXO8z5skVDtHKne7MvBLqr6dATy5Ahh7UWchuO2R1DxIkN4
XXhGe9uYrpDCIZfKt0mzzglcsGQq6FmNg6QUZrCKGLtWfg1xGDSfdJew9Bj4Cm+a
Oo6O5a05ZwKBgHfMeRcDcT45KVpsoBykYhP5EOdaLGdvAfDotKrJLrcOl67k1OU3
I6yNDOWAKmAvvvbueRiU2M0H2QPKa4fs3xZm+0CrxdsqXY1TGZjMWyIPnXbN0WuR
yLAclX5jonLei63N+ex1pzHSMGmxSIIXb2TC8238gAotTCzBWxUyn3FRAoGAGxCe
KYywBF0aso+c7HGGRMB+phKcOEtupm1XpgAw6pwwn+7IPCORI2x0JfPXXBpi60PW
beYnrbrOaeexn/SZ4+Dr1mD1G5YA+tLhcY5NfYazJVefpqB6p//OwTG9Ge8WN9Ae
BrPtJATfEtkf43K5k+7oEYGqnnffe9exGHP5w40CgYBhOZ7kEUlglAuVVkiZ2gNW
EoPo2L8pQ4C1betRAOC+beeB7yMAqDQ3kLW581Hs1yqiGJig0S1a+KZNvo5JWZuS
RkDfg1VVmb0DtyYGvf6lzFccWo+lnJaWpxL0F5bwsSC9nc3VgVtTI1JkqJLQRZTw
SYwRL0x7tLH5tBAG5anibg==
-----END PRIVATE KEY-----"""


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
