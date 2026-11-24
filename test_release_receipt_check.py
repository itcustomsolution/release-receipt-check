import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "examples"


class ReceiptCLI(unittest.TestCase):
    def run_case(self, status, reviewed=None, observed=None, raw_reviewed=None, raw_observed=None):
        with tempfile.TemporaryDirectory() as folder:
            paths = [Path(folder) / name for name in ("reviewed.json", "observed.json")]
            for path, raw, obj, fixture in zip(paths, (raw_reviewed, raw_observed),
                                               (reviewed, observed), ("reviewed", "observed-pass")):
                if raw is not None:
                    path.write_text(raw)
                elif obj is not None:
                    path.write_text(json.dumps(obj))
                else:
                    path.write_text((FIXTURES / f"{fixture}.json").read_text())
            result = subprocess.run([sys.executable, str(HERE / "release_receipt_check.py"),
                                     *map(str, paths)], capture_output=True, text=True)
            self.assertEqual(result.stderr, "")
            data = json.loads(result.stdout)
            self.assertEqual((data["status"], result.returncode),
                             (status, {"PASS": 0, "FAIL": 1, "UNKNOWN": 2}[status]))
            return data

    def fixture(self, name):
        return json.loads((FIXTURES / f"{name}.json").read_text())

    def test_pass(self):
        self.assertEqual(self.run_case("PASS")["failures"], [])

    def test_stale_revision(self):
        self.assertIn("revision: mismatch", self.run_case("FAIL", observed=self.fixture("observed-fail"))["failures"])

    def test_required_check_missing(self):
        self.assertIn("check 'smoke': missing", self.run_case("UNKNOWN", observed=self.fixture("observed-unknown"))["unknown"])

    def test_wrapper_success_does_not_override_failed_test(self):
        test = self.fixture("observed-pass")
        test["checks"]["unit"].update(test_exit_code=1, wrapper_exit_code=0)
        self.assertIn("check 'unit': outcome contradicts test exit", self.run_case("FAIL", observed=test)["failures"])

    def test_subject_binding_and_missing_evidence_interact(self):
        test = self.fixture("observed-unknown")
        test["checks"]["unit"]["artifact_sha256"] = "c" * 64
        data = self.run_case("FAIL", observed=test)
        self.assertIn("check 'unit'.artifact_sha256: mismatch", data["failures"])
        self.assertIn("check 'smoke': missing", data["unknown"])

    def test_duplicate_keys(self):
        self.assertIn("observed: invalid JSON", self.run_case("FAIL", raw_observed='{"checks": {}, "checks": {}}')["failures"])

    def test_same_reviewer(self):
        review = self.fixture("reviewed")
        review["review"]["reviewer"] = review["author"].upper()
        self.run_case("FAIL", reviewed=review)

    def test_wrong_review_target(self):
        review = self.fixture("reviewed")
        review["review"]["environment"] = "production"
        self.run_case("FAIL", reviewed=review)

    def test_unpinned_rollback_and_skip(self):
        review = self.fixture("reviewed")
        del review["rollback"]["artifact_sha256"]
        test = self.fixture("observed-pass")
        test["checks"]["smoke"]["outcome"] = "SKIPPED"
        data = self.run_case("UNKNOWN", reviewed=review, observed=test)
        self.assertEqual(len(data["unknown"]), 2)

    def test_replayed_observation_fails_even_when_bytes_match(self):
        obs = self.fixture("observed-pass")
        obs["deployment_id"] = "fixture-stage-old-deployment"
        self.assertIn("deployment_id: mismatch", self.run_case("FAIL", observed=obs)["failures"])

    def test_replayed_check_wrong_deployment_and_environment(self):
        obs = self.fixture("observed-pass")
        obs["checks"]["smoke"].update(deployment_id="fixture-old", environment="preview")
        data = self.run_case("FAIL", observed=obs)
        self.assertIn("check 'smoke'.deployment_id: mismatch", data["failures"])
        self.assertIn("check 'smoke'.environment: mismatch", data["failures"])

    def test_check_revision_binding(self):
        obs = self.fixture("observed-pass")
        obs["checks"]["smoke"]["revision"] = "fixture-other"
        self.assertIn("check 'smoke'.revision: mismatch", self.run_case("FAIL", observed=obs)["failures"])

    def test_reviewer_bound_to_deployment_attempt(self):
        reviewed = self.fixture("reviewed")
        reviewed["review"]["deployment_id"] = "previous-attempt"
        self.assertIn("reviewed.review.deployment_id: mismatch", self.run_case("FAIL", reviewed=reviewed)["failures"])

    def test_capture_cannot_precede_check_or_review(self):
        obs = self.fixture("observed-pass")
        obs["observed_at"] = "2026-01-02T16:01:59+01:00"
        data = self.run_case("FAIL", observed=obs)
        self.assertIn("reviewed.review.reviewed_at: later than observation", data["failures"])
        self.assertIn("check 'smoke'.completed_at: later than observation", data["failures"])

    def test_naive_invalid_and_unknown_timezone_rejected(self):
        for bad in ("2026-01-02T15:05:00", "2026-01-02T15:05:00+07:99",
                    "2026-01-02T15:05:00-00:00", "2026-02-30T15:05:00Z"):
            with self.subTest(bad=bad):
                obs = self.fixture("observed-pass")
                obs["observed_at"] = bad
                self.assertIn("observed.observed_at: invalid", self.run_case("FAIL", observed=obs)["failures"])

    def test_equal_after_normalizing_offset_is_accepted(self):
        obs = self.fixture("observed-pass")
        obs["observed_at"] = "2026-01-02T15:04:00Z"
        self.run_case("PASS", observed=obs)

    def test_fractional_seconds(self):
        obs = self.fixture("observed-pass")
        obs["observed_at"] = "2026-01-02T15:04:00.1Z"
        obs["checks"]["smoke"]["completed_at"] = "2026-01-02T10:04:00.100000-05:00"
        self.run_case("PASS", observed=obs)

    def test_v1_schema_rejected(self):
        obs = self.fixture("observed-pass")
        obs["schema_version"] = 1
        self.assertIn("observed.schema_version: invalid", self.run_case("FAIL", observed=obs)["failures"])

    def test_missing_subject_and_time_are_unknown(self):
        obs = self.fixture("observed-pass")
        del obs["observed_at"], obs["checks"]["smoke"]["environment"]
        data = self.run_case("UNKNOWN", observed=obs)
        self.assertIn("observed.observed_at: missing", data["unknown"])
        self.assertIn("check 'smoke'.environment: missing", data["unknown"])

    def test_boolean_exit_code_not_accepted(self):
        test = self.fixture("observed-pass")
        test["checks"]["smoke"]["test_exit_code"] = False
        self.run_case("FAIL", observed=test)


if __name__ == "__main__":
    unittest.main()
