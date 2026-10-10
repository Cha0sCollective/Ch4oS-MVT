"""Reuse proven lifecycle operations without changing the release-facing runner."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from smoke import Runner
from .adapters import Adapter
from .errors import CheckFailed
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

    def prepare(self):
        root = self.pack.roots["server"]
        audit(root, self.pack.hashes["server"])
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

    def isolate(self):
        self.docker("network", "connect", self.network, self.name)
        if self.development:
            self.docker("network", "disconnect", "bridge", self.name)

    def boot(self, phase):
        super().boot(phase)
        audit(self.pack.roots["server"], {p: h for p, h in self.pack.hashes["server"].items()
                                         if p.startswith("mods/") and p.endswith(".jar")})

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
