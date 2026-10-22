"""Deliberate runtime mutations around unmodified MVT assertions.

Only the calibration entry point imports this file. No fault flag is added to
the ordinary CLI, client mod, server bootstrap or release-facing runner.
"""
import hashlib
import json
import os
from pathlib import Path
import signal
import time

from mvt.client import docker
from mvt.errors import InfrastructureError

TOKEN = "MVT_CALIBRATION_SYNTHETIC_TOKEN_6e54"


class Probe:
    def __init__(self, fault, directory):
        self.fault, self.directory = fault, directory
        self.clients, self.server = [], None
        self.fired, self.verified = False, False
        self.events, self.raw_calls, self.paused = [], {}, []
        self.holder = None
        self.step = None
        self.audit_offsets = {}

    def event(self, kind, **values):
        record = {"event": kind, "step": self.step, **values}
        self.events.append(record)
        with (self.directory / "probe-events.jsonl").open("a") as file:
            file.write(json.dumps(record)+"\n")
            file.flush()

    def track_client(self, client):
        self.clients.append(client)
        raw = client.control.call
        self.raw_calls[client.name] = raw
        until = client.control.until
        def call(operation, *, timeout=60, **arguments):
            self.before_call(client, operation, arguments)
            if self.fired and self.fault in ("timeout", "stale-response"):
                timeout = min(timeout, 2)
            result = raw(operation, timeout=timeout, **arguments)
            self.after_call(client, operation, arguments, result)
            return result
        def bounded(predicate, *, timeout=120, **arguments):
            if self.fired and self.fault in ("placement", "transfer", "inventory", "nonce", "saved-region"):
                timeout = min(timeout, 15)
            return until(predicate, timeout=timeout, **arguments)
        client.control.call, client.control.until = call, bounded
        return client

    def active(self):
        return self.clients[-1]

    def raw(self, operation, **arguments):
        return self.raw_calls[self.active().name](operation, **arguments)

    def command(self, command):
        if self.server:
            self.server.check_command(command)
            return None
        result = self.raw("server_command", command=command, timeout=120)
        if result.get("success", 0) <= 0:
            raise InfrastructureError("Calibration precondition command did not succeed")
        return result["success"]

    def owned_client(self):
        client = self.active()
        labels = json.loads(docker("inspect", "--format", "{{json .Config.Labels}}", client.name))
        if labels.get("ch4os.mvt.run") != client.control.run_id:
            raise InfrastructureError("Calibration refuses to mutate an unowned container")
        return client

    def mark(self, **evidence):
        self.verified = True
        self.event("fault_verified", fault=self.fault, evidence=evidence)

    def before_step(self, name):
        self.step = name
        self.event("before_step")
        if name.startswith("verify_"):
            if self.server:
                path = self.server.out / "commands.jsonl"
                self.audit_offsets[name] = len(path.read_text().splitlines()) if path.exists() else 0
        if not self.fault or self.fired:
            return
        entry = name in ("create_world", "join_server")
        if entry and self.fault in ("crash", "oom", "timeout", "stale-response", "interrupt"):
            client = self.owned_client()
            self.fired = True
            if self.fault == "interrupt":
                self.mark(signal="SIGTERM", target="supervisor")
                os.kill(os.getpid(), signal.SIGTERM)
            elif self.fault == "crash":
                docker("kill", "--signal", "KILL", client.name)
                self.mark(signal="SIGKILL", target="owned-client")
            elif self.fault == "oom":
                docker("update", "--memory", "64m", "--memory-swap", "64m", client.name)
                deadline = time.monotonic()+15
                while time.monotonic() < deadline:
                    state = json.loads(docker("inspect", "--format", "{{json .State}}", client.name))
                    if state.get("OOMKilled"):
                        break
                    time.sleep(0.2)
                if not state.get("OOMKilled"):
                    raise InfrastructureError("Calibration could not induce an actual cgroup OOM kill")
                time.sleep(1.1)  # Expire the supervisor's normal one-second liveness cache.
                self.mark(container_memory="64m", OOMKilled=True)
            else:
                docker("pause", client.name)
                self.paused.append(client)
                state = json.loads(docker("inspect", "--format", "{{json .State}}", client.name))
                if state.get("Paused") is not True:
                    raise InfrastructureError("Calibration could not pause the owned client")
                if self.fault == "stale-response":
                    path = client.control.directory / "response.json"
                    path.write_text(json.dumps({"protocol": 1, "run_id": "0"*32, "request_id": "1"*32,
                        "sequence": client.control.sequence+1, "status": "completed", "result": {"world_ready": True}}))
                self.mark(paused=True, operation_deadline_seconds=2, wrong_run_completion=self.fault == "stale-response")
        elif name == "verify_persistence" and self.fault == "nonce":
            self.fired = True
            before = self.server.score("#nonce") if self.server else self.raw("server_command", command="scoreboard players get #nonce mvt")["success"]
            self.command("scoreboard players add #nonce mvt 1")
            after = self.server.score("#nonce") if self.server else self.raw("server_command", command="scoreboard players get #nonce mvt")["success"]
            if after != before+1:
                raise InfrastructureError("Calibration did not change the real persisted nonce")
            self.mark(nonce_before=before, nonce_after=after)
        elif name == "quit_saved_client" and self.fault == "cleanup":
            self.fired = True
            client = self.owned_client()
            self.holder = "mvt-calibration-holder-"+client.control.run_id
            docker("run", "-d", "--name", self.holder, "--network", client.network,
                   "--label", "ch4os.mvt.calibration="+client.control.run_id,
                   "--read-only", "--cap-drop", "ALL", "--entrypoint", "python3",
                   client.options.client_image, "-c", "import time; time.sleep(600)")
            self.mark(held_network=client.network, holder=self.holder)

    def after_step(self, name):
        if self.fault == "saved-region" and not self.fired and name == ("stop_server" if self.server else "quit_client"):
            self.fired = True
            if self.server:
                root = self.server.pack.roots["server"]
                world = root / "world"
            else:
                root = self.active().root
                worlds = list((root / "saves").glob("mvt-*"))
                if len(worlds) != 1:
                    raise InfrastructureError("Calibration requires exactly one saved test world")
                world = worlds[0]
            region = world / "region" / "r.0.0.mca"
            if not region.is_file() or region.is_symlink() or not region.resolve().is_relative_to(root.resolve()):
                raise InfrastructureError("Expected saved fixture region is unavailable")
            digest = hashlib.sha256(region.read_bytes()).hexdigest()
            size = region.stat().st_size
            region.unlink()  # Exact owned region file; never delete an inferred tree.
            self.mark(removed_region=region.relative_to(root).as_posix(), sha256_before=digest, bytes_before=size)
        elif self.fault == "redaction" and not self.fired and name == "prepare_fixture":
            self.fired = True
            self.command("say accessToken="+TOKEN)
            self.mark(synthetic_marker_sha256=hashlib.sha256(TOKEN.encode()).hexdigest())
        self.event("after_step")

    def before_call(self, client, operation, arguments):
        if self.fired or self.step != "client_interactions":
            return
        if self.fault == "placement" and operation == "place":
            self.fired = True
            x, y, z = (arguments[k] for k in ("x", "y", "z"))
            self.command(f"setblock {x} {y} {z} minecraft:barrier replace")
            client.control.until(lambda s: s.get("block") == "minecraft:barrier", x=x, y=y, z=z)
            self.mark(occupied_target=[x,y,z], block="minecraft:barrier")
        elif self.fault == "transfer" and operation == "transfer":
            self.fired = True
            before = self.raw("snapshot")
            if before.get("menu", [{}])[0] != {"id": "minecraft:diamond", "count": 3}:
                raise InfrastructureError("Expected three-diamond transfer precondition missing")
            self.command("item replace block 0 200 0 container.0 with minecraft:diamond 2")
            client.control.until(lambda s: s.get("menu", [{}])[0].get("count") == 2)
            self.mark(chest_count_before=3, chest_count_after=2)

    def after_call(self, client, operation, arguments, result):
        if not self.fired and self.fault == "inventory" and self.step == "client_interactions" and operation == "close_container":
            self.fired = True
            state = self.raw("snapshot")
            count = sum(i["count"] for i in state["inventory"] if i["id"] == "minecraft:diamond")
            if count != 3:
                raise InfrastructureError("Client did not observe the three-item transfer before disagreement injection")
            self.command(f'execute as {state["uuid"]} run clear @s minecraft:diamond 1')
            self.command(f'execute if data entity {state["uuid"]} Inventory[{{id:"minecraft:diamond",count:2}}]')
            self.mark(client_count_observed=3, authoritative_count_after=2)

    def restore_pauses(self):
        for client in self.paused:
            docker("unpause", client.name)
        self.paused.clear()

    def cleanup_probe(self):
        self.restore_pauses()
        if self.holder:
            client = self.active()
            label = json.loads(docker("inspect", "--format", "{{json .Config.Labels}}", self.holder))
            if label.get("ch4os.mvt.calibration") != client.control.run_id:
                raise InfrastructureError("Calibration holder ownership mismatch")
            docker("rm", "-f", self.holder)
            docker("network", "rm", client.network)
            self.holder = None

    def no_reseeding(self):
        mutations = ("setblock ", "fill ", "give ", "item replace ", "scoreboard objectives add ", "forceload ", "scoreboard players set #nonce ")
        if self.server:
            path = self.server.out / "commands.jsonl"
            if not path.exists():
                return True
            rows = [json.loads(l) for l in path.read_text().splitlines()]
            return all(not row["command"].startswith(mutations) for start in self.audit_offsets.values() for row in rows[start:])
        if len(self.clients) < 2:
            return True
        path = self.directory / (self.clients[-1].name+"-commands.jsonl")
        if not path.exists():
            return False
        rows = [json.loads(l) for l in path.read_text().splitlines()]
        return not any(row.get("event") == "request" and row.get("operation") == "server_command" and
                       row.get("arguments", {}).get("command", "").startswith(mutations) for row in rows)
