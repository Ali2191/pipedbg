"""Authentication and feature gating for pipedbg."""
from .license import License, LicenseError, get_license, is_pro, load_license, save_license
from .limits import UsageLimitError, check_ai_limit, get_usage_state
from .gate import ProFeatureError, require_pro, render_pro_message

__all__ = [
    "License",
    "LicenseError",
    "get_license",
    "is_pro",
    "load_license",
    "save_license",
    "UsageLimitError",
    "check_ai_limit",
    "get_usage_state",
    "ProFeatureError",
    "require_pro",
    "render_pro_message",
]
