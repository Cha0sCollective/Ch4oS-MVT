"""Headless client supervision on an isolated Linux Docker worker."""
from __future__ import annotations

import configparser
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import time
import uuid

from .errors import AuthRequired, CheckFailed, InfrastructureError, Unsupported
from .inputs import audit
from .adapters import inventory
from .files import read_regular, safe_tree
from .protocol import Control
from .report import sanitize


def docker(*args, timeout=120):
    try:
        result = subprocess.run(["docker", *args], capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise InfrastructureError(f"Client Docker operation unavailable ({type(exc).__name__})") from exc
    if result.returncode:
        raise InfrastructureError("Client Docker operation failed: " + sanitize(result.stderr[-2000:]))
    return result.stdout.strip()


class Client:
    def __init__(self, options, pack, work: Path, evidence: Path, network: str | None):
        self.options, self.pack, self.work, self.evidence = options, pack, work, evidence
        self.network = network
        self.own_network = False
        self.name = "mvt-client-" + uuid.uuid4().hex[:12]
        self.control = Control(work / self.name, self.alive)
        self.process = None
        self.instance = None
        self.started = False
        self.root = pack.roots["client"]
        self.image_id = None
        safe_tree(self.root)
        self.expected_mods = inventory(self.root)
        self._state_checked = 0
        self._running = True
        self.account_lock = None
        self.create_attempted = False

    def prepare(self, verify_installation=True):
        if platform.system() != "Linux":
            raise Unsupported("Headless client suites require the isolated Linux worker; server suites also work locally")
        if not self.options.client_image:
            raise Unsupported("Provide a prepared --client-image for the pack's exact NeoForge version")
        image = json.loads(docker("image", "inspect", self.options.client_image))[0]
        if image.get("Os") != "linux":
            raise Unsupported("Client worker image must be Linux")
        labels = image.get("Config", {}).get("Labels", {})
        if self.options.client_mode == "development" and labels.get("org.cha0s.mvt.neoforge") != self.pack.neoforge:
            raise Unsupported("Development image NeoForge version differs from the candidate")
        if labels.get("org.cha0s.mvt.protocol") != "1":
            raise Unsupported("Client image has no compatible MVT control protocol label")
        self.image_id = image["Id"]
        automation_hash = None
        if self.options.client_mode == "development":
            manifest = json.loads(docker("run", "--rm", "--network", "none", "--read-only", "--entrypoint", "cat",
                                         self.image_id, "/opt/mvt/client-mod/build/mvt-launch.json"))
            if manifest.get("neoforge") != self.pack.neoforge:
                raise Unsupported("Prepared development launch differs from candidate NeoForge")
            automation_hash = manifest.get("automation_sha256")
            if not isinstance(automation_hash, str) or len(automation_hash) != 64:
                raise Unsupported("Prepared client image lacks its automation build identity")
        if verify_installation:
            audit(self.root, self.pack.hashes["client"])
        else:
            # Config/world changes are expected; candidate mod bytes must remain pinned.
            expected = {p: h for p, h in self.pack.hashes["client"].items() if p.startswith("mods/") and p.endswith(".jar")}
            if self.options.client_mode == "authenticated":
                expected["mods/mvt-client.jar"] = hashlib.sha256(Path(self.options.automation_jar).read_bytes()).hexdigest()
            audit(self.root, expected)
        if self.options.client_mode == "authenticated":
            if not self.options.launcher_dir or not self.options.account_profile or not self.options.automation_jar:
                raise AuthRequired("Provide a dedicated signed-in Prism root/profile and the built automation JAR")
            if not (Path(self.options.launcher_dir) / "accounts.json").is_file():
                raise AuthRequired("Dedicated Prism account store is unavailable; sign in interactively")
            if verify_installation:
                import fcntl
                self.account_lock = (Path(self.options.launcher_dir) / ".mvt-account.lock").open("a")
                try:
                    fcntl.flock(self.account_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise AuthRequired("Dedicated Prism account is already in use by MVT") from exc
            jar = Path(self.options.automation_jar)
            if not jar.is_file():
                raise Unsupported("Automation JAR is missing; build client-mod first")
            (self.root / "mods").mkdir(exist_ok=True)
            if "mods/mvt-client.jar" in self.pack.hashes["client"]:
                raise Unsupported("Candidate conflicts with reserved automation overlay filename")
            shutil.copy2(jar, self.root / "mods" / "mvt-client.jar")
        if self.network is None:
            self.network = self.name + "-network"
            docker("network", "create", "--label", "ch4os.mvt=disposable", *(["--internal"] if self.options.client_mode == "development" else []), self.network)
            self.own_network = True
        return {"image_id": self.image_id, "image_digests": image.get("RepoDigests", []),
                "mode": self.options.client_mode, "protocol": 1,
                "authentication": "NOT_TESTED" if self.options.client_mode == "development" else "REQUIRED",
                "automation_sha256": hashlib.sha256(Path(self.options.automation_jar).read_bytes()).hexdigest() if self.options.client_mode == "authenticated" else automation_hash}

    def start(self):
        uid = str(os.getuid())
        command = ["create", "--name", self.name, "--init", "--network", self.network,
                   "--label", "ch4os.mvt=disposable", "--label", "ch4os.mvt.run="+self.control.run_id, "--user", uid,
                   "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--read-only",
                   "--memory", self.options.client_memory, "--memory-swap", self.options.client_memory,
                   "--tmpfs", "/tmp:rw,exec,nosuid,size=1g,mode=1777",
                   "--mount", f"type=bind,src={self.root},dst=/game",
                   "--mount", f"type=bind,src={self.control.directory},dst=/control",
                   "-e", "MVT_CONTROL_DIR=/control", "-e", "MVT_RUN_ID="+self.control.run_id,
                   "-e", "LIBGL_ALWAYS_SOFTWARE=true", "-e", "GALLIUM_DRIVER=llvmpipe",
                   "-e", "ALSOFT_DRIVERS=null", "-e", "HOME=/tmp"]
        if self.options.client_mode == "development":
            self.create_attempted = True
            docker(*command, self.image_id)
            self.started = True
            docker("start", self.name)
        else:
            launcher = Path(self.options.launcher_dir).resolve()
            self.instance = launcher / "instances" / self.name
            self.instance.mkdir(parents=True, exist_ok=False)
            # Copy only candidate game state. No arbitrary instance hooks survive import.
            safe_tree(self.root)
            shutil.copytree(self.root, self.instance / ".minecraft")
            (self.instance / "mmc-pack.json").write_text(json.dumps({"formatVersion": 1, "components": [
                {"uid": "net.minecraft", "version": "1.21.1", "important": True},
                {"uid": "net.neoforged", "version": self.pack.neoforge, "important": True}]}))
            config = {"docker_command": command, "image_id": self.image_id,
                      "instance": str(self.instance), "launcher": str(launcher)}
            config_path = self.control.directory / "wrapper.json"
            config_path.write_text(json.dumps(config))
            wrapper = Path(__file__).with_name("launcher_wrapper.py").resolve()
            cfg = configparser.ConfigParser(interpolation=None)
            cfg["General"] = {"name": self.name, "OverrideCommands": "true",
                              "PreLaunchCommand": "", "PostExitCommand": "",
                              "WrapperCommand": f'python3 "{wrapper}" "{config_path}"',
                              "OverrideJavaLocation": "false"}
            with (self.instance / "instance.cfg").open("w") as file:
                cfg.write(file, space_around_delimiters=False)
            try:
                self.create_attempted = True
                self.process = subprocess.Popen(["xvfb-run", "-a", self.options.prism_launcher, "--dir", str(launcher),
                                                 "--launch", self.name, "--profile", self.options.account_profile],
                                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            except OSError as exc:
                raise InfrastructureError("Prism or Xvfb is unavailable") from exc
        self.control.until(lambda s: s.get("title_ready"), timeout=self.options.boot_timeout)

    def alive(self):
        if self.options.client_mode == "authenticated" and not self.started:
            marker = self.control.directory / "launched.json"
            if not marker.exists():
                if self.process and self.process.poll() is not None:
                    raise AuthRequired("Prism did not start the test client; check account entitlement/sign-in")
                return True
            self.started = True
        if not self.started:
            return True
        if time.monotonic() - self._state_checked < 1:
            return self._running
        state = json.loads(docker("inspect", "--format", "{{json .State}}", self.name))
        self._state_checked = time.monotonic()
        self._running = state.get("Running", False)
        if state.get("OOMKilled"):
            raise InfrastructureError("Client memory limit exceeded")
        if state.get("Status") == "created":
            if self.process and self.process.poll() is not None:
                raise InfrastructureError("Prism wrapper could not start the sandboxed client")
            self._running = True
            return True
        return self._running

    def quit(self):
        # Completion is written before Minecraft exits; graceful exit is verified separately.
        self.control.call("quit", timeout=120)
        exit_code = docker("wait", self.name, timeout=120)
        state = json.loads(docker("inspect", "--format", "{{json .State}}", self.name))
        if state.get("OOMKilled"):
            raise InfrastructureError("Client was OOM-killed")
        if exit_code != "0":
            raise CheckFailed("Client did not shut down cleanly")
        if self.instance:
            # Preserve the world/config for relaunch, excluding launch credentials.
            safe_tree(self.instance / ".minecraft")
            shutil.copytree(self.instance / ".minecraft", self.root, dirs_exist_ok=True)

    def cleanup(self):
        errors = []
        if self.process and self.process.poll() is None:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
                try:
                    self.process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    os.killpg(self.process.pid, signal.SIGKILL)
                    self.process.wait(timeout=20)
            except (OSError, subprocess.TimeoutExpired) as exc:
                errors.append("Could not stop Prism process group: " + type(exc).__name__)
        # Resolve ambiguous create/start outcomes using our full per-run ownership label.
        if self.create_attempted:
            try:
                labels = json.loads(docker("inspect", "--format", "{{json .Config.Labels}}", self.name))
                if (labels or {}).get("ch4os.mvt.run") == self.control.run_id:
                    self.started = True
                else:
                    self.started = False
                    errors.append("Client container ownership mismatch; no removal attempted")
            except InfrastructureError:
                if self.started:
                    errors.append("Could not verify client container ownership for cleanup")
                self.started = False
        if self.started:
            if self.options.client_mode == "development":
                try:
                    logs = docker("logs", self.name)
                    (self.evidence / f"{self.name}.log").write_text(sanitize(logs[-2_000_000:]), encoding="utf-8")
                except Exception as exc:
                    errors.append(str(exc))
            # Authenticated runtime logs are deliberately not exported: mods can print session data.
            try:
                docker("rm", "-f", self.name)
            except Exception as exc:
                errors.append(str(exc))
        for filename, source in (("events.jsonl", self.control.directory / "events.jsonl"),
                                 ("commands.jsonl", self.control.trace_path)):
            if source.exists():
                try:
                    (self.evidence / f"{self.name}-{filename}").write_text(sanitize(read_regular(source, 2_000_000).decode("utf-8", "replace")))
                except (OSError, InfrastructureError) as exc:
                    errors.append("Could not export client evidence: " + type(exc).__name__)
        if self.instance and self.instance.exists():
            try:
                shutil.rmtree(self.instance)
            except OSError as exc:
                errors.append("Could not remove disposable Prism instance: " + type(exc).__name__)
        if self.own_network:
            try:
                docker("network", "rm", self.network)
            except Exception as exc:
                errors.append(str(exc))
        if self.account_lock:
            self.account_lock.close()
        return errors
