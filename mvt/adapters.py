"""Versioned test adapters selected from verified installed JAR metadata."""
from __future__ import annotations

from dataclasses import dataclass
import io
import json
from pathlib import Path
import re
import tomllib
import zipfile

from .errors import CheckFailed, Unsupported


def inventory(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    direct: set[str] = set()

    def inspect(data: bytes, depth: int = 0) -> None:
        if depth > 4:
            raise Unsupported("Nested mod archive limit exceeded")
        with zipfile.ZipFile(io.BytesIO(data)) as jar:
            if "META-INF/neoforge.mods.toml" in jar.namelist():
                metadata = tomllib.loads(jar.read("META-INF/neoforge.mods.toml").decode("utf-8"))
                manifest = jar.read("META-INF/MANIFEST.MF").decode("utf-8", "replace") if "META-INF/MANIFEST.MF" in jar.namelist() else ""
                match = re.search(r"^Implementation-Version: (.+)$", manifest, re.MULTILINE)
                for mod in metadata.get("mods", []):
                    identity, version = mod["modId"], str(mod.get("version", ""))
                    if version == "${file.jarVersion}":
                        version = match.group(1).strip() if match else "unknown"
                    if depth == 0:
                        if identity in direct:
                            raise CheckFailed(f"Duplicate top-level installed mod ID: {identity}")
                        direct.add(identity)
                        result[identity] = version
                    elif identity not in direct:
                        previous = result.get(identity)
                        candidates = set(previous.removeprefix("jarjar_candidates:").split("|")) if previous else set()
                        candidates.add(version)
                        result[identity] = version if len(candidates) == 1 else "jarjar_candidates:"+"|".join(sorted(candidates))
            if "META-INF/jarjar/metadata.json" in jar.namelist():
                for entry in json.loads(jar.read("META-INF/jarjar/metadata.json")).get("jars", []):
                    info = jar.getinfo(entry["path"])
                    if info.file_size > 256 * 1024 * 1024:
                        raise Unsupported("Nested mod archive is too large")
                    inspect(jar.read(info), depth + 1)

    for path in sorted((root / "mods").glob("*.jar")):
        try:
            inspect(path.read_bytes())
        except (zipfile.BadZipFile, KeyError, ValueError) as exc:
            raise CheckFailed(f"Cannot inspect installed mod JAR: {path.name}") from exc
    return result


@dataclass(frozen=True)
class Adapter:
    name: str
    revision: str
    mod_id: str
    version_pattern: str
    block: str
    item: str
    x: int

    def compatible(self, versions: list[str]) -> bool:
        return all(re.fullmatch(self.version_pattern, candidate)
                   for v in versions for candidate in v.removeprefix("jarjar_candidates:").split("|"))

    def evidence(self) -> dict:
        return {"id": self.name, "revision": self.revision, "required_mod": self.mod_id,
                "supported_mod_versions": self.version_pattern,
                "scenarios": ["block_item_persistence", "client_interaction"]}


CREATE = Adapter("create", "1", "create", r"6\.0\.\d+(?:[-+].*)?", "create:andesite_casing", "create:andesite_alloy", 16)
REGISTRY = {CREATE.name: CREATE}


def select(choice: str, inventories: dict[str, dict[str, str]]) -> tuple[list[Adapter], dict]:
    sides = list(inventories.values())
    if not sides:
        raise Unsupported("Cannot select tests without installed inventories")
    requested = list(REGISTRY) if choice == "auto" else [] if choice == "none" else choice.split(",")
    if len(set(requested)) != len(requested) or any(n not in REGISTRY for n in requested):
        raise Unsupported("Unknown or duplicate test adapter")
    selected, unavailable = [], []
    for name in requested:
        adapter = REGISTRY[name]
        present = [side.get(adapter.mod_id) for side in sides]
        if choice == "auto" and not any(present):
            continue
        if not all(present) or not adapter.compatible(present):
            unavailable.append({"id": name, "reason": "missing on a required side or unsupported installed version"})
        else:
            selected.append(adapter)
    covered = {a.mod_id for a in selected}
    evidence = {"selection": choice, "selected": [a.evidence() for a in selected],
                "unavailable": unavailable,
                "mods_without_focused_coverage": sorted(set().union(*(set(s) for s in sides)) - covered)}
    return selected, evidence
