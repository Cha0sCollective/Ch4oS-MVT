"""Audit the complete development matrix without rewriting individual reports."""
import argparse
import hashlib
import json
from pathlib import Path

import mvt.__main__ as entry
from .run import cases, identity
from .reference import PACK_SHA


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def successful_report(path, suite, neo):
    value = json.loads(path.read_text())
    names = ["input", "inventory", "prepare", *entry.SCENARIOS[suite], "adapter_coverage"]
    require(value.get("schema") == 2 and value.get("suite") == suite, "Wrong suite/report schema")
    require(value.get("status") == "suite_passed_logs_unreviewed", "Suite did not pass")
    require([t["name"] for t in value.get("tests", [])] == names, "Missing, duplicate, or unexpected checks")
    require(all(t["status"] == "passed" for t in value["tests"]), "Skipped or failed check")
    require(not value.get("cleanup_errors"), "Cleanup did not complete")
    require(value.get("authentication") == "NOT_TESTED" and value.get("release_gate_approved") is False, "Invalid development approval claim")
    require(value.get("pack", {}).get("versions") == {"minecraft": "1.21.1", "neoforge": neo}, "Wrong reference runtime")
    require(value.get("client_runtime", {}).get("mode") == "development", "Wrong client mode")
    for pattern, count in (("mvt-client-*-state.json", 2),):
        states = [json.loads(p.read_text()) for p in path.parent.glob(pattern)]
        require(len(states) == count and all(s.get("ExitCode") == 0 and s.get("OOMKilled") is False and s.get("Running") is False for s in states), "Missing clean client exits")
    if suite == "client-server-smoke":
        runtime = value.get("server_runtime", {})
        require(value.get("server_online_mode") is False and runtime.get("mode") == "development", "Wrong server lane")
        boots = runtime.get("development_boots", {})
        require(set(boots) == {"fresh", "saved"} and all(b.get("fml_production") is False and b.get("gametest_enabled") is False and b.get("neoforge") == neo for b in boots.values()), "Missing fresh development boot identities")
        states = [json.loads(p.read_text()) for p in (path.parent/"server").glob("*-state.json")]
        require(len(states) == 2 and all(s.get("ExitCode") == 0 and s.get("OOMKilled") is False and s.get("Running") is False for s in states), "Missing clean server stops")
    return value


def collect(root, phase_paths, ch4os_prefix):
    positives, probes, reports, artifacts = [], [], [], []
    common_harness, client_files = None, None
    matrix_images = {}
    def add_report(path, value):
        nonlocal common_harness
        actual = value.get("harness_sha256")
        require(isinstance(actual, str) and len(actual) == 64, "Missing harness fingerprint")
        if common_harness is None:
            common_harness = actual
        require(actual == common_harness, "Calibration reports use different MVT source snapshots")
        reports.append(path)
    for repeat in range(1, 4):
        for suite in ("client-smoke", "client-server-smoke"):
            path = root/f"{ch4os_prefix}{repeat}"/suite/"report.json"
            value = successful_report(path, suite, "21.1.248")
            require(value["pack"]["source"].get("sha") == PACK_SHA, "Wrong Ch4oS source commit")
            require(value["adapters"]["selection"] == "auto" and [a["id"] for a in value["adapters"]["selected"]] == ["create"], "Ch4oS repeat must exercise automatic Create coverage")
            if client_files is None:
                client_files = value["pack"]["files"]["client"]
            require(value["pack"]["files"]["client"] == client_files, "Ch4oS repeats contain different candidate files")
            add_report(path, value)
            positives.append({"reference": "ch4os", "suite": suite, "repeat": repeat,
                "path": path.relative_to(root).as_posix(), "sha256": digest(path), "checks": len(value["tests"])})
            images = matrix_images.setdefault("21.1.248", {})
            require(images.get("client", value["client_runtime"]["image_id"]) == value["client_runtime"]["image_id"], "Client images changed between clean repeats")
            images["client"] = value["client_runtime"]["image_id"]
            if suite == "client-server-smoke":
                require(images.get("server", value["server_runtime"]["image_id"]) == value["server_runtime"]["image_id"], "Server images changed between clean repeats")
                images["server"] = value["server_runtime"]["image_id"]
    for phase, relative in phase_paths.items():
        directory = root / relative
        summary = json.loads((directory/"phase.json").read_text())
        plan = cases(phase)
        require(summary.get("phase") == phase and summary.get("complete") is True, "Incomplete phase: "+phase)
        require(summary.get("calibrator_sha256") == identity(), "Calibration probe source changed; rerun phase "+phase)
        require(summary.get("planned_cases") == plan, "Wrong or incomplete phase matrix: "+phase)
        require([r["case"] for r in summary["results"]] == plan, "Missing or duplicate phase outcomes")
        for result in summary["results"]:
            case = result["case"]
            case_dir = directory/case["id"]
            path = case_dir/"report.json"
            value = json.loads(path.read_text())
            require(result.get("oracle_matched") is True and result.get("reasons") == [], "Calibration oracle did not match: "+case["id"])
            require(result["actual_exit"] == case["expected_code"] and result["report_sha256"] == digest(path), "Outcome/report integrity mismatch")
            require(json.loads((case_dir/"case-result.json").read_text()) == result, "Case summary differs from phase ledger")
            require(value.get("calibration_probe", {}).get("case") == case and value["calibration_probe"].get("calibrator_sha256") == identity(), "Wrong calibration source/case in raw report")
            require(result.get("no_reseeding") is True and result.get("owned_containers_removed") is True, "Missing persistence/cleanup proof")
            if case.get("fault"):
                events = [json.loads(line) for line in (case_dir/"probe-events.jsonl").read_text().splitlines()]
                require(any(e.get("event") == "fault_verified" and e.get("fault") == case["fault"] for e in events), "Fault was never verified")
            add_report(path, value)
            if case["expected_code"] == 0:
                neo = "21.1.233" if case["reference"] == "no-create" else "21.1.248"
                successful_report(path, case["suite"], neo)
                for side in ("client", "server"):
                    key = side+"_runtime"
                    if key in value:
                        images = matrix_images.setdefault(neo, {})
                        actual = value[key]["image_id"]
                        require(images.get(side, actual) == actual == summary["images"][neo][side], "Image identity changed across matrix")
                        images[side] = actual
                if phase == "reference":
                    require(value["adapters"]["selected"] == [] and not value["installed_mods"]["client"], "No-Create reference unexpectedly contains gameplay mods")
                    require(value["pack"]["source"].get("calibration_reference_sha256") == digest(Path(__file__).with_name("reference.json")), "Wrong reference definition")
                    require(value["pack"]["files"]["client"].get("config/fml.toml") == hashlib.sha256(b"earlyWindowControl = false\n").hexdigest(), "Reference early-window profile missing")
                    positives.append({"reference": "no-create", "suite": case["suite"], "repeat": case["repeat"],
                        "path": path.relative_to(root).as_posix(), "sha256": digest(path), "checks": len(value["tests"])})
                if case["reference"] == "ch4os-prism":
                    require(value["pack"]["files"]["client"] == client_files, "Prism calibration silently changed the pinned client pack")
            if phase != "reference":
                probes.append({"id": case["id"], "expected_exit": case["expected_code"], "actual_exit": result["actual_exit"],
                    "expected_failure_step": case.get("expected_step"), "oracle_matched": True,
                    "path": path.relative_to(root).as_posix(), "sha256": digest(path)})
        artifacts.append(directory/"phase.json")
    require(len(positives) == 12, "Twelve clean reference suite runs are required")
    for reference in ("ch4os", "no-create"):
        for suite in ("client-smoke", "client-server-smoke"):
            require(sorted(p["repeat"] for p in positives if p["reference"] == reference and p["suite"] == suite) == [1,2,3], "Three consecutive clean repetitions missing")
    for path in reports:
        artifacts.extend(p for p in path.parent.rglob("*") if p.is_file())
    artifact_ledger = [{"path": p.relative_to(root).as_posix(), "sha256": digest(p), "bytes": p.stat().st_size} for p in sorted(set(artifacts))]
    return {"schema": 1, "status": "CALIBRATED_DEVELOPMENT", "release_gate_approved": False,
        "authentication": "NOT_TESTED", "graphical_testing": False, "harness_sha256": common_harness,
        "calibrator_sha256": identity(), "scope": {"minecraft": "1.21.1", "runtime_images": matrix_images,
            "server_heap": "4G", "server_container": "6G", "client_container": "8G",
            "references": ["Pinned Create Ch4oS "+PACK_SHA, "Declared minimal NeoForge-only 21.1.233 fixture"],
            "reference_definition_sha256": digest(Path(__file__).with_name("reference.json")),
            "reference_early_window_control": False},
        "clean_repetitions": positives, "probe_cases": probes, "artifact_ledger": artifact_ledger,
        "limitations": ["Production/authenticated lane not calibrated", "No log-baseline release gate", "Reference matrix is not all NeoForge packs", "No graphical tests"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ch4os-prefix", default="ch4os-fixed-")
    for phase in ("reference", "faults", "inputs", "auxiliary"):
        parser.add_argument("--"+phase, required=True)
    args = parser.parse_args()
    value = collect(args.root, {p: getattr(args,p) for p in ("reference","faults","inputs","auxiliary")}, args.ch4os_prefix)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(value, indent=2)+"\n")
    print(json.dumps({"status":value["status"],"clean_repetitions":len(value["clean_repetitions"]),"probe_cases":len(value["probe_cases"])}))


if __name__ == "__main__":
    main()
