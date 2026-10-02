"""Explainable, privacy-preserving matching for normalized ATS jobs."""

import html
import re


FAMILY_PATTERNS = {
    "ml_infra": r"\b(?:ml|machine learning|ai) infrastructure\b|\bml platform\b",
    "ml_systems": r"\b(?:ml|machine learning) systems?\b|\bmodel serving\b",
    "ai_software": r"\b(?:ai|artificial intelligence) software\b|\bai engineer(?:ing)?\b",
    "distributed_systems": r"\bdistributed systems?\b",
    "frontend": r"\bfront[ -]?end\b|\bui engineer(?:ing)?\b|\breact\b",
    "backend": r"\bback[ -]?end\b|\bserver[ -]?side\b|\bapi development\b",
    "full_stack": r"\bfull[ -]?stack\b",
    "systems": r"\bsystems? software\b|\bsystems? engineer(?:ing)?\b|\boperating systems?\b",
    "data": r"\bdata engineer(?:ing)?\b|\bdata platform\b|\bdata pipelines?\b",
    "embedded": r"\bembedded\b|\bfirmware\b",
    "networking": r"\bnetwork(?:ing)? engineer(?:ing)?\b|\bnetwork protocols?\b",
    "telecom": r"\btelecom(?:munications?)?\b|\b5g\b|\bradio access network\b",
    "hardware_design": r"\bhardware (?:design|engineer(?:ing)?)\b",
    "design_verification": r"\bdesign verification\b|\b(?:dv|uvm) engineer(?:ing)?\b|\buvm\b",
    "rtl": r"\brtl\b|\bverilog\b|\bsystemverilog\b",
    "asic": r"\b(?:asic|vlsi)\b",
    "fpga": r"\bfpga\b",
    "pcb": r"\bpcb\b|\bprinted circuit board\b",
    "circuits": r"\bcircuit(?:s|ry)?\b|\banalog design\b",
    "robotics_hardware": r"\brobotics? hardware\b|\brobotics? electronics\b",
    "semiconductor": r"\bsemiconductor\b|\bchip design\b",
    "validation": r"\bhardware validation\b|\bpost[ -]?silicon validation\b|\bsilicon validation\b",
    "general_swe": r"\bsoftware (?:engineer(?:ing)?|developer|development)\b|\bswe\b",
}
HARDWARE_FAMILIES = {
    "hardware_design", "design_verification", "rtl", "asic", "fpga", "pcb",
    "circuits", "robotics_hardware", "semiconductor", "validation", "embedded",
}
STUDENT_TERMS = re.compile(r"\b(?:intern(?:ship)?|co[ -]?op|student|pey)\b", re.I)
FULL_TIME_TERMS = re.compile(r"\b(?:full[ -]?time|permanent|new grad(?:uate)?|experienced hire)\b", re.I)
SENIOR_TERMS = re.compile(r"\b(?:senior|staff|principal|director|manager)\b", re.I)
EXPERIENCE_TERMS = re.compile(r"\b(?:[5-9]|1[0-9])\+? years? (?:of )?(?:professional|industry|work) experience\b", re.I)
GRADUATE_ONLY = re.compile(r"\b(?:graduate degree required|master'?s degree required|ph\.?d\.? required)\b", re.I)
JS_TERMS = re.compile(r"\b(?:javascript|typescript|react|vue|angular|node\.?js|next\.?js|js|ts)\b", re.I)
FRONTEND_TERMS = re.compile(r"\b(?:front[ -]?end|ui|web application|react|vue|angular|html|css)\b", re.I)


def _text(value):
    return value if isinstance(value, str) else ""


def normalize_location(location):
    text = _text(location).lower().strip()
    if not text:
        return "unknown"
    remote = bool(re.search(r"\bremote\b", text))
    canada = bool(re.search(r"\bcanada\b|\bcanadian\b", text))
    us = bool(re.search(r"\b(?:united states|u\.?s\.?a?\.?|usa)\b", text))
    if remote:
        return "us" if us else "remote_canada" if canada else "unknown"
    if re.search(r"\b(?:toronto|mississauga|brampton|markham|vaughan|richmond hill|scarborough|north york|etobicoke|oakville|burlington|gta|greater toronto)\b", text):
        return "toronto_gta"
    if re.search(r"\b(?:ottawa|kanata|nepean)\b", text):
        return "ottawa"
    if us:
        return "us"
    if re.search(r"\b(?:san jose|san francisco|new york|seattle|boston|austin|palo alto|menlo park|mountain view|santa clara|sunnyvale|los angeles|chicago|atlanta|bellevue|redmond|denver|portland|washington,? d\.?c\.?)\b", text) or re.search(r",\s*(?:ca|ny|wa|ma|tx|il|ga|co|or|nj|va|nc|fl|az)\b", text):
        return "us"
    if re.search(r"\b(?:singapore|india|united kingdom|london|germany|france|australia|israel|taiwan|japan|poland|ireland|netherlands)\b", text):
        return "international"
    if canada or re.search(r"\b(?:vancouver|montreal|montréal|calgary|edmonton|waterloo|kitchener|hamilton|halifax|winnipeg|victoria|quebec|québec|on|bc|ab|qc|ns|mb)\b", text):
        return "canada_unspecified" if text in ("canada", "canadian") else "other_canadian_city"
    return "unknown"


def classify_role_families(job):
    title = _text(job.get("title"))
    description = html.unescape(re.sub(r"<[^>]+>", " ", _text(job.get("description") or job.get("description_html"))))
    title_hits = {family for family, pattern in FAMILY_PATTERNS.items() if re.search(pattern, title, re.I)}
    description_hits = {family for family, pattern in FAMILY_PATTERNS.items() if re.search(pattern, description, re.I)}
    technical_title = re.search(r"\b(?:software|engineer(?:ing)?|developer|programmer|data|machine learning|ai|hardware|firmware|embedded|systems?|network(?:ing)?|telecom|asic|fpga|rtl|verification|robotics?|silicon|semiconductor|chip|circuit|electrical|electronics|analog|digital|ate|dft|emulation)\b", title, re.I)
    if not technical_title:
        return title_hits
    hardware_title = re.search(r"\b(?:hardware|asic|fpga|rtl|verification|silicon|semiconductor|chip|circuit|electrical|electronics|analog|digital|ate|dft|emulation)\b|\bphysical design\b|\bmixed.signal\b", title, re.I)
    if hardware_title and not re.search(r"\b(?:software|firmware|embedded)\b", title, re.I):
        return title_hits | (description_hits & (HARDWARE_FAMILIES - {'embedded'}))
    # A title's specific discipline outranks incidental mentions in a description.
    specific_title = title_hits - {"general_swe"}
    if specific_title:
        return title_hits | (description_hits & HARDWARE_FAMILIES if title_hits & HARDWARE_FAMILIES else set())
    if 'general_swe' in title_hits:
        description_hits -= HARDWARE_FAMILIES
    return title_hits | description_hits


def javascript_intensity(job, families=None):
    families = classify_role_families(job) if families is None else families
    title = _text(job.get("title"))
    description = html.unescape(re.sub(r"<[^>]+>", " ", _text(job.get("description") or job.get("description_html"))))
    mentions = len(JS_TERMS.findall(title + " " + description))
    frontend = bool(FRONTEND_TERMS.search(title)) or "frontend" in families
    if frontend and mentions >= 2 or mentions >= 4:
        return "HIGH"
    if mentions and ("full_stack" in families or mentions >= 2):
        return "MEDIUM"
    return "LOW"


def internship_eligibility(job):
    title = _text(job.get("title"))
    employment = _text(job.get("employment_type"))
    description = html.unescape(re.sub(r"<[^>]+>", " ", _text(job.get("description") or job.get("description_html"))))
    if SENIOR_TERMS.search(title) or EXPERIENCE_TERMS.search(description) or GRADUATE_ONLY.search(description):
        return False
    if FULL_TIME_TERMS.search(title) and not STUDENT_TERMS.search(title):
        return False
    if FULL_TIME_TERMS.search(employment) and not STUDENT_TERMS.search(employment):
        return False
    if STUDENT_TERMS.search(title) or STUDENT_TERMS.search(employment):
        return True
    if FULL_TIME_TERMS.search(description):
        return False
    # A dedicated student hiring statement can confirm a title without intern.
    if re.search(r"\b(?:internship|co[ -]?op|summer student|pey) (?:position|program|opportunity|role)\b", description, re.I):
        return True
    return None


def match_job(job, profile, require_internship=True):
    """Return a decision with discrete evidence and anonymous profile ID."""
    families = classify_role_families(job)
    location = job.get('location_normalized') or normalize_location(job.get("location"))
    eligibility = internship_eligibility(job)
    overlap = sorted(families & set(profile["role_families"]))
    excluded = sorted(families & set(profile.get("excluded_role_families", [])))
    # Hardware evidence may override a general software label on embedded roles.
    if excluded and families & HARDWARE_FAMILIES:
        excluded = []
    wanted = set(profile.get("preferred_locations", []))
    location_fit = "unrestricted" if not wanted else ("preferred" if location in wanted else "unknown" if location == "unknown" else "outside_preference")
    js = javascript_intensity(job, families)
    reasons = []
    warnings = []
    if overlap:
        reasons.append("Role family: " + ", ".join(overlap))
    if eligibility is True:
        reasons.append("Student opportunity indicated")
    elif eligibility is None:
        warnings.append("Internship eligibility is unconfirmed")
    else:
        warnings.append("Appears to be a full-time or permanent role")
    if location_fit == "preferred":
        reasons.append("Preferred location: " + location)
    elif location_fit == "unknown":
        warnings.append("Location or country eligibility is unknown")
    elif location_fit == "outside_preference":
        warnings.append("Outside preferred locations")
    if excluded:
        warnings.append("Excluded role family: " + ", ".join(excluded))
    if profile.get("javascript_preference") == "avoid_high" and js == "HIGH":
        warnings.append("High JavaScript intensity")
    description = _text(job.get("description") or job.get("description_html"))
    skill_hits = sorted(skill for skill in profile.get("skills", []) if re.search(r"(?<!\w)" + re.escape(skill) + r"(?!\w)", description, re.I))
    if skill_hits:
        reasons.append("Skill overlap: " + ", ".join(skill_hits))
    season = re.search(r"\b(summer|fall|autumn|winter|spring)\s+(20\d{2})\b", _text(job.get("title")) + " " + description, re.I)
    season_fit = "unknown"
    if season:
        season_key = season.group(1).lower().replace("autumn", "fall") + "_" + season.group(2)
        season_fit = "target" if season_key in profile.get("target_seasons", []) else "other"
        if season_fit == "other":
            warnings.append("Outside target season")
    else:
        warnings.append("Term is unconfirmed")
    year = profile["education"]["year"]
    years = [int(n) for n in re.findall(r"\b([1-4])(?:st|nd|rd|th)[ -]?year\b", _text(job.get("title")) + " " + description, re.I)]
    year_fit = "compatible" if not years or year in years else "incompatible"
    if year_fit == "incompatible":
        warnings.append("Student year requirement may not fit")
    matched = bool(overlap) and not excluded and (eligibility is True or not require_internship) and season_fit != "other" and year_fit != "incompatible" and location not in {"us", "international"} and location_fit != "outside_preference" and not (profile.get("javascript_preference") == "avoid_high" and js == "HIGH" and families <= {"frontend", "general_swe"})
    return {
        "profile_id": profile["id"],
        "matched": matched,
        "score_components": {
            "internship_eligibility": eligibility,
            "role_families": overlap,
            "location": location,
            "location_fit": location_fit,
            "skill_overlap": skill_hits,
            "year_fit": year_fit,
            "season_fit": season_fit,
            "javascript_intensity": js,
            "excluded_families": excluded,
        },
        "reasons": reasons,
        "warnings": warnings,
    }


def match_profiles(job, profiles, require_internship=True):
    """Accept either a profile-ID mapping or an iterable of profile objects."""
    values = profiles.values() if isinstance(profiles, dict) else profiles
    return {profile["id"]: match_job(job, profile, require_internship) for profile in values}
