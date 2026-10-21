"""Reuse proven lifecycle operations without changing the release-facing runner."""
from __future__ import annotations

import json
import os
import platform
from pathlib import Path
from types import SimpleNamespace
import uuid

from smoke import Runner
from .adapters import Adapter
from .errors import CheckFailed, InfrastructureError, Unsupported
from .files import read_regular
from .inputs import Pack, audit
from .mounts import game_mount
from .report import sanitize


class Server(Runner):
    def __init__(self, options, pack: Pack, adapters: list[Adapter], out: Path, development=False):
        super().__init__(SimpleNamespace(output=str(out), pack_sha=pack.source.get("sha", ""),
                                        image=options.image, heap=options.heap,
                                        container_memory=options.container_memory, boot_timeout=options.boot_timeout))
        self.pack, self.adapters, self.development = pack, adapters, development
        self.network = self.name + "-network"
        self.created_network = False
        self.sensitive = options.suite != "server-lifecycle" and options.client_mode == "authenticated"
        self.runtime_mode = getattr(options, "server_mode", "production")
        self.development_image = getattr(options, "development_server_image", None)
        self.runtime_id = uuid.uuid4().hex
        self.runtime_control = pack.roots["server"].with_name("server-runtime-"+self.runtime_id)
        self.development_create_attempted = False

    def prepare(self):
        root = self.pack.roots["server"]
        audit(root, self.pack.hashes["server"])
        self.report["mode"] = self.runtime_mode
        if self.runtime_mode == "development":
            return self.prepare_development(root)
        self.docker("pull", self.args.image, timeout=600)
        image = json.loads(self.docker("image", "inspect", self.args.image))[0]
        self.report.update(image_id=image["Id"], image_digests=image.get("RepoDigests", []),
                           versions={"minecraft": self.pack.minecraft, "neoforge": self.pack.neoforge})
        flags = ["network", "create", "--label", "ch4os.mvt=disposable"]
        if self.development:
            flags += ["--internal"]
        self.docker(*flags, self.network)
        self.created_network = True
        env = {"EULA": "TRUE", "TYPE": "NEOFORGE", "VERSION": self.pack.minecraft,
               "NEOFORGE_VERSION": self.pack.neoforge, "INIT_MEMORY": "1G", "MAX_MEMORY": self.args.heap,
               "ENABLE_RCON": "TRUE", "RCON_PASSWORD": self.password,
               "ONLINE_MODE": "FALSE" if self.development else "TRUE", "LEVEL": "world", "SEED": "8675309",
               "SPAWN_PROTECTION": "0", "DIFFICULTY": "peaceful",
               "VIEW_DISTANCE": "4", "SIMULATION_DISTANCE": "4", "ENABLE_AUTOPAUSE": "FALSE",
               "ENABLE_AUTOSTOP": "FALSE", "OVERRIDE_SERVER_PROPERTIES": "TRUE"}
        # Installation needs internet. Attach the private scenario network only after boot.
        command = ["create", "--name", self.name, "--label", "ch4os.mvt=disposable", "--restart=no",
                   "--stop-timeout", "120", "--memory", self.args.container_memory,
                   "--memory-swap", self.args.container_memory, "--mount", game_mount(root, "/data")]
        for key, value in env.items():
            command += ["-e", f"{key}={value}"]
        self.docker(*command, image["Id"])
        self.created_container = True

    def prepare_development(self, root):
        if platform.system() != "Linux" or not self.development or not self.development_image:
            raise Unsupported("Provide a prepared development-server image for the isolated development client-server lane")
        image = json.loads(self.docker("image", "inspect", self.development_image))[0]
        labels = image.get("Config", {}).get("Labels", {})
        if image.get("Os") != "linux" or any(labels.get(key) != value for key, value in {
            "org.cha0s.mvt.server-protocol": "1", "org.cha0s.mvt.runtime": "development",
            "org.cha0s.mvt.minecraft": self.pack.minecraft, "org.cha0s.mvt.neoforge": self.pack.neoforge,
        }.items()):
            raise Unsupported("Development-server image protocol/runtime/version differs from the candidate")
        manifest = json.loads(self.docker("run", "--rm", "--network", "none", "--read-only", "--entrypoint", "cat",
                                         image["Id"], "/opt/mvt/server-mod/build/mvt-server-launch.json"))
        bootstrap = manifest.get("bootstrap_sha256", "")
        if (manifest.get("protocol"), manifest.get("mode"), manifest.get("minecraft"), manifest.get("neoforge")) != (
            1, "development", self.pack.minecraft, self.pack.neoforge
        ) or not isinstance(bootstrap, str) or len(bootstrap) != 64 or any(c not in "0123456789abcdef" for c in bootstrap):
            raise Unsupported("Prepared server launch lacks a matching runtime/bootstrap identity")
        self.report.update(image_id=image["Id"], image_digests=image.get("RepoDigests", []), bootstrap_sha256=bootstrap,
                           versions={"minecraft": self.pack.minecraft, "neoforge": self.pack.neoforge})
        self.runtime_control.mkdir(mode=0o700, exist_ok=False)
        self.docker("network", "create", "--internal", "--label", "ch4os.mvt=disposable", self.network)
        self.created_network = True
        command = ["create", "--name", self.name, "--init", "--network", self.network,
                   "--label", "ch4os.mvt=disposable", "--label", "ch4os.mvt.run="+self.runtime_id,
                   "--restart=no", "--stop-timeout", "120", "--user", str(os.getuid()),
                   "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--read-only",
                   "--memory", self.args.container_memory, "--memory-swap", self.args.container_memory,
                   "--tmpfs", "/tmp:rw,exec,nosuid,size=1g,mode=1777",
                   "--mount", game_mount(root, "/data"), "--mount", game_mount(self.runtime_control, "/control")]
        for key, value in {"EULA": "TRUE", "MVT_SERVER_MODE": "development", "MVT_SERVER_RUN_ID": self.runtime_id,
                           "MVT_RUNTIME_DIR": "/control", "MVT_SERVER_HEAP": self.args.heap,
                           "RCON_PASSWORD": self.password, "RCON_PORT": "25575", "HOME": "/tmp"}.items():
            command += ["-e", f"{key}={value}"]
        self.development_create_attempted = True
        self.docker(*command, image["Id"])
        self.created_container = True

    def isolate(self):
        if self.runtime_mode == "development":
            networks = json.loads(self.docker("inspect", "--format", "{{json .NetworkSettings.Networks}}", self.name))
            internal = json.loads(self.docker("network", "inspect", "--format", "{{json .Internal}}", self.network))
            if not isinstance(networks, dict) or set(networks) != {self.network} or internal is not True:
                raise InfrastructureError("Development server must use only its private internal network")
            return
        self.docker("network", "connect", self.network, self.name)
        if self.development:
            self.docker("network", "disconnect", "bridge", self.name)

    def boot(self, phase):
        if self.runtime_mode == "development":
            (self.runtime_control / "runtime.json").unlink(missing_ok=True)
        super().boot(phase)
        if self.runtime_mode == "development":
            self.verify_runtime(phase)
        audit(self.pack.roots["server"], {p: h for p, h in self.pack.hashes["server"].items()
                                         if p.startswith("mods/") and p.endswith(".jar")})

    def verify_runtime(self, phase):
        try:
            value = json.loads(read_regular(self.runtime_control / "runtime.json", 16_384))
        except (OSError, ValueError) as exc:
            raise InfrastructureError("Development server did not publish a fresh runtime identity") from exc
        expected = {"protocol": 1, "run_id": self.runtime_id, "mode": "development", "minecraft": self.pack.minecraft,
                    "neoforge": self.pack.neoforge, "fml_production": False, "gametest_enabled": False}
        if not isinstance(value, dict) or any(type(value.get(k)) is not type(v) or value[k] != v for k, v in expected.items()):
            raise Unsupported("Development server runtime identity, version or GameTest mode differs from the requested lane")
        self.report.setdefault("development_boots", {})[phase] = {k: value[k] for k in expected}

    def rcon(self, command, timeout=30):
        answer = self.docker("exec", self.name, "rcon-cli", command, timeout=timeout)
        # Expanded-suite artifacts apply the same filtering to RCON probes and
        # replies as client traces/logs. Keep the legacy smoke.py contract intact.
        with (self.out / "commands.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(sanitize(self.redact(json.dumps({"phase": self.phase, "command": command,
                                                         "response": answer}))) + "\n")
        return answer

    def capture(self):
        if not self.created_container or not self.phase:
            return
        state = self.state()
        safe_state = {key: state.get(key) for key in
                      ("Status", "Running", "OOMKilled", "ExitCode", "StartedAt", "FinishedAt")}
        (self.out / f"{self.phase}-state.json").write_text(json.dumps(safe_state, indent=2))
        if not self.sensitive:
            logs = self.docker("logs", "--timestamps", "--since", self.since, self.name)
            (self.out / f"{self.phase}.log").write_text(sanitize(logs[-2_000_000:]), encoding="utf-8")

    def write_fixture(self):
        self.rcon("scoreboard objectives add mvt dummy")
        self.check_command("forceload add -16 -16 31 15")
        self.loaded()
        self.check_command("fill -3 199 -3 27 199 6 minecraft:stone")
        self.check_command("setblock 0 200 0 minecraft:chest replace")
        self.check_command("setblock 1 200 0 minecraft:gold_block replace")
        self.check_command("item replace block 0 200 0 container.0 with minecraft:diamond 3")
        self.check_command(f"scoreboard players set #nonce mvt {self.nonce}")
        for a in self.adapters:
            self.check_command(f"setblock {a.x} 200 0 minecraft:chest replace")
            self.check_command(f"setblock {a.x+1} 200 0 {a.block} replace")
            self.check_command(f"item replace block {a.x} 200 0 container.0 with {a.item} 3")
        self.verify_fixture()

    def verify_fixture(self):
        if self.score("#nonce") != self.nonce:
            raise CheckFailed("Save/reload nonce mismatch")
        self.loaded()
        self.check_command("execute if block 1 200 0 minecraft:gold_block")
        self.check_command('execute if data block 0 200 0 Items[{Slot:0b,id:"minecraft:diamond",count:3}]')
        for a in self.adapters:
            self.check_command(f"execute if block {a.x+1} 200 0 {a.block}")
            self.check_command(f'execute if data block {a.x} 200 0 Items[{{Slot:0b,id:"{a.item}",count:3}}]')
        self.tick()

    def cleanup(self):
        errors = []
        if self.development_create_attempted and not self.created_container:
            try:
                names = self.docker("ps", "-a", "--filter", "label=ch4os.mvt.run="+self.runtime_id, "--format", "{{.Names}}").splitlines()
                if names == [self.name]:
                    self.created_container = True
                elif names:
                    errors.append("Development server ownership mismatch during cleanup")
            except Exception as exc:
                errors.append(self.redact(str(exc)))
        if self.created_container:
            for operation in (self.capture, lambda: self.docker("stop", "--time", "120", self.name, timeout=150),
                              self.capture, lambda: self.docker("rm", "-f", self.name)):
                try:
                    operation()
                except Exception as exc:
                    errors.append(self.redact(str(exc)))
        if self.created_network:
            try:
                self.docker("network", "rm", self.network)
            except Exception as exc:
                errors.append(self.redact(str(exc)))
        return errors
