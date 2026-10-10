"""Bounded, verified Packwiz/Prism import; no pack-supplied hooks execute."""
from __future__ import annotations

import configparser
from dataclasses import dataclass, field
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import stat
import tomllib
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen
import uuid
import zipfile

from .errors import CheckFailed, InfrastructureError, Unsupported

MAX_FILE = 256 * 1024 * 1024
MAX_ZIP = 4 * 1024 * 1024 * 1024


def candidate_path(value: str) -> PurePosixPath:
    path = safe_path(value)
    if path.parts[0].lower() in {"world", "world_nether", "world_the_end", "saves", "libraries", "versions", "assets", "natives", ".minecraft", "minecraft"}:
        raise Unsupported("Pack source must contain game files, not worlds or a bundled runtime")
    if path.name.lower() in {"accounts.json", "launcher_accounts.json", "session.json", "server.jar", "run.sh", "run.bat"}:
        raise Unsupported("Pack source contains private account data or a runtime launch file")
    return path


def safe_path(value: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise CheckFailed("Invalid archive/pack path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(p in (".", "..", "") for p in value.split("/")):
        raise CheckFailed("Unsafe archive/pack path")
    for part in path.parts:
        if ":" in part or part.rstrip(" .") != part or any(ord(c) < 32 for c in part):
            raise CheckFailed("Unsafe archive/pack filename")
        if re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part):
            raise CheckFailed("Reserved archive/pack filename")
    return path


def runtime(versions: dict) -> tuple[str, str]:
    if versions.get("minecraft") != "1.21.1" or any(k in versions for k in ("forge", "fabric", "quilt")):
        raise Unsupported("Only Minecraft 1.21.1 with NeoForge is supported")
    neo = versions.get("neoforge", "")
    if not isinstance(neo, str) or not re.fullmatch(r"21\.1\.\d+", neo):
        raise Unsupported("Require an exact NeoForge 21.1.x version for Minecraft 1.21.1")
    return "1.21.1", neo


def fetch(url: str, limit: int = MAX_FILE) -> bytes:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise Unsupported("Pack downloads must use HTTPS without embedded credentials")
    try:
        with urlopen(Request(url, headers={"User-Agent": "Ch4oS-MVT/0.2"}), timeout=60) as response:
            if urlsplit(response.url).scheme != "https":
                raise Unsupported("Download redirected outside HTTPS")
            data = response.read(limit + 1)
        if len(data) > limit:
            raise Unsupported("Download exceeds the bounded file size")
        return data
    except (OSError, ValueError) as exc:
        # Never include a URL/query or credentials in a transport exception.
        raise InfrastructureError(f"Download unavailable ({type(exc).__name__})") from exc


def verify(data: bytes, algorithm: str, expected: str) -> None:
    if algorithm not in ("sha1", "sha256", "sha512"):
        raise Unsupported(f"Unsupported Packwiz hash algorithm: {algorithm}")
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-fA-F]+", expected):
        raise CheckFailed("Invalid Packwiz content hash")
    if hashlib.new(algorithm, data).hexdigest() != expected.lower():
        raise CheckFailed("Pack content hash mismatch; never refresh candidate metadata")


class Cache:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def get(self, url: str, algorithm: str, digest: str) -> bytes:
        # Validate before constructing the cache path.
        if algorithm not in ("sha1", "sha256", "sha512") or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]+", digest) or len(digest) != hashlib.new(algorithm).digest_size*2:
            raise CheckFailed("Invalid cache content identity")
        path = self.root / algorithm / digest.lower()
        if path.exists():
            if path.is_symlink() or path.stat().st_size > MAX_FILE:
                raise CheckFailed("Invalid cached content file")
            data = path.read_bytes()
            verify(data, algorithm, digest)
            return data
        data = fetch(url)
        verify(data, algorithm, digest)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
        temporary.write_bytes(data)
        temporary.replace(path)
        return data


@dataclass
class Pack:
    minecraft: str
    neoforge: str
    source: dict
    roots: dict[str, Path]
    hashes: dict[str, dict[str, str]] = field(default_factory=dict)
    selections: list[dict] = field(default_factory=list)

    def evidence(self) -> dict:
        return {"source": self.source, "versions": {"minecraft": self.minecraft, "neoforge": self.neoforge},
                "files": self.hashes, "optional_selections": self.selections,
                "available_sides": sorted(self.roots)}


def pinned_source(repo: str, sha: str, path: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise CheckFailed("Require owner/repository for --pack-repo")
    if not re.fullmatch(r"[0-9a-fA-F]{40}", sha):
        raise CheckFailed("Require a full immutable source commit SHA")
    return f"https://raw.githubusercontent.com/{repo}/{sha.lower()}/{quote(str(safe_path(path.rstrip('/'))))}/"


def packwiz(repo: str, sha: str, path: str, work: Path, cache: Cache,
            sides: tuple[str, ...] = ("client", "server")) -> Pack:
    base = pinned_source(repo, sha, path)
    pack_bytes = fetch(base + "pack.toml", 1_000_000)
    try:
        manifest = tomllib.loads(pack_bytes.decode("utf-8"))
        mc, neo = runtime(manifest["versions"])
        index_ref = manifest["index"]
        index_name = str(safe_path(index_ref["file"]))
        index_bytes = fetch(base + quote(index_name), 8_000_000)
        verify(index_bytes, index_ref["hash-format"], index_ref["hash"])
        index = tomllib.loads(index_bytes.decode("utf-8"))
    except (KeyError, ValueError, UnicodeError) as exc:
        raise CheckFailed("Invalid Packwiz manifest/index") from exc
    roots = {side: work / side for side in sides}
    pack = Pack(mc, neo, {"kind": "packwiz", "repository": repo, "sha": sha.lower(), "path": path,
                         "pack_sha256": hashlib.sha256(pack_bytes).hexdigest(),
                         "index_sha256": hashlib.sha256(index_bytes).hexdigest()}, roots,
                {side: {} for side in sides})
    seen: set[str] = set()
    installed_size = 0
    for entry in index.get("files", []):
        name = str(safe_path(entry["file"]))
        if name.casefold() in seen:
            raise CheckFailed("Duplicate Packwiz index path")
        seen.add(name.casefold())
        data = cache.get(base + quote(name), entry.get("hash-format", index["hash-format"]), entry["hash"])
        destinations = sides
        target = name
        if entry.get("metafile", False):
            meta = tomllib.loads(data.decode("utf-8"))
            side = meta.get("side", "both")
            if side not in ("client", "server", "both"):
                raise CheckFailed("Invalid Packwiz side")
            optional = meta.get("option", {})
            if any(key in optional and not isinstance(optional[key], bool) for key in ("optional", "default")):
                raise CheckFailed("Optional-mod selections must be boolean pack defaults")
            enabled = not optional.get("optional", False) or optional.get("default", False)
            pack.selections.append({"metadata": name, "side": side, "enabled": bool(enabled)})
            if not enabled:
                continue
            destinations = tuple(s for s in sides if side in (s, "both"))
            filename = str(safe_path(meta["filename"]))
            if "/" in filename:
                raise CheckFailed("Metafile filename must be a basename")
            target = str(PurePosixPath(name).parent / filename)
            download = meta["download"]
            if "url" not in download:
                raise Unsupported("Metafile needs a direct download URL; provider-only downloads need operator provisioning")
            if not destinations:
                continue
            data = cache.get(download["url"], download["hash-format"], download["hash"])
        for side in destinations:
            candidate_path(target)
            installed_size += len(data)
            if installed_size > MAX_ZIP*len(sides):
                raise Unsupported("Materialized pack exceeds size limit")
            if target.casefold() in {p.casefold() for p in pack.hashes[side]}:
                raise CheckFailed("Two Packwiz entries install the same file")
            destination = roots[side] / target
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            pack.hashes[side][target] = hashlib.sha256(data).hexdigest()
    for root in roots.values():
        root.mkdir(parents=True, exist_ok=True)
    return pack


def _bootstrap(command: str) -> tuple[str, str, str]:
    # Recognize a data-only Packwiz bootstrap invocation. Do not evaluate shell syntax.
    args = shlex.split(command)
    if len(args) < 4 or args[1:3] != ["-jar", "packwiz-installer-bootstrap.jar"]:
        raise Unsupported("Prism export contains an unsupported launch hook")
    flags = args[3:-1]
    if flags not in ([], ["--bootstrap-no-update", "-s", "client"], ["-s", "client"]):
        raise Unsupported("Unsupported Packwiz bootstrap flags")
    parsed = urlsplit(args[-1])
    if parsed.scheme != "https" or parsed.netloc != "raw.githubusercontent.com" or parsed.query or parsed.fragment:
        raise Unsupported("Prism Packwiz source must be a pinned raw GitHub URL")
    parts = parsed.path.strip("/").split("/")
    if len(parts) < 5 or parts[-1] != "pack.toml" or not re.fullmatch(r"[0-9a-fA-F]{40}", parts[2]):
        raise Unsupported("Prism Packwiz source needs an immutable commit; export a fixed version")
    return "/".join(parts[:2]), parts[2], "/".join(parts[3:-1])


def prism(archive: Path, work: Path, cache: Cache) -> Pack:
    if archive.stat().st_size > MAX_ZIP:
        raise Unsupported("Prism archive exceeds size limit")
    with zipfile.ZipFile(archive) as z:
        seen: set[str] = set()
        total = 0
        entries = {}
        for entry in z.infolist():
            name = entry.filename.rstrip("/")
            safe_path(name)
            if name.casefold() in seen:
                raise CheckFailed("Duplicate archive path")
            seen.add(name.casefold())
            mode = entry.external_attr >> 16
            if stat.S_ISLNK(mode) or (mode and stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)):
                raise CheckFailed("Archive links/special files are not supported")
            if entry.flag_bits & 1 or entry.file_size > MAX_FILE:
                raise Unsupported("Encrypted or oversized archive entry")
            total += entry.file_size
            if total > MAX_ZIP or len(seen) > 100_000:
                raise Unsupported("Expanded archive exceeds size/count limit")
            if not entry.is_dir():
                entries[name] = entry
        descriptors = [p for p in entries if PurePosixPath(p).name == "mmc-pack.json"]
        if len(descriptors) != 1:
            raise Unsupported("Prism ZIP must contain exactly one instance descriptor")
        prefix = str(PurePosixPath(descriptors[0]).parent)
        prefix = "" if prefix == "." else prefix + "/"
        descriptor = json.loads(z.read(descriptors[0]))
        components = descriptor.get("components", [])
        versions = {c["uid"]: c.get("version", "") for c in components}
        if len(versions) != len(components):
            raise CheckFailed("Duplicate Prism runtime component")
        if any(uid in versions for uid in ("net.minecraftforge", "net.fabricmc.fabric-loader", "org.quiltmc.quilt-loader")):
            raise Unsupported("Prism instance uses another loader")
        mc, neo = runtime({"minecraft": versions.get("net.minecraft"), "neoforge": versions.get("net.neoforged")})
        if set(versions) - {"net.minecraft", "net.neoforged", "org.lwjgl3"}:
            raise Unsupported("Custom Prism runtime components are not supported")
        if "org.lwjgl3" in versions and versions["org.lwjgl3"] != "3.3.3":
            raise Unsupported("Custom LWJGL versions need a separately calibrated worker")
        if any(c.get("cached") or c.get("important") is False for c in components if c["uid"] in ("net.minecraft", "net.neoforged")):
            raise Unsupported("Custom Prism runtime components are not supported")
        cfg = configparser.ConfigParser(interpolation=None, strict=True)
        cfg.read_string("[instance]\n" + z.read(prefix + "instance.cfg").decode("utf-8"))
        values = dict(cfg["instance"])
        for section in cfg.sections():
            if section != "instance":
                for key, value in cfg[section].items():
                    if key in values:
                        raise CheckFailed("Duplicate Prism instance setting")
                    values[key] = value
        if any(values.get(k.lower(), "").strip() for k in ("WrapperCommand", "PostExitCommand")):
            raise Unsupported("Prism instance contains an executable hook")
        hook = values.get("prelaunchcommand", "").strip()
        if hook:
            repo, sha, path = _bootstrap(hook)
            pack = packwiz(repo, sha, path, work, cache)
            if (pack.minecraft, pack.neoforge) != (mc, neo):
                raise CheckFailed("Prism runtime disagrees with its Packwiz manifest")
            allowed = {prefix+"mmc-pack.json", prefix+"instance.cfg", prefix+".minecraft/packwiz-installer-bootstrap.jar", prefix+"minecraft/packwiz-installer-bootstrap.jar"}
            if set(entries) - allowed:
                raise Unsupported("Packwiz Prism export has extra bundled overrides; supply its pinned Packwiz source directly")
        else:
            root = work / "client"
            root.mkdir(parents=True)
            pack = Pack(mc, neo, {"kind": "prism"}, {"client": root}, {"client": {}})
            for name, entry in entries.items():
                game_prefix = next((prefix + p for p in (".minecraft/", "minecraft/") if name.startswith(prefix + p)), None)
                if not game_prefix:
                    if name not in (prefix+"mmc-pack.json", prefix+"instance.cfg", prefix+"instance.png", prefix+"icon.png"):
                        raise Unsupported("Prism export contains unsupported files outside its game directory")
                    continue
                target = name[len(game_prefix):]
                first = PurePosixPath(target).parts[0]
                if first not in ("mods", "config", "defaultconfigs", "kubejs", "scripts", "resourcepacks", "shaderpacks", "options.txt"):
                    raise Unsupported("Bundled instance includes unsupported runtime/user data")
                data = z.read(entry)
                if target.casefold() in {p.casefold() for p in pack.hashes["client"]}:
                    raise CheckFailed("Duplicate Prism game destination")
                dest = root / target
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
                pack.hashes["client"][target] = hashlib.sha256(data).hexdigest()
        with archive.open("rb") as file:
            pack.source["archive_sha256"] = hashlib.file_digest(file, "sha256").hexdigest()
        pack.source["instance_runtime"] = {"minecraft": mc, "neoforge": neo}
        return pack


def audit(root: Path, expected: dict[str, str]) -> None:
    for name, digest in expected.items():
        path = root / safe_path(name)
        relative_parents = [root / p for p in safe_path(name).parents if str(p) != "."]
        if root.is_symlink() or any(parent.is_symlink() for parent in relative_parents) or not path.resolve().is_relative_to(root.resolve()):
            raise CheckFailed(f"Installation contains a linked path: {name}")
        if not path.is_file() or path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise CheckFailed(f"Installation audit failed: {name}")
    expected_mods = {p for p in expected if p.startswith("mods/") and p.lower().endswith(".jar")}
    actual_mods = {p.relative_to(root).as_posix() for p in (root / "mods").rglob("*") if p.name.lower().endswith(".jar")}
    if actual_mods != expected_mods:
        raise CheckFailed("Installed mod inventory has missing or unmanaged JARs")
