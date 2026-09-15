#!/usr/bin/env python3
"""Opt-in, cold-start lifecycle prototype; Python 3.11+ and local Docker required.

No graphical client, repository writes, live-server attachment, or published ports.
Exit 0 means ONLY the listed lifecycle assertions passed, not a release approval.
Log baselining, caches, upgrade fixtures, GameTests and client tests are NOT implemented.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import platform
import re
import secrets
import signal
import subprocess
import sys
import time
import tomllib
from datetime import datetime, timezone
from urllib.request import urlopen
import xml.etree.ElementTree as ET

REPO = "Cha0sCollective/Create-Ch4os-Packwiz"
STEPS = ("prepare", "boot_fresh", "tick_fresh", "write_fixture", "save_fresh",
         "stop_fresh", "boot_saved", "verify_persistence", "save_saved", "stop_saved")


class CheckFailed(RuntimeError):
    """A server assertion failed; consult the evidence before attributing cause."""


class InfrastructureError(RuntimeError):
    """The harness could not obtain valid test evidence."""


def parse_score(text: str, holder: str) -> int:
    # Intentionally fail closed on unknown/localized responses. Dedicated 1.21.1
    # console responses are expected; an RCON process exit code is not an assertion.
    match = re.fullmatch(re.escape(holder) + r" has (-?\d+) \[mvt\]", text.strip())
    if match is None:
        raise CheckFailed(f"Unrecognized scoreboard response: {text!r}")
    return int(match.group(1))


def parse_time(text: str) -> int:
    match = re.fullmatch(r"The time is (\d+)", text.strip())
    if match is None:
        raise CheckFailed(f"Unrecognized time response: {text!r}")
    return int(match.group(1))


def validate_sha(value: str) -> str:
    if not re.fullmatch(r"[0-9a-fA-F]{40}", value):
        raise argparse.ArgumentTypeError("Use a full 40-character pack commit SHA, not a branch.")
    return value.lower()


class Runner:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.name = "ch4os-mvt-" + secrets.token_hex(6)
        self.volume = self.name + "-data"
        self.password = secrets.token_hex(24)
        self.nonce = secrets.randbelow(1_000_000_000) + 1
        self.created_container = self.created_volume = False
        self.phase = ""
        self.since = ""
        self.out = Path(args.output).resolve()
        self.out.mkdir(parents=True, exist_ok=False)  # Never overwrite old evidence.
        self.report = {
            "schema": 1, "suite": "lifecycle-prototype-v1", "pack_repository": REPO,
            "pack_sha": args.pack_sha, "status": "running",
            "container": self.name, "volume": self.volume,
            "release_gate_approved": False, "log_comparison": "NOT_IMPLEMENTED",
            "limits": ["No log baseline", "No full installed-mod audit", "No upgrade test",
                       "No recipe/loot semantics", "No GameTests", "No client/network test"],
            "image_requested": args.image, "max_heap": args.heap,
            "container_memory": args.container_memory, "platform": platform.platform(),
            "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "tests": [{"name": n, "status": "skipped"} for n in STEPS],
        }

    def redact(self, text: str) -> str:
        return text.replace(self.password, "<RCON_PASSWORD>")

    def docker(self, *args: str, timeout: int = 30, check: bool = True) -> str:
        try:
            result = subprocess.run(["docker", *args], stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            # Never stringify subprocess command arguments: they may hold a password.
            raise InfrastructureError(f"Docker {args[0]} unavailable/timed out ({type(exc).__name__})") from exc
        output = self.redact(result.stdout)
        if check and result.returncode:
            raise InfrastructureError(f"Docker {args[0]} exited {result.returncode}: {output[-4000:]}")
        return output.strip()

    def rcon(self, command: str, timeout: int = 30) -> str:
        answer = self.docker("exec", self.name, "rcon-cli", command, timeout=timeout)
        with (self.out / "commands.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"phase": self.phase, "command": command,
                                     "response": answer}) + "\n")
        return answer

    def score(self, holder: str) -> int:
        return parse_score(self.rcon(f"scoreboard players get {holder} mvt"), holder)

    def check_command(self, command: str, timeout: int = 30) -> None:
        # Reset and verify first: parse errors must not reuse a previous success.
        self.rcon("scoreboard players set #probe mvt 0")
        if self.score("#probe") != 0:
            raise CheckFailed("Probe could not be reset")
        response = self.rcon(f"execute store success score #probe mvt run {command}", timeout)
        if self.score("#probe") != 1:
            raise CheckFailed(f"Command/assertion failed: {command}\n{response}")

    def state(self) -> dict:
        return json.loads(self.docker("inspect", "--format", "{{json .State}}", self.name))

    def prepare(self) -> None:
        base = f"https://raw.githubusercontent.com/{REPO}/{self.args.pack_sha}/pack/"
        try:
            with urlopen(base + "pack.toml", timeout=30) as response:
                pack_bytes = response.read(1_000_001)
            pack = tomllib.loads(pack_bytes.decode("utf-8"))
            index = pack["index"]
            if index["file"] != "index.toml" or index["hash-format"] != "sha256":
                raise CheckFailed("Prototype supports this pack's SHA-256 index.toml format only")
            with urlopen(base + "index.toml", timeout=30) as response:
                index_bytes = response.read(5_000_001)
        except (OSError, ValueError, KeyError) as exc:
            raise InfrastructureError(f"Cannot obtain/parse pinned pack manifest: {exc}") from exc
        if hashlib.sha256(index_bytes).hexdigest() != index["hash"]:
            raise CheckFailed("Committed Packwiz index hash mismatch; do not auto-refresh it")
        versions = pack["versions"]
        if versions.get("minecraft") != "1.21.1" or not re.fullmatch(r"21\.1\.\d+", versions.get("neoforge", "")):
            raise CheckFailed("This example is scoped to Minecraft 1.21.1 / NeoForge 21.1.x")
        self.report.update({"versions": versions, "pack_sha256": hashlib.sha256(pack_bytes).hexdigest(),
                            "index_sha256": index["hash"]})
        (self.out / "pack.toml").write_bytes(pack_bytes)
        (self.out / "index.toml").write_bytes(index_bytes)
        self.docker("pull", self.args.image, timeout=600)
        image = json.loads(self.docker("image", "inspect", self.args.image))[0]
        self.report["image_id"] = image["Id"]
        self.report["image_digests"] = image.get("RepoDigests", [])
        self.created_volume = True
        self.docker("volume", "create", "--label", "ch4os.mvt=disposable", self.volume)
        environment = {
            "EULA": "TRUE", "TYPE": "NEOFORGE", "VERSION": versions["minecraft"],
            "NEOFORGE_VERSION": versions["neoforge"], "PACKWIZ_URL": base + "pack.toml",
            "INIT_MEMORY": "1G", "MAX_MEMORY": self.args.heap, "ENABLE_RCON": "TRUE",
            "RCON_PASSWORD": self.password, "ONLINE_MODE": "TRUE", "LEVEL": "world",
            "SEED": "8675309", "VIEW_DISTANCE": "4", "SIMULATION_DISTANCE": "4",
            "ENABLE_AUTOPAUSE": "FALSE", "ENABLE_AUTOSTOP": "FALSE",
        }
        command = ["create", "--name", self.name, "--label", "ch4os.mvt=disposable",
                   "--restart=no", "--stop-timeout", "120", "--memory", self.args.container_memory,
                   "--memory-swap", self.args.container_memory, "-v", f"{self.volume}:/data"]
        for key, value in environment.items():
            command += ["-e", f"{key}={value}"]
        # Use the resolved image ID so a floating tag cannot move between pull/create.
        self.created_container = True
        self.docker(*command, image["Id"])

    def boot(self, phase: str) -> None:
        self.phase = phase
        self.since = datetime.now(timezone.utc).isoformat()
        self.docker("start", self.name)
        deadline = time.monotonic() + self.args.boot_timeout
        while time.monotonic() < deadline:
            state = self.state()
            if state.get("OOMKilled"):
                raise InfrastructureError("Container memory limit exceeded; evidence is inconclusive")
            if not state["Running"]:
                raise CheckFailed(f"Server exited before ready: exit={state['ExitCode']}")
            logs = self.docker("logs", "--since", self.since, self.name)
            # A prior boot's Done line is excluded by --since.
            if re.search(r"Done \([\d.]+s\)!", logs):
                try:
                    parse_time(self.rcon("time query gametime", timeout=10))
                    self.report.setdefault("java_runtime", self.docker("exec", self.name, "java", "-version"))
                    return
                except (CheckFailed, InfrastructureError):
                    pass
            time.sleep(2)
        raise InfrastructureError("Boot/readiness deadline exceeded; inspect logs and runner resources")

    def tick(self) -> None:
        start = parse_time(self.rcon("time query gametime"))
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            time.sleep(1)
            if parse_time(self.rcon("time query gametime")) - start >= 100:
                return
        raise CheckFailed("Server failed to advance 100 ticks within 60 seconds")

    def loaded(self) -> None:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                self.check_command("execute if loaded 0 200 0")
                return
            except CheckFailed:
                time.sleep(1)
        raise CheckFailed("Fixture chunk did not load")

    def write_fixture(self) -> None:
        self.rcon("scoreboard objectives add mvt dummy")
        self.check_command("forceload add 0 0")
        self.loaded()
        self.check_command("setblock 0 200 0 minecraft:chest replace")
        self.check_command("setblock 1 200 0 create:andesite_casing replace")
        self.check_command("item replace block 0 200 0 container.0 with create:andesite_alloy 3")
        self.check_command(f"scoreboard players set #nonce mvt {self.nonce}")
        self.verify_fixture()

    def verify_fixture(self) -> None:
        # Crucial: this method does NOT recreate the objective, nonce, blocks or items.
        if self.score("#nonce") != self.nonce:
            raise CheckFailed("Save/reload nonce mismatch")
        self.loaded()
        self.check_command("execute if block 1 200 0 create:andesite_casing")
        self.check_command('execute if data block 0 200 0 Items[{Slot:0b,id:"create:andesite_alloy",count:3}]')
        self.tick()

    def save(self) -> None:
        self.check_command("save-all flush", timeout=120)

    def capture(self) -> None:
        if self.created_container and self.phase:
            text = self.docker("logs", "--timestamps", "--since", self.since, self.name)
            (self.out / f"{self.phase}.log").write_text(text + "\n", encoding="utf-8")
            state = self.state()
            # Do not export inspect.Config.Env (contains the RCON password).
            safe_state = {key: state.get(key) for key in
                          ("Status", "Running", "OOMKilled", "ExitCode", "StartedAt", "FinishedAt")}
            (self.out / f"{self.phase}-state.json").write_text(json.dumps(safe_state, indent=2), encoding="utf-8")

    def stop(self) -> None:
        before = self.state()
        if before.get("OOMKilled"):
            raise InfrastructureError("Container was OOM-killed before shutdown")
        if not before["Running"]:
            raise CheckFailed("Server exited before the requested shutdown")
        try:
            self.rcon("stop", timeout=20)
        except InfrastructureError:
            pass  # An RCON disconnect during stop is possible; exit state is authoritative.
        self.docker("wait", self.name, timeout=120)
        state = self.state()
        self.capture()
        if state.get("OOMKilled"):
            raise InfrastructureError("Container was OOM-killed")
        if state["Running"] or state["ExitCode"] != 0:
            raise CheckFailed(f"Unclean shutdown: exit={state['ExitCode']}")

    def write_reports(self) -> None:
        (self.out / "report.json").write_text(json.dumps(self.report, indent=2) + "\n", encoding="utf-8")
        root = ET.Element("testsuite", name="Ch4oS lifecycle prototype", tests=str(len(STEPS)))
        for result in self.report["tests"]:
            case = ET.SubElement(root, "testcase", name=result["name"], time=str(result.get("seconds", 0)))
            if result["status"] in ("skipped", "running"):
                ET.SubElement(case, "skipped", message="Not completed")
            elif result["status"] in ("failed", "error"):
                child = ET.SubElement(case, "failure" if result["status"] == "failed" else "error")
                child.text = result.get("detail", "")
        if self.report.get("cleanup_errors"):
            case = ET.SubElement(root, "testcase", name="cleanup")
            ET.SubElement(case, "error").text = "\n".join(self.report["cleanup_errors"])
        root.set("tests", str(len(root)))
        root.set("failures", str(len(root.findall("testcase/failure"))))
        root.set("errors", str(len(root.findall("testcase/error"))))
        root.set("skipped", str(len(root.findall("testcase/skipped"))))
        ET.ElementTree(root).write(self.out / "junit.xml", encoding="utf-8", xml_declaration=True)

    def run(self) -> int:
        actions = (self.prepare, lambda: self.boot("fresh"), self.tick, self.write_fixture,
                   self.save, self.stop, lambda: self.boot("saved"), self.verify_fixture, self.save, self.stop)
        code = 0
        try:
            for result, action in zip(self.report["tests"], actions):
                result["status"] = "running"
                self.write_reports()
                started = time.monotonic()
                try:
                    action()
                    result["status"] = "passed"
                except CheckFailed as exc:
                    result.update(status="failed", detail=self.redact(str(exc)))
                    code = 1
                    break
                except Exception as exc:
                    result.update(status="error", detail=self.redact(f"{type(exc).__name__}: {exc}"))
                    code = 2
                    break
                finally:
                    result["seconds"] = round(time.monotonic() - started, 3)
        finally:
            # Failure cleanup is NOT a passing graceful-shutdown test.
            cleanup_errors = []
            if self.created_container:
                for operation in (self.capture,
                                  lambda: self.docker("stop", "--time", "120", self.name, timeout=150),
                                  self.capture,
                                  lambda: self.docker("rm", "-f", self.name)):
                    try:
                        operation()
                    except Exception as exc:
                        cleanup_errors.append(self.redact(str(exc)))
            if self.created_volume:
                try:
                    self.docker("volume", "rm", self.volume)
                except Exception as exc:
                    cleanup_errors.append(self.redact(str(exc)))
            if cleanup_errors:
                self.report["cleanup_errors"] = cleanup_errors
                code = code or 2
            if not all(item["status"] == "passed" for item in self.report["tests"]):
                code = code or 2
            self.report["status"] = {0: "lifecycle_passed_logs_unreviewed", 1: "failed", 2: "error"}[code]
            self.write_reports()
        print(json.dumps({"status": self.report["status"], "output": str(self.out), "release_gate_approved": False}))
        return code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack-sha", required=True, type=validate_sha)
    parser.add_argument("--accept-eula", action="store_true", help="Operator explicitly accepts the Minecraft EULA")
    parser.add_argument("--image", default="itzg/minecraft-server:java21", help="Pin a reviewed Java 21 image digest for repeatable baselines")
    parser.add_argument("--heap", default="4G", help="Exploratory maximum Java heap, not a pack sizing guarantee")
    parser.add_argument("--container-memory", default="6g", help="Container limit including native memory")
    parser.add_argument("--boot-timeout", type=int, default=900)
    parser.add_argument("--output", default="mvt-results")
    args = parser.parse_args()
    if not args.accept_eula:
        parser.error("Nothing launched. Read the Minecraft EULA and explicitly pass --accept-eula to run.")
    if args.boot_timeout < 1:
        parser.error("--boot-timeout must be positive")
    for value in (args.heap, args.container_memory):
        if not re.fullmatch(r"[1-9]\d*[gGmM]", value):
            parser.error("Memory values must be positive whole G or M units")
    def interrupt(signum, frame):
        raise InfrastructureError(f"Interrupted by signal {signum}")
    signal.signal(signal.SIGTERM, interrupt)
    signal.signal(signal.SIGINT, interrupt)
    return Runner(args).run()


if __name__ == "__main__":
    sys.exit(main())
