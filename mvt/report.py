from __future__ import annotations

import json
from pathlib import Path
import re
import time
import xml.etree.ElementTree as ET

from .errors import AuthRequired, CheckFailed, InfrastructureError


def sanitize(text: str) -> str:
    text = re.sub(r'(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/-]+=*', '<REDACTED_AUTH>', text)
    text = re.sub(r'(?i)(access[_-]?token|refresh[_-]?token|authorization|password)(["\s:=]+)([^\s",;]+)', r'\1\2<REDACTED>', text)
    return re.sub(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b", "<TOKEN>", text)


class Report:
    def __init__(self, out: Path, suite: str, steps: list[str]):
        self.out = out.resolve()
        self.out.mkdir(parents=True, exist_ok=False)
        self.data = {"schema": 2, "suite": suite, "status": "running", "release_gate_approved": False,
                     "log_comparison": "NOT_IMPLEMENTED", "calibration": "NOT_PERFORMED",
                     "tests": [{"name": n, "status": "skipped"} for n in steps]}
        self.code = 0
        self.write()

    def step(self, name: str, action):
        result = next(t for t in self.data["tests"] if t["name"] == name)
        result["status"] = "running"
        self.write()
        started = time.monotonic()
        try:
            value = action()
            result["status"] = "passed"
            return value
        except CheckFailed as exc:
            result.update(status="failed", detail=sanitize(str(exc)))
            self.code = 1
            raise
        except Exception as exc:
            result.update(status="error", detail=sanitize(str(exc)), kind="AUTH_REQUIRED" if isinstance(exc, AuthRequired) else "INFRA_ERROR")
            self.code = self.code or 2
            raise
        finally:
            result["seconds"] = round(time.monotonic() - started, 3)
            self.write()

    def write(self):
        (self.out / "report.json").write_text(json.dumps(self.data, indent=2) + "\n", encoding="utf-8")
        root = ET.Element("testsuite", name=self.data["suite"])
        for t in self.data["tests"]:
            case = ET.SubElement(root, "testcase", name=t["name"], time=str(t.get("seconds", 0)))
            if t["status"] in ("running", "skipped"):
                ET.SubElement(case, "skipped", message="Not completed")
            elif t["status"] in ("failed", "error"):
                ET.SubElement(case, "failure" if t["status"] == "failed" else "error").text = t.get("detail", "")
        for err in self.data.get("cleanup_errors", []):
            ET.SubElement(ET.SubElement(root, "testcase", name="cleanup"), "error").text = err
        root.set("tests", str(len(root)))
        for key, element in (("failures", "failure"), ("errors", "error"), ("skipped", "skipped")):
            root.set(key, str(len(root.findall(f"testcase/{element}"))))
        ET.ElementTree(root).write(self.out / "junit.xml", encoding="utf-8", xml_declaration=True)

    def finish(self, cleanup_errors=()):
        if cleanup_errors:
            self.data["cleanup_errors"] = [sanitize(e) for e in cleanup_errors]
            self.code = self.code or 2
        if not self.data["tests"] or not all(t["status"] == "passed" for t in self.data["tests"]):
            self.code = self.code or 2
        self.data["status"] = "suite_passed_logs_unreviewed" if self.code == 0 else "failed" if self.code == 1 else "incomplete"
        self.write()
        return self.code
