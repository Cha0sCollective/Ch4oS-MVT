"""Unit tests for the thin agent-facing MVT wrapper; no Minecraft is launched."""
import argparse
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import mvt


class AgentInterface(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.output = str(Path(self.tmp.name) / "results")

    def args(self, **overrides):
        values = dict(
            pack_sha="a" * 40,
            accept_eula=True,
            output=self.output,
            image="itzg/minecraft-server:java21",
            heap="4G",
            container_memory="6g",
            boot_timeout=900,
            format="json",
        )
        values.update(overrides)
        return argparse.Namespace(**values)

    def write_report(self, report):
        path = Path(self.output)
        path.mkdir(parents=True)
        (path / "report.json").write_text(json.dumps(report), encoding="utf-8")

    def test_build_command_delegates_to_smoke(self):
        command = mvt.build_smoke_command(self.args())
        self.assertEqual(command[0], mvt.sys.executable)
        self.assertEqual(Path(command[1]), mvt.SMOKE)
        self.assertIn("--accept-eula", command)
        self.assertIn("--pack-sha", command)
        self.assertIn("a" * 40, command)

    def test_pass_summary_is_compact(self):
        self.write_report({
            "status": "lifecycle_passed_logs_unreviewed",
            "pack_sha": "a" * 40,
            "versions": {"minecraft": "1.21.1", "neoforge": "21.1.248"},
            "tests": [{"name": "prepare", "status": "passed"}, {"name": "boot_fresh", "status": "passed"}],
        })
        result = SimpleNamespace(returncode=0, stdout="", stderr="")
        summary = mvt.summarize(mvt.read_report(self.output), result, self.output)
        self.assertEqual(summary["status"], "pass")
        self.assertEqual(summary["checks_passed"], 2)
        self.assertEqual(summary["checks_total"], 2)
        self.assertNotIn("limits", summary)

    def test_failure_summary_points_to_failed_step(self):
        self.write_report({
            "status": "failed",
            "pack_sha": "a" * 40,
            "versions": {"minecraft": "1.21.1", "neoforge": "21.1.248"},
            "tests": [
                {"name": "prepare", "status": "passed"},
                {"name": "boot_fresh", "status": "failed", "detail": "server exited"},
                {"name": "tick_fresh", "status": "skipped"},
            ],
        })
        result = SimpleNamespace(returncode=1, stdout="", stderr="")
        summary = mvt.summarize(mvt.read_report(self.output), result, self.output)
        self.assertEqual(summary["status"], "fail")
        self.assertEqual(summary["failed_step"], "boot_fresh")
        self.assertEqual(summary["detail"], "server exited")

    def test_cleanup_error_is_reported_directly(self):
        self.write_report({
            "status": "error",
            "pack_sha": "a" * 40,
            "versions": {"minecraft": "1.21.1", "neoforge": "21.1.248"},
            "tests": [{"name": "prepare", "status": "passed"}],
            "cleanup_errors": ["Docker rm exited 1"],
        })
        result = SimpleNamespace(returncode=2, stdout="", stderr="")
        summary = mvt.summarize(mvt.read_report(self.output), result, self.output)
        self.assertEqual(summary["status"], "error")
        self.assertEqual(summary["failed_step"], "cleanup")
        self.assertEqual(summary["detail"], "Docker rm exited 1")

    @patch("mvt.subprocess.run")
    def test_check_preserves_smoke_exit_code_and_emits_json(self, mock_run):
        self.write_report({
            "status": "lifecycle_passed_logs_unreviewed",
            "pack_sha": "a" * 40,
            "versions": {"minecraft": "1.21.1", "neoforge": "21.1.248"},
            "tests": [{"name": "prepare", "status": "passed"}],
        })
        mock_run.return_value = SimpleNamespace(returncode=0, stdout="", stderr="")
        stream = StringIO()
        with redirect_stdout(stream):
            code = mvt.run_check(self.args())
        self.assertEqual(code, 0)
        payload = json.loads(stream.getvalue())
        self.assertEqual(payload["status"], "pass")
        self.assertEqual(payload["suite"], "lifecycle")

    def test_missing_report_reports_smoke_error(self):
        result = SimpleNamespace(returncode=2, stdout="", stderr="usage: smoke.py\nsmoke.py: error: bad input\n")
        summary = mvt.summarize(None, result, self.output)
        self.assertEqual(summary["status"], "error")
        self.assertEqual(summary["detail"], "smoke.py: error: bad input")


if __name__ == "__main__":
    unittest.main()
