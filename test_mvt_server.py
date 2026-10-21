import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mvt.__main__ import main, parser
from mvt.errors import InfrastructureError, Unsupported
from mvt.inputs import Pack
from mvt.server import Server
from smoke import Runner
from worker.server_launch import prepare


class DevelopmentServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.game = self.root / "server"
        self.game.mkdir()
        self.pack = Pack("1.21.1", "21.1.248", {}, {"server": self.game}, {"server": {}})
        self.options = parser().parse_args(["run", "--pack-sha", "a"*40, "--suite", "client-server-smoke",
            "--server-mode", "development", "--development-server-image", "prepared-server"])
        self.server = Server(self.options, self.pack, [], self.root / "evidence", development=True)
        self.calls = []
        self.labels = {"org.cha0s.mvt.server-protocol": "1", "org.cha0s.mvt.runtime": "development",
                       "org.cha0s.mvt.minecraft": "1.21.1", "org.cha0s.mvt.neoforge": "21.1.248"}
        self.manifest = {"protocol": 1, "mode": "development", "minecraft": "1.21.1", "neoforge": "21.1.248",
                         "bootstrap_sha256": "b"*64}

    def docker(self, *args, **kwargs):
        self.calls.append(args)
        if args[:2] == ("image", "inspect"):
            return json.dumps([{"Id": "sha256:pinned", "Os": "linux", "Config": {"Labels": self.labels}}])
        if args[0] == "run":
            return json.dumps(self.manifest)
        if args[:2] == ("network", "inspect"):
            return "true"
        if args[0] == "inspect":
            return json.dumps({self.server.network: {}})
        return ""

    def prepare(self):
        with patch("mvt.server.platform.system", return_value="Linux"), \
             patch.dict(os.environ, {"MVT_SHARED_ROOT": "", "MVT_SHARED_VOLUME": ""}), \
             patch("mvt.server.os.getuid", return_value=1000, create=True), \
             patch.object(self.server, "docker", side_effect=self.docker):
            self.server.prepare()

    def state(self):
        return {"protocol": 1, "run_id": self.server.runtime_id, "mode": "development", "minecraft": "1.21.1",
                "neoforge": "21.1.248", "fml_production": False, "gametest_enabled": False}

    def test_development_runtime_uses_prepared_image_and_internal_network(self):
        self.prepare()
        self.assertFalse(any(c[0] == "pull" for c in self.calls))
        create = next(c for c in self.calls if c[0] == "create")
        self.assertEqual(create[-1], "sha256:pinned")
        self.assertIn("--read-only", create)
        self.assertIn("--cap-drop", create)
        self.assertIn("--internal", next(c for c in self.calls if c[:2] == ("network", "create")))
        self.assertFalse(any("--publish" in c or "-p" in c or "docker.sock" in str(c) for c in self.calls))
        self.assertEqual(self.server.report["mode"], "development")
        self.assertEqual(self.server.report["bootstrap_sha256"], "b"*64)
        with patch.object(self.server, "docker", side_effect=self.docker):
            self.server.isolate()
        with patch.object(self.server, "docker", return_value="false"), self.assertRaises(InfrastructureError):
            self.server.isolate()
        with patch.object(self.server, "docker", side_effect=[json.dumps({self.server.network: {}}), "false"]), self.assertRaises(InfrastructureError):
            self.server.isolate()

    def test_wrong_loader_or_manifest_cannot_start_a_container(self):
        for item, key, value in ((self.labels, "org.cha0s.mvt.neoforge", "21.1.233"),
                                 (self.manifest, "mode", "production"), (self.manifest, "bootstrap_sha256", "x"*64)):
            with self.subTest(key=key), patch.dict(item, {key: value}), self.assertRaises(Unsupported):
                self.prepare()
        self.assertFalse(any(c[0] == "create" for c in self.calls))

    def test_runtime_requires_real_development_identity_and_no_gametests(self):
        self.server.runtime_control.mkdir()
        path = self.server.runtime_control / "runtime.json"
        for field, value in (("fml_production", True), ("fml_production", 0), ("gametest_enabled", True),
                             ("run_id", "stale"), ("neoforge", "21.1.233")):
            path.write_text(json.dumps({**self.state(), field: value}))
            with self.subTest(field=field, value=value), self.assertRaises(Unsupported):
                self.server.verify_runtime("fresh")
        path.write_text(json.dumps(self.state()))
        self.server.verify_runtime("fresh")
        self.assertFalse(self.server.report["development_boots"]["fresh"]["fml_production"])

    def test_restart_cannot_reuse_the_previous_runtime_marker(self):
        self.server.runtime_control.mkdir()
        marker = self.server.runtime_control / "runtime.json"
        marker.write_text(json.dumps(self.state()))
        with patch.object(Runner, "boot"), self.assertRaises(InfrastructureError):
            self.server.boot("saved")
        self.assertFalse(marker.exists())

    def test_ambiguous_create_is_cleaned_up_only_for_our_full_run_label(self):
        self.server.development_create_attempted = True
        def docker(*args, **kwargs):
            if args[0] == "ps":
                self.assertIn("label=ch4os.mvt.run="+self.server.runtime_id, args)
                return self.server.name
            self.calls.append(args)
            return ""
        with patch.object(self.server, "docker", side_effect=docker):
            self.assertEqual(self.server.cleanup(), [])
        self.assertIn(("rm", "-f", self.server.name), self.calls)

    def test_authenticated_and_noninteraction_modes_reject_development_server(self):
        for args in (("--client-mode", "authenticated"), ("--suite", "server-lifecycle")):
            output = self.root / ("rejected-"+args[1])
            with patch("mvt.__main__.resolve") as resolve:
                self.assertEqual(main(["run", "--pack-sha", "a"*40, "--accept-eula", "--server-mode", "development",
                                      "--suite", "client-server-smoke", *args, "--output", str(output)]), 2)
            resolve.assert_not_called()

    def test_launcher_preserves_pack_settings_but_forces_isolated_offline_properties(self):
        (self.game / "server.properties").write_text("custom.setting=preserved\nonline-mode=true\n")
        launch = {"command": ["java", "@vmargs.txt", "-Xmx2G", "-cp", "prepared", "Main", "--nogui"]}
        env = {"EULA": "TRUE", "MVT_SERVER_MODE": "development", "RCON_PASSWORD": "a"*32, "MVT_SERVER_HEAP": "4G"}
        command = prepare(self.game, launch, env)
        settings = dict(line.split("=", 1) for line in (self.game / "server.properties").read_text().splitlines() if "=" in line)
        self.assertEqual(settings["custom.setting"], "preserved")
        self.assertEqual(settings["online-mode"], "false")
        self.assertEqual(settings["rcon.password"], "a"*32)
        self.assertGreater(command.index("-Xmx4G"), command.index("-Xmx2G"))
        self.assertNotIn("a"*32, str(command))
        self.assertIn("-Djava.awt.headless=true", command)
        self.assertEqual((self.game / "eula.txt").read_text(), "eula=true\n")
        for invalid in ({"EULA": "FALSE"}, {"MVT_SERVER_MODE": "production"}, {"RCON_PASSWORD": "bad\nproperty=value"}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                prepare(self.game, launch, {**env, **invalid})


if __name__ == "__main__":
    unittest.main()
