import hashlib
import io
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from mvt.adapters import inventory, select
from mvt.errors import CheckFailed, Unsupported
from mvt.inputs import Cache, audit, packwiz, pinned_source, prism, runtime, safe_path


def modjar(mod_id="example", version="1.0", nested=None, kind=None, connector=False):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as z:
        properties = '[properties]\n"connector:placeholder"=true\n' if connector else ''
        z.writestr("META-INF/neoforge.mods.toml", properties+f'[[mods]]\nmodId="{mod_id}"\nversion="{version}"\n')
        if connector:
            z.writestr("fabric.mod.json", json.dumps({"id": mod_id, "version": version}))
        if kind:
            z.writestr("META-INF/MANIFEST.MF", f"Manifest-Version: 1.0\r\nFMLModType: {kind}\r\n\r\n")
        if nested:
            z.writestr("META-INF/jarjar/metadata.json", json.dumps({"jars": [{"path": "nested.jar"}]}))
            z.writestr("nested.jar", nested)
    return stream.getvalue()


class InputTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.cache = Cache(self.root / "cache")

    def archive(self, extra=None, cfg="[General]\nname=test\n", mc="1.21.1", neo="21.1.248"):
        path = self.root / "instance.zip"
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("mmc-pack.json", json.dumps({"components": [
                {"uid": "net.minecraft", "version": mc}, {"uid": "net.neoforged", "version": neo}]}))
            z.writestr("instance.cfg", cfg)
            z.writestr(".minecraft/mods/example.jar", modjar())
            for name, data in (extra or {}).items():
                z.writestr(name, data)
        return path

    def test_runtime_scope_and_exact_version(self):
        for version in ("21.1.233", "21.1.248"):
            self.assertEqual(runtime({"minecraft": "1.21.1", "neoforge": version}), ("1.21.1", version))
        for versions in ({"minecraft": "1.21.2", "neoforge": "21.1.248"},
                         {"minecraft": "1.21.1", "neoforge": "latest"},
                         {"minecraft": "1.21.1", "neoforge": "21.1.248", "fabric": "x"}):
            with self.subTest(versions=versions), self.assertRaises(Unsupported):
                runtime(versions)

    def test_unsafe_paths_and_mutable_sources(self):
        for name in ("../x", "/x", "a/../x", "a\\x", "a//x", "a/NUL.jar", "a/x:", "a/x.", "./x"):
            with self.subTest(name=name), self.assertRaises(CheckFailed):
                safe_path(name)
        with self.assertRaises(CheckFailed):
            pinned_source("owner/repo", "main", "pack")
        with self.assertRaises(CheckFailed):
            pinned_source("owner/repo", "a"*40, "/pack")

    def test_cache_verifies_hits_and_never_refreshes_bad_bytes(self):
        data = b"pinned"
        digest = hashlib.sha256(data).hexdigest()
        with patch("mvt.inputs.fetch", return_value=data) as download:
            self.assertEqual(self.cache.get("https://example.test/a", "sha256", digest), data)
            self.assertEqual(self.cache.get("https://example.test/a", "sha256", digest), data)
            self.assertEqual(download.call_count, 1)
        (self.cache.root / "sha256" / digest).write_bytes(b"corrupt")
        with patch("mvt.inputs.fetch") as download, self.assertRaises(CheckFailed):
            self.cache.get("https://example.test/a", "sha256", digest)
        download.assert_not_called()

    def test_prism_bundled_client_only_and_audit(self):
        pack = prism(self.archive(), self.root / "work", self.cache)
        self.assertEqual(set(pack.roots), {"client"})
        audit(pack.roots["client"], pack.hashes["client"])
        self.assertEqual(inventory(pack.roots["client"]), {"example": "1.0"})
        (pack.roots["client"] / "mods" / "extra.jar").write_bytes(modjar("extra"))
        with self.assertRaises(CheckFailed):
            audit(pack.roots["client"], pack.hashes["client"])

    def test_prism_hooks_rejected_in_general_section(self):
        for key in ("WrapperCommand", "PostExitCommand", "PreLaunchCommand"):
            with self.subTest(key=key), self.assertRaises(Unsupported):
                prism(self.archive(cfg=f"[General]\n{key}=echo unsafe\n"), self.root / "work", self.cache)

    def test_prism_preserves_bounded_license_notices(self):
        name = "third-party-notices/industrialized-architecture-LICENSE.txt"
        data = b"Original third-party license\n"
        pack = prism(self.archive({".minecraft/"+name: data}), self.root / "notices", self.cache)
        self.assertEqual((pack.roots["client"]/name).read_bytes(), data)
        self.assertEqual(pack.hashes["client"][name], hashlib.sha256(data).hexdigest())
        audit(pack.roots["client"], pack.hashes["client"])
        for i, (name, data) in enumerate((("launch.jar", b"jar"), ("accounts.json", b"{}"),
                                         ("binary.txt", b"\xff"), ("zero-byte.txt", b"x\0y"),
                                         ("huge.txt", b"x"*(1024*1024+1)))):
            with self.subTest(name=name), self.assertRaises(Unsupported):
                prism(self.archive({".minecraft/third-party-notices/"+name: data}), self.root/f"bad-notice-{i}", self.cache)

    def test_prism_traversal_duplicate_and_symlink(self):
        with self.assertRaises(CheckFailed):
            prism(self.archive({"../escape": "bad"}), self.root / "work", self.cache)
        with self.assertRaises(CheckFailed):
            prism(self.archive({".Minecraft/mods/example.jar": "bad"}), self.root / "work", self.cache)
        archive = self.archive()
        with zipfile.ZipFile(archive, "a") as z:
            entry = zipfile.ZipInfo(".minecraft/mods/link.jar")
            entry.external_attr = (stat.S_IFLNK | 0o777) << 16
            z.writestr(entry, "../../accounts.json")
        with self.assertRaises(CheckFailed):
            prism(archive, self.root / "work", self.cache)

    def test_prism_private_data_and_wrong_minecraft_rejected(self):
        with self.assertRaises(Unsupported):
            prism(self.archive({".minecraft/accounts.json": "secret"}), self.root / "work", self.cache)
        with self.assertRaises(Unsupported):
            prism(self.archive(mc="1.20.1"), self.root / "work", self.cache)

    def test_packwiz_hash_chain_side_selection_and_optional_defaults(self):
        base = pinned_source("test/repo", "a"*40, "pack")
        blobs = {}
        entries = []
        for name, side, enabled in (("shared", "both", None), ("client", "client", None), ("off", "both", False)):
            data = modjar(name)
            download = "https://example.test/" + name
            blobs[download] = data
            optional = '' if enabled is None else '\n[option]\noptional=true\ndefault=false\n'
            meta = f'filename="{name}.jar"\nside="{side}"\n[download]\nurl="{download}"\nhash-format="sha256"\nhash="{hashlib.sha256(data).hexdigest()}"\n{optional}'.encode()
            path = f"mods/{name}.pw.toml"
            blobs[base+path] = meta
            entries.append(f'[[files]]\nfile="{path}"\nhash="{hashlib.sha256(meta).hexdigest()}"\nmetafile=true\n')
        index = ('hash-format="sha256"\n'+''.join(entries)).encode()
        blobs[base+"index.toml"] = index
        blobs[base+"pack.toml"] = f'[versions]\nminecraft="1.21.1"\nneoforge="21.1.233"\n[index]\nfile="index.toml"\nhash-format="sha256"\nhash="{hashlib.sha256(index).hexdigest()}"\n'.encode()
        with patch("mvt.inputs.fetch", side_effect=lambda url, *args: blobs[url]):
            pack = packwiz("test/repo", "a"*40, "pack", self.root / "work", self.cache)
        self.assertEqual(set(pack.hashes["server"]), {"mods/shared.jar"})
        self.assertEqual(set(pack.hashes["client"]), {"mods/shared.jar", "mods/client.jar"})
        self.assertFalse(pack.selections[-1]["enabled"])
        blobs[base+"index.toml"] = b"corrupt"
        with patch("mvt.inputs.fetch", side_effect=lambda url, *args: blobs[url]), self.assertRaises(CheckFailed):
            packwiz("test/repo", "a"*40, "pack", self.root / "bad", self.cache)

    def test_nested_mod_inventory_duplicate_id_and_adapters(self):
        root = self.root / "pack"
        (root / "mods").mkdir(parents=True)
        (root / "mods" / "nested.jar").write_bytes(modjar("outer", nested=modjar("create", "6.0.6")))
        mods = inventory(root)
        adapters, evidence = select("auto", {"client": mods, "server": mods})
        self.assertEqual([a.name for a in adapters], ["create"])
        self.assertEqual(evidence["mods_without_focused_coverage"], ["outer"])
        selected, evidence = select("auto", {"client": {"create": "7.0.0"}})
        self.assertFalse(selected)
        self.assertEqual(len(evidence["unavailable"]), 1)
        self.assertFalse(select("none", {"client": mods})[1]["unavailable"])
        self.assertTrue(select("create", {"server": {"example": "1"}})[1]["unavailable"])
        self.assertFalse(select("auto", {"server": {"example": "1"}})[1]["unavailable"])
        (root / "mods" / "dup.jar").write_bytes(modjar("outer"))
        with self.assertRaises(CheckFailed):
            inventory(root)

    def test_shared_jarjar_dependencies_are_candidates_not_duplicate_mods(self):
        root = self.root / "pack"
        (root / "mods").mkdir(parents=True)
        (root / "mods" / "one.jar").write_bytes(modjar("one", nested=modjar("dependency", "1.0")))
        (root / "mods" / "two.jar").write_bytes(modjar("two", nested=modjar("dependency", "1.1")))
        self.assertEqual(inventory(root)["dependency"], "jarjar_candidates:1.0|1.1")

    def test_loader_library_is_recorded_without_false_gameplay_coverage(self):
        root = self.root / "pack"
        (root / "mods").mkdir(parents=True)
        (root / "mods" / "defaults.jar").write_bytes(modjar("configureddefaults", "21.1.3", kind="LIBRARY"))
        # A declared Create ID in a library must never activate gameplay tests.
        (root / "mods" / "library.jar").write_bytes(modjar("create", "6.0.10", kind="LIBRARY"))
        libraries = []
        mods = inventory(root, libraries=libraries)
        self.assertEqual(mods, {})
        self.assertEqual(libraries[0]["declared_ids"], {"configureddefaults": "21.1.3"})
        self.assertEqual(libraries[0]["kind"], "LIBRARY")
        self.assertFalse(select("auto", {"client": mods})[0])
        (root / "mods" / "actual-create.jar").write_bytes(modjar("create", "6.0.10"))
        self.assertEqual(inventory(root), {"create": "6.0.10"})
        self.assertEqual([a.name for a in select("auto", {"client": inventory(root)})[0]], ["create"])

    def test_connector_normalization_retains_source_version(self):
        root = self.root / "pack"
        (root / "mods").mkdir(parents=True)
        (root / "mods" / "continuity.jar").write_bytes(modjar("continuity", "3.0.0+1.21.neoforge", connector=True))
        changes = {}
        self.assertEqual(inventory(root, normalizations=changes)["continuity"], "3.0.0+1.21.neoforge")
        self.assertEqual(changes["continuity"]["runtime_version"], "3.0.0_1.21.neoforge")
        # Native NeoForge versions must never inherit a Connector conversion.
        (root / "mods" / "native.jar").write_bytes(modjar("native", "1.0+extra"))
        changes = {}
        inventory(root, normalizations=changes)
        self.assertNotIn("native", changes)


if __name__ == "__main__":
    unittest.main()
