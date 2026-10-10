"""Same client interactions for integrated and dedicated servers."""
import re
import time

from .errors import AuthRequired, CheckFailed, InfrastructureError


def count(state, item):
    return sum(s["count"] for s in state.get("inventory", []) if s["id"] == item)


class Session:
    def __init__(self, client, adapters, nonce, server=None):
        self.client, self.adapters, self.nonce, self.server = client, adapters, nonce, server
        self.control = client.control
        self.uuid = None

    def ready(self):
        def ready(state):
            if state.get("screen") == "ModMismatchDisconnectedScreen":
                raise CheckFailed("NeoForge mod-channel negotiation failed; inspect the bounded client log for the rejected channel")
            reason = state.get("disconnect_reason")
            if reason:
                if re.search(r"(?i)invalid session|verify username|authentication servers|not authenticated", reason):
                    raise AuthRequired("Client authentication/join prerequisite failed: " + reason)
                if re.search(r"(?i)connection refused|network is unreachable|unknown host|timed out", reason):
                    raise InfrastructureError("Disposable server connection unavailable: " + reason)
                raise CheckFailed("Client disconnected during join: " + reason)
            return state.get("world_ready")
        state = self.control.until(ready, timeout=self.client.options.boot_timeout)
        self.uuid = state.get("uuid")
        if not isinstance(self.uuid, str) or not re.fullmatch(r"[a-f0-9-]{36}", self.uuid):
            raise CheckFailed("Client returned an invalid player identity")
        if state.get("dimension") != "minecraft:overworld":
            raise CheckFailed("Client joined the wrong dimension")
        for identity, version in getattr(self.client, "expected_mods", {}).items():
            loaded = state.get("mods", {}).get(identity)
            if loaded is None:
                raise CheckFailed("Installed client mod did not load: " + identity)
            candidates = version.removeprefix("jarjar_candidates:").split("|")
            conversion = getattr(self.client, "expected_normalizations", {}).get(identity)
            if conversion and state.get("mods", {}).get(conversion["loader"]) and conversion["source_version"] in candidates:
                candidates.append(conversion["runtime_version"])
            if "unknown" not in candidates and not any("${" in v for v in candidates) and loaded not in candidates:
                raise CheckFailed("Loaded client mod version differs from installed metadata: " + identity)
        for adapter in self.adapters:
            version = state.get("mods", {}).get(adapter.mod_id)
            if not version or not adapter.compatible([version]):
                raise CheckFailed("Selected adapter mod did not load with its supported version")
        if state.get("mods", {}).get("neoforge") != self.client.pack.neoforge:
            raise CheckFailed("Loaded client NeoForge version differs from candidate")
        return state

    def command(self, command):
        if self.server:
            self.server.check_command(command)
        else:
            result = self.control.call("server_command", command=command, timeout=120)
            if result.get("success", 0) <= 0:
                raise CheckFailed("Integrated-server command/assertion failed: " + command)

    def save(self):
        if self.server:
            self.server.save()
        elif self.control.call("save_world", timeout=120).get("success", 0) <= 0:
            raise CheckFailed("Integrated server did not confirm the world save/flush")

    def setup(self):
        if self.server:
            self.server.rcon("scoreboard objectives add mvt dummy")
        else:
            self.command("scoreboard objectives add mvt dummy")
        self.command(f"scoreboard players set #nonce mvt {self.nonce}")
        self.command("forceload add -16 -16 31 15")
        for x, z in ((-3, -3), (-3, 6), (27, -3), (27, 6)):
            deadline = time.monotonic()+60
            while True:
                try:
                    self.command(f"execute if loaded {x} 200 {z}")
                    break
                except CheckFailed:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.2)
        self.command("fill -3 199 -3 27 199 6 minecraft:stone")
        # UUIDs are entity arguments, not selectors; execute as first so the
        # game-mode predicate applies to the actual player on either server.
        creative = f"execute as {self.uuid} if entity @s[gamemode=creative]"
        try:
            self.command(creative)
        except CheckFailed:
            self.command(f"execute as {self.uuid} run gamemode creative @s")
            self.command(creative)
        try:
            self.command(f"execute as {self.uuid} run clear @s")
        except CheckFailed:
            pass  # Empty inventories also return zero; prove the empty state below.
        self.command(f"execute unless data entity {self.uuid} Inventory[0]")
        self.control.until(lambda s: sum(item["count"] for item in s.get("inventory", [])) == 0)
        fixtures = [(0, "minecraft:gold_block", "minecraft:diamond")] + [(a.x, a.block, a.item) for a in self.adapters]
        for x, block, item in fixtures:
            for pos in ((x, 201, 0), (x+2, 200, 0), (x+2, 200, 3), (x+2, 201, 3), (x+3, 200, 3), (x+3, 201, 3)):
                position = " ".join(map(str, pos))
                try:
                    self.command(f"setblock {position} minecraft:air replace")
                except CheckFailed:
                    pass  # Already-air commands return zero; independently verify the required state.
                self.command(f"execute if block {position} minecraft:air")
            self.command(f"setblock {x} 200 0 minecraft:chest replace")
            self.command(f"item replace block {x} 200 0 container.0 with {item} 3")
            self.command(f"execute as {self.uuid} run give @s {block} 1")

    def interact(self):
        fixtures = [(0, "minecraft:gold_block", "minecraft:diamond")] + [(a.x, a.block, a.item) for a in self.adapters]
        for x, block, item in fixtures:
            # Decimal coordinates suppress Minecraft's implicit block-centering offset.
            self.command(f"tp {self.uuid} {x+2}.0 200.0 3.0")
            state = self.control.until(lambda s: abs(s.get("x", -1000)-(x+2)) < 0.2)
            self.control.until(lambda s: count(s, block) > 0)
            self.control.call("move", dx=0.5, dz=0.0)
            self.control.until(lambda s: s.get("tick", 0) >= state["tick"]+20 and s.get("x", 0) > x+2.2)
            self.command(f"execute as {self.uuid} positioned {x+2.5} 200.0 3.0 if entity @s[distance=..0.15]")
            self.control.call("place", x=x+2, y=200, z=0, item=block)
            self.control.until(lambda s: s.get("block") == block, x=x+2, y=200, z=0)
            self.command(f"execute if block {x+2} 200 0 {block}")
            self.control.call("open_container", x=x, y=200, z=0)
            self.control.until(lambda s: bool(s.get("menu")) and s["menu"][0] == {"id": item, "count": 3})
            self.control.call("transfer", slot=0)
            self.control.until(lambda s: count(s, item) == 3 and s.get("menu", [{}])[0].get("count") == 0)
            self.control.call("close_container")
            self.command(f'execute if data entity {self.uuid} Inventory[{{id:"{item}",count:3}}]')
            self.command(f"execute unless data block {x} 200 0 Items[0]")

    def verify(self):
        # Never create objectives, seed inventory, or repair fixtures here.
        self.ready()
        self.command(f"execute if score #nonce mvt matches {self.nonce}")
        fixtures = [(0, "minecraft:gold_block", "minecraft:diamond")] + [(a.x, a.block, a.item) for a in self.adapters]
        for x, block, item in fixtures:
            self.command(f"tp {self.uuid} {x+2}.0 200.0 3.0")
            self.control.until(lambda s: s.get("block") == block, x=x+2, y=200, z=0)
            self.command(f"execute if block {x+2} 200 0 {block}")
            self.command(f'execute if data entity {self.uuid} Inventory[{{id:"{item}",count:3}}]')
            self.control.until(lambda s: count(s, item) == 3)
            self.command(f"execute unless data block {x} 200 0 Items[0]")
