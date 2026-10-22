"""Checks for the calibration machinery; these are not live calibration evidence."""
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import mvt.__main__ as entry
from calibration.probes import Probe
from calibration.reference import build_inputs, local_pair
from calibration.run import cases, check_case, identity
from calibration.verify import successful_report


class CalibrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def report(self, suite="client-smoke"):
        names = ["input", "inventory", "prepare", *entry.SCENARIOS[suite], "adapter_coverage"]
        value = {"schema": 2, "suite": suite, "status": "suite_passed_logs_unreviewed",
                 "tests": [{"name": name, "status": "passed"} for name in names],
                 "authentication": "NOT_TESTED", "release_gate_approved": False,
                 "pack": {"versions": {"minecraft": "1.21.1", "neoforge": "21.1.233"}},
                 "client_runtime": {"mode": "development"}}
        state = {"Running": False, "OOMKilled": False, "ExitCode": 0}
        for i in range(2):
            (self.root/f"mvt-client-{i}-state.json").write_text(json.dumps(state))
        if suite == "client-server-smoke":
            value["server_online_mode"] = False
            value["server_runtime"] = {"mode": "development", "development_boots": {
                phase: {"fml_production": False, "gametest_enabled": False, "neoforge": "21.1.233"}
                for phase in ("fresh", "saved")}}
            (self.root/"server").mkdir(exist_ok=True)
            for phase in ("fresh", "saved"):
                (self.root/"server"/f"{phase}-state.json").write_text(json.dumps(state))
        return value

    def audit(self, value):
        path = self.root/"report.json"
        path.write_text(json.dumps(value))
        return successful_report(path, value["suite"], "21.1.233")

    def test_matrix_is_complete_and_fault_oracles_are_explicit(self):
        matrix = {phase: cases(phase) for phase in ("reference", "faults", "inputs", "auxiliary")}
        self.assertEqual({p: len(v) for p, v in matrix.items()},
                         {"reference": 6, "faults": 24, "inputs": 9, "auxiliary": 4})
        ids = [c["id"] for values in matrix.values() for c in values]
        self.assertEqual(len(ids), len(set(ids)))
        for case in matrix["faults"]:
            if case["fault"] in ("placement", "transfer", "inventory", "nonce", "saved-region", "crash"):
                self.assertEqual(case["expected_code"], 1)
                self.assertIsNotNone(case["expected_step"])
            elif case["fault"] == "redaction":
                self.assertEqual(case["expected_code"], 0)
            else:
                self.assertEqual(case["expected_code"], 2)
        self.assertEqual(len(identity()), 64)

    def test_reference_archive_is_reproducible_and_explicitly_paired(self):
        first = build_inputs(self.root/"first")
        second = build_inputs(self.root/"second")
        self.assertEqual(first["no-create"]["sha256"], second["no-create"]["sha256"])
        work = self.root/"work"
        work.mkdir()
        options = SimpleNamespace(prism_zip=Path(first["no-create"]["path"]),
                                  cache=self.root/"cache", suite="client-server-smoke")
        pack, sides = local_pair(options, work)
        self.assertEqual(sides, ("client", "server"))
        self.assertEqual(pack.hashes["server"], {})
        self.assertEqual(pack.source["explicit_server_source"]["definition_sha256"], first["definition_sha256"])
        self.assertIn("onboardAccessibility:false", (pack.roots["client"]/"options.txt").read_text())
        self.assertEqual((pack.roots["client"]/"config/fml.toml").read_text(), "earlyWindowControl = false\n")

    def test_auditor_accepts_completed_clean_suite(self):
        for suite in ("client-smoke", "client-server-smoke"):
            self.assertEqual(self.audit(self.report(suite))["suite"], suite)

    def test_auditor_rejects_zero_missing_duplicate_skipped_checks(self):
        value = self.report()
        variants = [[], value["tests"][:-1], value["tests"]+[value["tests"][0]]]
        for tests in variants:
            with self.subTest(tests=len(tests)), self.assertRaises(ValueError):
                self.audit({**value, "tests": tests})
        value["tests"][3]["status"] = "skipped"
        with self.assertRaises(ValueError):
            self.audit(value)

    def test_auditor_rejects_wrong_runtime_and_approval_claims(self):
        baseline = self.report("client-server-smoke")
        for field, replacement in (("authentication", "PASSED_ONLINE_MODE_JOIN"),
                                   ("release_gate_approved", True), ("server_online_mode", True)):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.audit({**baseline, field: replacement})
        for change in ("production", "gametest", "loader"):
            value = copy.deepcopy(baseline)
            boot = value["server_runtime"]["development_boots"]["saved"]
            boot[{"production": "fml_production", "gametest": "gametest_enabled", "loader": "neoforge"}[change]] = True
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.audit(value)

    def test_auditor_rejects_missing_or_oom_exit_evidence(self):
        value = self.report()
        path = self.root/"mvt-client-1-state.json"
        path.write_text(json.dumps({"Running": False, "OOMKilled": True, "ExitCode": 137}))
        with self.assertRaises(ValueError):
            self.audit(value)
        path.unlink()
        with self.assertRaises(ValueError):
            self.audit(value)

    @patch("calibration.run.docker", return_value="")
    def test_failure_requires_actual_fault_and_expected_classification(self, docker):
        case = next(c for c in cases("faults") if c["id"] == "client-smoke-placement")
        probe = Probe("placement", self.root)
        value = {"status": "failed", "authentication": "NOT_TESTED", "release_gate_approved": False,
                 "tests": [{"name": "client_interactions", "status": "failed"}]}
        self.assertTrue(check_case(case, 1, value, probe, self.root))
        probe.fired = probe.verified = True
        self.assertEqual(check_case(case, 1, value, probe, self.root), [])
        self.assertTrue(check_case(case, 2, {**value, "status": "incomplete"}, probe, self.root))


if __name__ == "__main__":
    unittest.main()
