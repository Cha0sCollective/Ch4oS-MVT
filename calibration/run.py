"""Run reproducible calibration cases against the real MVT CLI and game images."""
import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import signal
import tempfile
import time
from unittest.mock import patch

import mvt.__main__ as entry
from mvt.client import Client, docker
from mvt.inputs import Cache, packwiz
from mvt.report import Report
from mvt.server import Server
from .probes import Probe, TOKEN
from .reference import PACK_SHA, build_inputs, local_pair, descriptor, write_zip

SUITES = ("client-smoke", "client-server-smoke")
PRODUCTION_SERVER = "itzg/minecraft-server@sha256:aae986843ab9b00effb816c6404d28519671bcaa49bf234589d4a0e74c56ccb5"


def cases(phase):
    result = []
    def add(identity, suite, reference="no-create", **extra):
        result.append({"id": identity, "suite": suite, "reference": reference, "expected_code": 0, **extra})
    if phase == "reference":
        for repeat in range(1, 4):
            for suite in SUITES:
                add(f"no-create-{suite}-{repeat}", suite, repeat=repeat)
    elif phase == "faults":
        for suite in SUITES:
            for fault in ("placement", "transfer", "inventory", "nonce", "saved-region", "crash", "timeout", "stale-response", "oom", "interrupt", "cleanup", "redaction"):
                code = 2 if fault in ("timeout", "stale-response", "oom", "interrupt", "cleanup") else 0 if fault == "redaction" else 1
                stage = "client_interactions" if fault in ("placement", "transfer", "inventory") else "verify_persistence" if fault in ("nonce", "saved-region") else None
                if fault in ("crash", "timeout", "stale-response", "oom", "interrupt"):
                    stage = "create_world" if suite == "client-smoke" else "join_server"
                add(f"{suite}-{fault}", suite, fault=fault, expected_code=code, expected_step=stage)
    elif phase == "inputs":
        for name, code in (("wrong-minecraft", 2), ("wrong-loader", 2), ("traversal", 1), ("hook", 2)):
            add("input-"+name, "client-smoke", command="inspect", archive=name, local_pair=False, expected_code=code, expected_step="input")
        add("client-only-prism-needs-counterpart", "client-server-smoke", local_pair=False, expected_code=2, expected_step="input")
        add("counterpart-wrong-neoforge", "client-server-smoke", local_pair=False, counterpart=True, expected_code=2, expected_step="input")
        add("missing-authentication-profile", "client-smoke", authentication=True, expected_code=2, expected_step="prepare", expected_kind="AUTH_REQUIRED")
        add("wrong-client-image", "client-smoke", wrong_image=True, expected_code=2, expected_step="prepare")
        add("unsupported-adapter-version", "client-smoke", command="inspect", archive="unsupported-create", local_pair=False,
            expected_code=2, expected_step="adapter_coverage")
    elif phase == "auxiliary":
        add("prism-explicit-server-create", "client-server-smoke", reference="ch4os-prism", adapters="create", counterpart=True)
        add("ch4os-core-only", "client-smoke", reference="ch4os", adapters="none")
        add("ch4os-real-channel-mismatch", "client-server-smoke", reference="ch4os", production_server=True,
            expected_code=1, expected_step="join_server", expected_detail="mod-channel negotiation")
        add("explicit-create-unavailable", "client-smoke", adapters="create", expected_code=2, expected_step="adapter_coverage")
    else:
        raise ValueError("Unknown calibration phase")
    return result


def identity():
    root = Path(__file__).parent
    paths = sorted(list(root.glob("*.py"))+[root / "reference.json"])
    return hashlib.sha256(b"".join(p.name.encode()+b"\0"+p.read_bytes() for p in paths)).hexdigest()


def prepare_inputs(directory):
    values = build_inputs(directory)
    # Metadata-only negative fixture, never launched as a real mod.
    import io, zipfile
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as z:
        z.writestr("META-INF/neoforge.mods.toml", '[[mods]]\nmodId="create"\nversion="7.0.0"\n')
    path = directory / "unsupported-create.zip"
    values["unsupported-create"] = {"path": str(path), "sha256": write_zip(path, {
        "mmc-pack.json": descriptor(), "instance.cfg": "[General]\nname=metadata-only negative\n",
        ".minecraft/mods/metadata-only-create.jar": data.getvalue()})}
    return values


def make_ch4os_prism(directory, cache):
    path = directory / "ch4os.zip"
    with tempfile.TemporaryDirectory(prefix="mvt-calibration-prism-") as temp:
        pack = packwiz("Cha0sCollective/Create-Ch4os-Packwiz", PACK_SHA, "pack", Path(temp), Cache(cache), ("client",))
        entries = {"mmc-pack.json": descriptor("21.1.248"), "instance.cfg": "[General]\nname=Pinned Create Ch4oS calibration\n"}
        for name in pack.hashes["client"]:
            entries[".minecraft/"+name] = pack.roots["client"] / name
        digest = write_zip(path, entries)
    return {"path": str(path), "sha256": digest}


def check_case(case, code, report, probe, out):
    reasons = []
    expected = case["expected_code"]
    if code != expected:
        reasons.append(f"Expected exit {expected}, observed {code}")
    if expected == 0:
        required = ["input", "inventory", "prepare", *entry.SCENARIOS[case["suite"]], "adapter_coverage"]
        if [t["name"] for t in report.get("tests", [])] != required or any(t["status"] != "passed" for t in report["tests"]):
            reasons.append("Passing calibration requires every selected check to execute and pass")
        if report.get("status") != "suite_passed_logs_unreviewed":
            reasons.append("A live passing case must be a completed suite")
        neo = "21.1.233" if case["reference"] == "no-create" else "21.1.248"
        if report.get("pack", {}).get("versions") != {"minecraft": "1.21.1", "neoforge": neo}:
            reasons.append("The live reference runtime version differs from the matrix")
        if case["suite"] == "client-server-smoke":
            runtime = report.get("server_runtime", {})
            boots = runtime.get("development_boots", {})
            if runtime.get("mode") != "development" or set(boots) != {"fresh", "saved"} or any(
                b.get("fml_production") is not False or b.get("gametest_enabled") is not False for b in boots.values()
            ):
                reasons.append("Both dedicated-server development runtime identities are required")
        states = [json.loads(p.read_text()) for p in out.glob("mvt-client-*-state.json")]
        if len(states) != 2 or any(s.get("ExitCode") != 0 or s.get("OOMKilled") is not False or s.get("Running") is not False for s in states):
            reasons.append("Two clean, non-OOM client exits must be evidenced")
        if case["suite"] == "client-server-smoke":
            states = [json.loads(p.read_text()) for p in (out/"server").glob("*-state.json")]
            if len(states) != 2 or any(s.get("ExitCode") != 0 or s.get("OOMKilled") is not False or s.get("Running") is not False for s in states):
                reasons.append("Two clean, non-OOM server stops must be evidenced")
    else:
        if report.get("status") != ("failed" if expected == 1 else "incomplete"):
            reasons.append("Failure classification differs from the planned oracle")
        stage = case.get("expected_step")
        if stage:
            test = next((t for t in report["tests"] if t["name"] == stage), {})
            allowed = ("running", "error") if case.get("fault") == "interrupt" else ("failed", "error")
            if test.get("status") not in allowed:
                reasons.append("Expected failure stage was not observed: "+stage)
            if case.get("expected_kind") and test.get("kind") != case["expected_kind"]:
                reasons.append("Expected error kind was not observed")
        if case.get("expected_detail") and case["expected_detail"] not in report.get("error", ""):
            reasons.append("Expected failure reason was not observed")
    if case.get("fault") and not (probe.fired and probe.verified):
        reasons.append("The deliberate fault was not applied and independently verified")
    if report.get("cleanup_errors") and case.get("fault") != "cleanup":
        reasons.append("Unexpected cleanup errors")
    if case.get("fault") == "cleanup" and not report.get("cleanup_errors"):
        reasons.append("The cleanup failure did not prevent suite success")
    if case.get("fault") == "cleanup" and any(t["status"] != "passed" for t in report["tests"]):
        reasons.append("Cleanup-failure calibration requires all gameplay checks to have passed")
    if report.get("authentication") != "NOT_TESTED" or report.get("release_gate_approved") is not False:
        reasons.append("Development evidence must not claim authenticated or release approval")
    if not probe.no_reseeding():
        reasons.append("Persistence/reconnect evidence contains fixture reseeding or lacks its trace")
    if case.get("fault") == "redaction":
        exported = [p.read_text(errors="replace") for p in out.rglob("*") if p.is_file()]
        if any(TOKEN in data for data in exported) or not any("<REDACTED>" in data for data in exported):
            reasons.append("Synthetic token redaction was not demonstrated in exported artifacts")
    for client in probe.clients:
        names = docker("ps", "-a", "--filter", "label=ch4os.mvt.run="+client.control.run_id, "--format", "{{.Names}}")
        if names:
            reasons.append("Owned client container survived cleanup")
        if client.work.exists():
            reasons.append("Owned temporary game installation survived cleanup")
    if probe.server:
        names = docker("ps", "-a", "--filter", "name=^/"+probe.server.name+"$", "--format", "{{.Names}}")
        if names:
            reasons.append("Owned server container survived cleanup")
    owned_networks = {c.network for c in probe.clients if c.network}
    if probe.server:
        owned_networks.add(probe.server.network)
    if owned_networks.intersection(docker("network", "ls", "--format", "{{.Name}}").splitlines()):
        reasons.append("Owned scenario network survived cleanup")
    return reasons


def run_case(case, out, inputs, images, cache):
    probe = Probe(case.get("fault"), out)
    class TracedReport(Report):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.data["calibration_probe"] = {"case": case, "calibrator_sha256": identity(),
                "post_fault_state_deadline_seconds": 15, "post_pause_operation_deadline_seconds": 2}
            self.write()
        def step(self, name, action):
            def checked():
                probe.before_step(name)
                value = action()
                probe.after_step(name)
                return value
            try:
                return super().step(name, checked)
            finally:
                probe.restore_pauses()
    def client(*args, **kwargs):
        return probe.track_client(Client(*args, **kwargs))
    def server(*args, **kwargs):
        probe.server = Server(*args, **kwargs)
        return probe.server
    neo = "21.1.233" if case["reference"] == "no-create" else "21.1.248"
    argv = [case.get("command", "run"), "--suite", case["suite"], "--cache", str(cache), "--output", str(out),
            "--adapters", case.get("adapters", "auto")]
    if case["reference"] == "ch4os":
        argv += ["--pack-sha", PACK_SHA]
    else:
        key = "ch4os-prism" if case["reference"] == "ch4os-prism" else case.get("archive", "no-create")
        argv += ["--prism-zip", inputs[key]["path"]]
    if case.get("counterpart"):
        argv += ["--server-pack-sha", PACK_SHA]
    if argv[0] == "run":
        argv += ["--accept-eula", "--client-mode", "authenticated" if case.get("authentication") else "development",
                 "--client-image", images["21.1.248" if case.get("wrong_image") else neo]["client"],
                 "--heap", "4G", "--container-memory", "6G", "--client-memory", "8G"]
        if case["suite"] == "client-server-smoke":
            argv += (["--image", PRODUCTION_SERVER] if case.get("production_server") else
                     ["--server-mode", "development", "--development-server-image", images[neo]["server"]])
    with ExitStack() as stack:
        stack.enter_context(patch.object(entry, "Report", TracedReport))
        stack.enter_context(patch.object(entry, "Client", client))
        stack.enter_context(patch.object(entry, "Server", server))
        if case["reference"] == "no-create" and case.get("local_pair", True) and not case.get("archive"):
            stack.enter_context(patch.object(entry, "resolve", local_pair))
        if case.get("authentication"):
            stack.enter_context(patch.dict(os.environ, {"MVT_SHARED_VOLUME": ""}))
        started = time.monotonic()
        code = entry.main(argv)
    report = json.loads((out / "report.json").read_text())
    probe.cleanup_probe()
    reasons = check_case(case, code, report, probe, out)
    value = {"case": case, "actual_exit": code, "oracle_matched": not reasons, "reasons": reasons,
             "seconds": round(time.monotonic()-started, 3), "report_sha256": hashlib.sha256((out/"report.json").read_bytes()).hexdigest(),
             "no_reseeding": probe.no_reseeding(), "owned_containers_removed": not any("survived cleanup" in r for r in reasons)}
    (out / "case-result.json").write_text(json.dumps(value, indent=2)+"\n")
    print(json.dumps({"case": case["id"], "oracle_matched": value["oracle_matched"], "actual_exit": code}), flush=True)
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("reference", "faults", "inputs", "auxiliary"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", help="Run one planned case only, for diagnosis")
    parser.add_argument("--accept-eula", action="store_true", required=True)
    options = parser.parse_args()
    root = Path(os.environ["MVT_SHARED_ROOT"])
    if not os.environ.get("MVT_SHARED_VOLUME") or not root.is_dir():
        parser.error("Use the trusted containerized calibration supervisor and named volume")
    import fcntl
    lock = (root/"calibration.lock").open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        parser.error("Another calibration phase is active; run phases serially")
    (root/"tmp").mkdir(exist_ok=True)
    tempfile.tempdir = str(root/"tmp")
    def interrupt(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupt)
    options.output.mkdir(parents=True, exist_ok=False)
    inputs = prepare_inputs(root / "calibration-inputs")
    if options.phase == "auxiliary":
        inputs["ch4os-prism"] = make_ch4os_prism(root / "calibration-inputs", root / "cache")
    images = {neo: {side: json.loads(docker("image", "inspect", f"mvt-{side}-dev:{neo}"))[0]["Id"]
                    for side in ("client", "server")} for neo in ("21.1.233", "21.1.248")}
    matrix = cases(options.phase)
    if options.case:
        matrix = [c for c in matrix if c["id"] == options.case]
        if not matrix:
            parser.error("Unknown case")
    summary = {"schema": 1, "phase": options.phase, "calibrator_sha256": identity(), "images": images,
               "reference_inputs": inputs, "planned_cases": matrix, "results": [], "complete": False}
    path = options.output / "phase.json"
    path.write_text(json.dumps(summary, indent=2))
    for case in matrix:
        value = run_case(case, options.output / case["id"], inputs, images, root / "cache")
        summary["results"].append(value)
        path.write_text(json.dumps(summary, indent=2))
        if not value["oracle_matched"]:
            print("Calibration stopped: a case did not match its planned outcome", flush=True)
            return 1
    summary["complete"] = True
    path.write_text(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
