"""Load private candidate matching preferences without logging their contents."""

import json
import os
import re
from pathlib import Path


DEFAULT_PRIVATE_FILE = Path(__file__).resolve().parent / "private/profiles.local.json"
PUBLIC_PROFILE_IDS = tuple(f"profile_{index:02d}" for index in range(1, 5))
ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
LOCATIONS = {
    "toronto_gta", "ottawa", "remote_canada", "canada_unspecified",
    "other_canadian_city", "us", "unknown",
}
ROLE_FAMILIES = {
    "frontend", "backend", "full_stack", "general_swe", "systems",
    "distributed_systems", "data", "ml_infra", "ml_systems", "ai_software",
    "embedded", "networking", "telecom", "hardware_design",
    "design_verification", "rtl", "asic", "fpga", "pcb", "circuits",
    "robotics_hardware", "semiconductor", "validation",
}


class ProfileError(ValueError):
    """Profile source is absent or invalid. Message never includes source contents."""


def _string_list(value, allowed=None):
    return (isinstance(value, list) and all(
        isinstance(item, str) and bool(item.strip())
        and (allowed is None or item in allowed)
        for item in value
    ))


def validate_profiles(data):
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ProfileError("Invalid candidate profile schema")
    profiles = data.get("profiles")
    if not isinstance(profiles, list) or not profiles:
        raise ProfileError("Invalid candidate profile schema")
    seen = set()
    for profile in profiles:
        if not isinstance(profile, dict):
            raise ProfileError("Invalid candidate profile schema")
        profile_id = profile.get("id")
        if not isinstance(profile_id, str) or not ID_PATTERN.fullmatch(profile_id) or profile_id in seen:
            raise ProfileError("Invalid candidate profile ID")
        seen.add(profile_id)
        education = profile.get("education")
        if not isinstance(education, dict) or type(education.get("year")) is not int or not 1 <= education["year"] <= 8 or not isinstance(education.get("degree"), str) or not education["degree"].strip():
            raise ProfileError("Invalid candidate education")
        if not _string_list(profile.get("role_families"), ROLE_FAMILIES) or not profile["role_families"]:
            raise ProfileError("Invalid candidate role families")
        for field, allowed in (("preferred_locations", LOCATIONS), ("target_seasons", None), ("skills", None), ("excluded_role_families", ROLE_FAMILIES)):
            if field in profile and not _string_list(profile[field], allowed):
                raise ProfileError("Invalid candidate profile field")
        if "javascript_preference" in profile and profile["javascript_preference"] not in ("avoid_high", "any"):
            raise ProfileError("Invalid candidate profile field")
        for field in ("display_name", "discord_user_id"):
            if field in profile and (not isinstance(profile[field], str) or not profile[field].strip()):
                raise ProfileError("Invalid candidate profile field")
    return {profile["id"]: profile for profile in profiles}


def load_profiles(environ=None, default_file=DEFAULT_PRIVATE_FILE):
    """Load env JSON, explicit file, or ignored local file, in that order."""
    env = os.environ if environ is None else environ
    raw = env.get("CANDIDATE_PROFILES_JSON")
    if raw is None:
        path = env.get("PRIVATE_PROFILES_FILE") or default_file
        try:
            raw = Path(path).read_text(encoding="utf-8")
        except OSError:
            raise ProfileError("Private candidate profiles are unavailable") from None
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        raise ProfileError("Invalid candidate profile JSON") from None
    return validate_profiles(data)
