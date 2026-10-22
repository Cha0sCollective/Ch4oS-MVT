"""Reproducible local reference inputs; runtime assertions always use real games."""
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

from mvt.inputs import Cache, prism

PACK_SHA = "8106370ce2a239ae1727c4ed08173d08238e766b"


def write_zip(path, entries):
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(entries.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            if isinstance(data, Path):
                with data.open("rb") as source, archive.open(info, "w") as target:
                    shutil.copyfileobj(source, target, length=1024*1024)
            else:
                archive.writestr(info, data.encode() if isinstance(data, str) else data)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def descriptor(neo="21.1.233", mc="1.21.1", loader="net.neoforged"):
    return json.dumps({"formatVersion": 1, "components": [
        {"uid": "net.minecraft", "version": mc, "important": True},
        {"uid": loader, "version": neo, "important": True}]}, sort_keys=True)


def build_inputs(directory):
    directory.mkdir(parents=True, exist_ok=True)
    definition = Path(__file__).with_name("reference.json").read_bytes()
    entries = {"mmc-pack.json": descriptor(), "instance.cfg": "[General]\nname=MVT NeoForge-only reference\n",
               ".minecraft/options.txt": "onboardAccessibility:false\nskipMultiplayerWarning:true\n",
               ".minecraft/config/fml.toml": "earlyWindowControl = false\n"}
    result = {"definition_sha256": hashlib.sha256(definition).hexdigest()}
    for name, extra in {
        "no-create": {},
        "wrong-minecraft": {"mmc-pack.json": descriptor(mc="1.20.1")},
        "wrong-loader": {"mmc-pack.json": descriptor(neo="0.16.0", loader="net.fabricmc.fabric-loader")},
        "traversal": {"../escape.txt": "never extract"},
        "hook": {"instance.cfg": "[General]\nWrapperCommand=echo CALIBRATION_HOOK_MUST_NOT_RUN\n"},
    }.items():
        path = directory / (name+".zip")
        result[name] = {"path": str(path), "sha256": write_zip(path, {**entries, **extra})}
    return result


def local_pair(options, work):
    """Use the real Prism importer plus this fixture's explicit server declaration.

    Ordinary CLI Prism resolution is unchanged and still rejects a client-only
    archive without a server counterpart. This trusted calibration fixture has
    zero third-party mods and explicitly declares its server files as empty.
    """
    definition_bytes = Path(__file__).with_name("reference.json").read_bytes()
    definition = json.loads(definition_bytes)
    assert definition["third_party_mods"] == [] and definition["server_files"] == {}
    pack = prism(options.prism_zip, work / "reference", Cache(options.cache))
    assert (pack.minecraft, pack.neoforge) == (definition["minecraft"], definition["neoforge"])
    pack.source["calibration_reference_sha256"] = hashlib.sha256(definition_bytes).hexdigest()
    if options.suite == "client-server-smoke":
        root = work / "reference" / "server"
        root.mkdir()
        pack.roots["server"] = root
        pack.hashes["server"] = {}
        pack.source["explicit_server_source"] = {"kind": "calibration-declared-empty-server",
            "definition_sha256": pack.source["calibration_reference_sha256"], "files": {}}
        return pack, ("client", "server")
    return pack, ("client",)
