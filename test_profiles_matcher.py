import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from job_matcher import classify_role_families, graduate_requirement, javascript_intensity, match_job, normalize_location
from profiles import ProfileError, load_profiles, validate_profiles


ROOT = Path(__file__).resolve().parent
SYNTHETIC = {
    "schema_version": 1,
    "profiles": [
        {"id": "sample_ml", "education": {"year": 3, "degree": "Software Engineering"},
         "role_families": ["ml_infra", "systems", "backend"], "preferred_locations": ["toronto_gta", "ottawa", "remote_canada"],
         "skills": ["Python"], "target_seasons": ["summer_2027"]},
        {"id": "sample_ui", "education": {"year": 3, "degree": "Design Computing"},
         "role_families": ["frontend", "full_stack"], "skills": ["React"]},
        {"id": "sample_chip", "education": {"year": 3, "degree": "Electrical Engineering"},
         "role_families": ["asic", "design_verification", "rtl"],
         "excluded_role_families": ["general_swe", "frontend", "backend"], "skills": ["Verilog"]},
    ],
}


def job(title, location="Toronto, ON", description="", **extra):
    return {"title": title, "location": location, "description": description, **extra}


class LoaderTests(unittest.TestCase):
    def test_optional_graduate_level(self):
        profile = {**SYNTHETIC['profiles'][0], 'education': {'year': 1, 'degree': 'Computing', 'level': 'graduate'}}
        self.assertIn('sample_ml', validate_profiles({'schema_version': 1, 'profiles': [profile]}))
        profile['education']['level'] = 'unknown'
        with self.assertRaises(ProfileError):
            validate_profiles({'schema_version': 1, 'profiles': [profile]})

    def test_mobile_and_explicit_foreign_preference_are_valid(self):
        data = {"schema_version": 1, "profiles": [{**SYNTHETIC['profiles'][0],
                "role_families": ["mobile"], "preferred_locations": ["non_canada"]}]}
        self.assertIn('sample_ml', validate_profiles(data))

    def test_environment_precedes_file(self):
        profiles = load_profiles({"CANDIDATE_PROFILES_JSON": json.dumps(SYNTHETIC), "PRIVATE_PROFILES_FILE": "/nonexistent"})
        self.assertEqual(set(profiles), {"sample_ml", "sample_ui", "sample_chip"})

    def test_explicit_private_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.json"
            path.write_text(json.dumps(SYNTHETIC), encoding="utf-8")
            self.assertIn("sample_ml", load_profiles({"PRIVATE_PROFILES_FILE": str(path)}))

    def test_missing_and_malformed_fail_without_leaking(self):
        marker = "PRIVATE_SENTINEL_DO_NOT_PRINT"
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            with self.assertRaises(ProfileError) as missing:
                load_profiles({}, default_file=Path("/nonexistent/private-profile.json"))
            with self.assertRaises(ProfileError) as malformed:
                load_profiles({"CANDIDATE_PROFILES_JSON": "{" + marker})
            with self.assertRaises(ProfileError):
                load_profiles({"CANDIDATE_PROFILES_JSON": json.dumps({"schema_version": 1, "profiles": [{"id": "bad"}]})})
        self.assertNotIn(marker, output.getvalue() + str(missing.exception) + str(malformed.exception))


class MatcherTests(unittest.TestCase):
    def setUp(self):
        self.profiles = {p["id"]: p for p in SYNTHETIC["profiles"]}

    def test_ml_infrastructure_internship(self):
        result = match_job(job("ML Infrastructure Intern - Summer 2027", description="Python model serving"), self.profiles["sample_ml"])
        self.assertTrue(result["matched"])
        self.assertIn("ml_infra", result["score_components"]["role_families"])
        self.assertEqual(result["score_components"]["skill_overlap"], ["Python"])

    def test_frontend_react_internship(self):
        self.assertTrue(match_job(job("Frontend React Intern", description="Build UI in React"), self.profiles["sample_ui"])["matched"])

    def test_asic_dv_and_generic_swe(self):
        hardware = self.profiles["sample_chip"]
        self.assertTrue(match_job(job("ASIC Design Verification Co-op", description="RTL and Verilog"), hardware)["matched"])
        self.assertFalse(match_job(job("Software Engineering Intern", description="General web services"), hardware)["matched"])

    def test_full_time_role_rejected_despite_student_mention(self):
        result = match_job(job("Backend Software Engineer - Full Time", description="Students are welcome to apply"), self.profiles["sample_ml"])
        self.assertFalse(result["matched"])

    def test_graduate_enrollment_requirement(self):
        undergraduate = {**self.profiles['sample_ml'], 'role_families': ['general_swe']}
        graduate = {**undergraduate, 'education': {**undergraduate['education'], 'level': 'graduate'}}
        cases = [
            ('Graduate Student Intern - Software Engineering', '', 'required', False),
            ('Software Engineering Intern', "Master's or PhD student required", 'required', False),
            ('Software Engineering Intern', 'Graduate degree preferred', 'preferred', True),
            ('Software Engineering Intern', "Bachelor's degree required, graduate degree preferred", 'preferred', True),
            ('Software Engineering Intern', "Currently pursuing a Bachelor's or Master's degree", 'unknown', True),
            ('PhD Software Engineering Intern', '', 'required', False),
            ("Bachelor's student Software Engineering internship", '', 'unknown', True),
            ('Software Engineering Intern', "Applicants may have a Master's degree", 'unknown', True),
        ]
        for title, description, requirement, allowed in cases:
            with self.subTest(title=title, description=description):
                sample = job(title, description=description)
                self.assertEqual(graduate_requirement(sample), requirement)
                self.assertEqual(match_job(sample, undergraduate)['matched'], allowed)
                if requirement == 'required':
                    self.assertTrue(match_job(sample, graduate)['matched'])

    def test_location_normalization(self):
        cases = {"Mississauga, ON": "toronto_gta", "Kanata, Ontario": "ottawa",
                 "Remote - Canada": "remote_canada", "Remote": "unknown",
                 "Canada": "canada_unspecified", "Vancouver, BC": "other_canadian_city",
                 "New York, USA": "non_canada", "": "unknown"}
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(normalize_location(text), expected)

    def test_javascript_intensity(self):
        cases = [
            (job("Backend Systems Intern", description="Python data services"), "LOW"),
            (job("Full-stack Intern", description="Python APIs and TypeScript user interface"), "MEDIUM"),
            (job("Frontend React Intern", description="React, JavaScript, TypeScript and CSS"), "HIGH"),
        ]
        for sample, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(javascript_intensity(sample), expected)

    def test_unknown_remote_is_not_canadian(self):
        result = match_job(job("ML Infrastructure Intern", location="Remote"), self.profiles["sample_ml"])
        self.assertEqual(result["score_components"]["location"], "unknown")
        self.assertTrue(any("unknown" in warning.lower() for warning in result["warnings"]))

    def test_foreign_locations_and_canadian_option(self):
        foreign = ('China, Shanghai', 'SHANGHAI', 'BEIJING', 'Brazil, Belo Horizonte',
                   'BELO HORIZONTE', 'United States', 'USA', 'California', 'San Jose, CA, USA',
                   'Costa Rica, San Jose', 'Malaysia, Penang', 'Vietnam, Ho_Chi_Minh_City',
                   'Lysaker, Norway', 'Mount Laurel, New Jersey', 'Bengaluru', 'Bucharest')
        for location in foreign:
            with self.subTest(location=location):
                self.assertEqual(normalize_location(location), 'non_canada')
                self.assertFalse(match_job(job('ML Infrastructure Intern', location), self.profiles['sample_ml'])['matched'])
        self.assertEqual(normalize_location('San Jose, CA or Toronto, Ontario, Canada'), 'toronto_gta')
        self.assertTrue(match_job(job('ML Infrastructure Intern', 'San Jose, CA or Toronto, Ontario, Canada'), self.profiles['sample_ml'])['matched'])
        self.assertEqual(normalize_location('London'), 'unknown')
        allowed = {**self.profiles['sample_ml'], 'preferred_locations': ['non_canada']}
        self.assertTrue(match_job(job('ML Infrastructure Intern', 'China, Shanghai'), allowed)['matched'])

    def test_conservative_us_and_mixed_locations(self):
        cases = {
            'AUSTIN': 'non_canada', 'Austin, TX': 'non_canada',
            'Hillsboro, Oregon': 'non_canada', 'Seattle, WA': 'non_canada',
            'New York, NY': 'non_canada', 'San Jose, CA': 'non_canada',
            '5 Locations': 'unknown',
            'San Jose, CA or Toronto, Ontario, Canada': 'toronto_gta',
            'Ottawa, Ontario, Canada and Austin, TX': 'ottawa',
            'Remote Canada': 'remote_canada', 'Canada': 'canada_unspecified',
            'Shanghai, China and Austin, TX': 'non_canada',
        }
        for location, expected in cases.items():
            with self.subTest(location=location):
                self.assertEqual(normalize_location(location), expected)

    def test_clear_title_families_with_supporting_description(self):
        cases = [
            ('Machine Learning Engineering Intern', 'Build backend services and backend APIs', {'ml_systems', 'backend'}),
            ('AI/ML Software Intern', '', {'ai_software'}),
            ('Software Engineering, Optical Transport and IP Networking Intern', '', {'networking'}),
            ('Telecom Software Intern', '', {'telecom'}),
            ('Embedded Systems Intern', '', {'embedded'}),
            ('Firmware Intern', '', {'embedded'}),
            ('FPGA Intern', '', {'fpga'}),
            ('ASIC Intern', '', {'asic'}),
            ('Design Verification Intern', '', {'design_verification'}),
            ('RTL Intern', '', {'rtl'}),
            ('Frontend Intern', '', {'frontend'}),
            ('Backend Intern', '', {'backend'}),
            ('Full Stack Intern', '', {'full_stack'}),
            ('iOS Mobile Intern', '', {'mobile'}),
        ]
        for title, description, expected in cases:
            with self.subTest(title=title):
                self.assertTrue(expected <= classify_role_families(job(title, description=description)))
        self.assertNotIn('ml_systems', classify_role_families(job('Software Engineering Intern', description='Use an ML library once')))
        self.assertNotIn('mobile', classify_role_families(job('Backend Intern', description='API serves mobile clients')))

    def test_mobile_title_overrides_incidental_backend(self):
        from job_matcher import classify_role_families
        cases = [
            ('Software Developer Intern, iOS (Summer 2027)', 'Backend API team collaboration', {'mobile', 'general_swe'}, {'backend'}),
            ('Android Mobile Software Engineering Intern', 'Uses backend APIs', {'mobile', 'general_swe'}, {'backend'}),
            ('Backend Software Engineering Intern', 'Serves mobile clients', {'backend', 'general_swe'}, {'mobile'}),
            ('Full-Stack Software Engineering Intern', 'Build mobile clients and APIs', {'full_stack', 'general_swe'}, {'mobile'}),
            ('Backend API Software Engineering Intern', 'Consumed by iOS apps', {'backend', 'general_swe'}, {'mobile'}),
        ]
        for title, description, included, excluded in cases:
            with self.subTest(title=title):
                families = classify_role_families(job(title, description=description))
                self.assertTrue(included <= families)
                self.assertFalse(excluded & families)

    def test_greenhouse_shape_and_wrong_season(self):
        greenhouse_job = {"title": "ML Infrastructure Intern - Summer 2026", "location": "Toronto, ON",
                          "description_html": "<p>Python model serving</p>", "source": "greenhouse", "job_id": "fake"}
        result = match_job(greenhouse_job, self.profiles["sample_ml"])
        self.assertFalse(result["matched"])
        self.assertEqual(result["score_components"]["season_fit"], "other")


class PrivacyTests(unittest.TestCase):
    def test_ignore_rules_and_untracked_private_file(self):
        for path in ("private/profiles.local.json", ".env"):
            result = subprocess.run(["git", "check-ignore", "-q", path], cwd=ROOT)
            self.assertEqual(result.returncode, 0, path)
        self.assertNotEqual(subprocess.run(["git", "check-ignore", "-q", ".env.example"], cwd=ROOT).returncode, 0)
        tracked = subprocess.check_output(["git", "ls-files", "--", "private/profiles.local.json"], cwd=ROOT)
        self.assertEqual(tracked, b"")

    def test_public_example_is_synthetic(self):
        example = json.loads((ROOT / "profiles.example.json").read_text(encoding="utf-8"))
        ids = {profile["id"] for profile in example["profiles"]}
        self.assertEqual(ids, {"sample_software", "sample_hardware"})
        self.assertTrue(all("display_name" not in p and "discord_user_id" not in p for p in example["profiles"]))
        self.assertTrue(all(p["education"]["year"] == 3 for p in example["profiles"]))

    def test_discord_uses_anonymous_id_or_private_runtime_data(self):
        from discord_notify import send_internship_alert
        with patch.dict(os.environ, {"INTERNSHIP_DISCORD_WEBHOOK_URL": "https://example.invalid/webhook"}), patch("discord_notify._send") as send:
            send_internship_alert("Example", "Intern", "Toronto", "https://example.invalid/job", ["sample_ml"])
            self.assertEqual(send.call_args.args[1]["embeds"][0]["fields"][1]["value"], "sample_ml")
            self.assertEqual(send.call_args.args[1]["allowed_mentions"]["users"], [])


if __name__ == "__main__":
    unittest.main()
