import json
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from mvt.__main__ import main, parser
from mvt.client import Client
from mvt.errors import AuthRequired, CheckFailed, InfrastructureError
from mvt.inputs import Pack
from mvt.files import read_regular, safe_tree
from mvt.protocol import Control
from mvt.report import Report, sanitize
from mvt.scenarios import Session
import test_mvt_inputs


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def control(self, alive=lambda: True):
        return Control(self.root / "control", alive)

    def responder(self, control, transform):
        errors = []
        def respond():
            try:
                deadline = time.monotonic()+2
                path = control.directory / "request.json"
                while not path.exists() and time.monotonic() < deadline:
                    time.sleep(0.005)
                request = json.loads(path.read_text())
                result = {k: request[k] for k in ("protocol", "run_id", "request_id", "sequence")}
                result.update(status="completed", result={"tick": 42})
                result = transform(result)
                temp = control.directory / "fake.tmp"
                temp.write_text(json.dumps(result))
                temp.replace(control.directory / "response.json")
            except Exception as exc:
                errors.append(exc)
        thread = threading.Thread(target=respond)
        thread.start()
        self.addCleanup(thread.join, 3)
        return thread, errors

    def test_matching_completion_required_and_trace_written(self):
        control = self.control()
        thread, errors = self.responder(control, lambda value: value)
        self.assertEqual(control.call("snapshot", timeout=2), {"tick": 42})
        thread.join()
        self.assertFalse(errors)
        trace = [json.loads(line) for line in control.trace_path.read_text().splitlines()]
        self.assertEqual([e["event"] for e in trace], ["request", "completion"])

    def test_stale_completion_cannot_hide_crashed_client(self):
        control = self.control(alive=lambda: False)
        (control.directory / "response.json").write_text(json.dumps({"run_id": "old", "sequence": 1, "status": "completed"}))
        with self.assertRaises(CheckFailed):
            control.call("snapshot", timeout=0.2)

    def test_wrong_request_identity_is_infrastructure_error(self):
        control = self.control()
        self.responder(control, lambda value: {**value, "request_id": "wrong"})
        with self.assertRaises(InfrastructureError):
            control.call("snapshot", timeout=2)

    def test_malformed_response_is_infrastructure_error(self):
        control = self.control()
        (control.directory / "response.json").write_text("[]")
        with self.assertRaises(InfrastructureError):
            control.call("snapshot", timeout=0.2)

    def test_deadline_and_action_failure_are_distinct(self):
        control = self.control()
        with self.assertRaises(InfrastructureError):
            control.call("snapshot", timeout=0.08)
        # A fresh channel makes this assertion independent of the deadline's sequence.
        control = Control(self.root / "failed")
        self.responder(control, lambda value: {**value, "status": "failed", "detail": "fixture mismatch"})
        with self.assertRaises(CheckFailed):
            control.call("place", timeout=2)
        trace = [json.loads(line) for line in control.trace_path.read_text().splitlines()]
        self.assertEqual(trace[-1]["event"], "failure")
        self.assertEqual(trace[-1]["detail"], "fixture mismatch")

    def test_report_zero_skipped_cleanup_and_auth_fail_closed(self):
        self.assertEqual(Report(self.root / "zero", "x", []).finish(), 2)
        self.assertEqual(Report(self.root / "skip", "x", ["not_run"]).finish(), 2)
        report = Report(self.root / "cleanup", "x", ["core"])
        report.step("core", lambda: None)
        self.assertEqual(report.finish(["cleanup password=secret"]), 2)
        self.assertNotIn("secret", (report.out / "report.json").read_text())
        self.assertEqual(ET.parse(report.out / "junit.xml").getroot().get("errors"), "1")
        report = Report(self.root / "auth", "x", ["prepare"])
        with self.assertRaises(AuthRequired):
            report.step("prepare", lambda: (_ for _ in ()).throw(AuthRequired("sign in")))
        self.assertEqual(report.finish(), 2)
        self.assertEqual(report.data["tests"][0]["kind"], "AUTH_REQUIRED")

    def test_redaction(self):
        clean = sanitize('accessToken="opaque" refresh_token=other authorization:Bearer eyJabc.def.ghi password=secret Authorization: Bearer opaqueSession')
        for value in ("opaque", "other", "eyJabc.def.ghi", "secret", "opaqueSession"):
            self.assertNotIn(value, clean)

    def test_runtime_links_cannot_export_or_copy_private_data(self):
        secret = self.root / "account.json"
        secret.write_text('{"private": "not exported"}')
        game = self.root / "game"
        game.mkdir()
        link = game / "events.jsonl"
        try:
            link.symlink_to(secret)
        except OSError:
            self.skipTest("Host does not permit test symlinks")
        with self.assertRaises(InfrastructureError):
            read_regular(link, 1000)
        with self.assertRaises(InfrastructureError):
            safe_tree(game)
        control = self.control()
        (control.directory / "request.tmp").symlink_to(secret)
        self.responder(control, lambda value: value)
        control.call("snapshot", timeout=2)
        self.assertEqual(secret.read_text(), '{"private": "not exported"}')
        self.assertFalse(control.trace_path.is_relative_to(control.directory))

    def test_prism_server_missing_and_eula_missing_exit_two(self):
        helper = test_mvt_inputs.InputTests()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        archive = helper.archive()
        with patch("mvt.client.docker") as docker:
            self.assertEqual(main(["run", "--prism-zip", str(archive), "--suite", "client-server-smoke", "--accept-eula",
                                   "--output", str(self.root / "missing")]), 2)
            self.assertEqual(main(["run", "--prism-zip", str(archive), "--suite", "client-smoke",
                                   "--output", str(self.root / "eula")]), 2)
        docker.assert_not_called()

    def test_inspection_is_core_only_and_adapter_unavailable_is_incomplete(self):
        pack_root = self.root / "game"
        pack_root.mkdir()
        pack = Pack("1.21.1", "21.1.248", {"kind": "test"}, {"client": pack_root}, {"client": {}})
        with patch("mvt.__main__.resolve", return_value=(pack, ("client",))):
            self.assertEqual(main(["inspect", "--pack-sha", "a"*40, "--suite", "client-smoke", "--adapters", "create",
                                   "--output", str(self.root / "inspect")]), 2)
        report = json.loads((self.root / "inspect" / "report.json").read_text())
        self.assertEqual([t["status"] for t in report["tests"]], ["passed", "passed", "error"])
        self.assertFalse(report["release_gate_approved"])
        self.assertEqual(report["calibration"], "NOT_PERFORMED")

    def test_no_worker_returns_incomplete_and_cleanup_runs(self):
        pack_root = self.root / "game"
        pack_root.mkdir()
        pack = Pack("1.21.1", "21.1.248", {"kind": "test"}, {"client": pack_root}, {"client": {}})
        with patch("mvt.__main__.resolve", return_value=(pack, ("client",))), \
             patch("mvt.client.platform.system", return_value="Windows"), patch.object(Client, "cleanup", return_value=[]) as cleanup:
            self.assertEqual(main(["run", "--pack-sha", "a"*40, "--suite", "client-smoke", "--accept-eula",
                                   "--output", str(self.root / "unsupported")]), 2)
        cleanup.assert_called_once()
        report = json.loads((self.root / "unsupported" / "report.json").read_text())
        self.assertEqual(report["authentication"], "NOT_TESTED")
        self.assertEqual(report["tests"][2]["status"], "error")

    def test_development_private_network_and_authentication_evidence(self):
        options = parser().parse_args(["run", "--pack-sha", "a"*40, "--suite", "client-smoke", "--client-image", "test-image"])
        game = self.root / "game"
        game.mkdir()
        pack = Pack("1.21.1", "21.1.248", {}, {"client": game}, {"client": {}})
        calls = []
        def docker(*args, **kwargs):
            calls.append(args)
            if args[:2] == ("image", "inspect"):
                return json.dumps([{"Os": "linux", "Id": "sha256:pinned", "Config": {"Labels": {
                    "org.cha0s.mvt.protocol": "1", "org.cha0s.mvt.neoforge": "21.1.248"}}}])
            if args[0] == "run":
                return json.dumps({"neoforge": "21.1.248", "automation_sha256": "a"*64})
            return ""
        client = Client(options, pack, self.root, self.root, None)
        with patch("mvt.client.platform.system", return_value="Linux"), patch("mvt.client.docker", side_effect=docker):
            evidence = client.prepare()
            client.cleanup()
        self.assertEqual(evidence["authentication"], "NOT_TESTED")
        network = next(c for c in calls if c[:2] == ("network", "create"))
        self.assertIn("--internal", network)
        self.assertTrue(any(c[:2] == ("network", "rm") for c in calls))
        self.assertFalse(any("--publish" in c or "-p" in c for c in calls))

    def test_join_error_classification(self):
        for reason, error in (("Invalid session", AuthRequired), ("Connection refused", InfrastructureError),
                              ("Mismatched mod channels", CheckFailed)):
            class Channel:
                def until(self, predicate, **kwargs):
                    predicate({"disconnect_reason": reason})
            client = SimpleNamespace(control=Channel(), options=SimpleNamespace(boot_timeout=1))
            with self.subTest(reason=reason), self.assertRaises(error):
                Session(client, [], 123).ready()

    def test_neoforge_mismatch_screen_fails_without_vanilla_disconnect_reason(self):
        class Channel:
            def until(self, predicate, **kwargs):
                predicate({"world_ready": False, "screen": "ModMismatchDisconnectedScreen"})
        client = SimpleNamespace(control=Channel(), options=SimpleNamespace(boot_timeout=900))
        with self.assertRaisesRegex(CheckFailed, "mod-channel negotiation"):
            Session(client, [], 123).ready()

    def test_connector_version_conversion_requires_loader_and_exact_source(self):
        state = {"world_ready": True, "uuid": "12345678-1234-1234-1234-123456789abc",
                 "dimension": "minecraft:overworld", "mods": {"neoforge": "21.1.248", "connector": "2.0.0-beta.17+1.21.1",
                                                            "continuity": "3.0.0_1.21.neoforge"}}
        class Channel:
            def until(self, predicate, **kwargs):
                predicate(state)
                return state
        client = SimpleNamespace(control=Channel(), options=SimpleNamespace(boot_timeout=1),
                                 pack=SimpleNamespace(neoforge="21.1.248"), expected_mods={"continuity": "3.0.0+1.21.neoforge"},
                                 expected_normalizations={"continuity": {"source_version": "3.0.0+1.21.neoforge",
                                                         "runtime_version": "3.0.0_1.21.neoforge", "loader": "connector"}})
        Session(client, [], 123).ready()
        state["mods"]["continuity"] = "3.0.1_1.21.neoforge"
        with self.assertRaises(CheckFailed):
            Session(client, [], 123).ready()
        state["mods"]["continuity"] = "3.0.0_1.21.neoforge"
        del state["mods"]["connector"]
        with self.assertRaises(CheckFailed):
            Session(client, [], 123).ready()

    def test_setup_accepts_already_creative_but_requires_proven_game_mode(self):
        for initial, change_works in ((True, True), (False, True), (False, False)):
            with self.subTest(initial=initial, change_works=change_works):
                mode = [initial]
                commands = []
                def command(value):
                    commands.append(value)
                    if value.startswith(("give ", "clear ", "gamemode ")):
                        raise CheckFailed("Player-only arguments reject raw entity UUIDs")
                    if value.endswith("if entity @s[gamemode=creative]") and not mode[0]:
                        raise CheckFailed("Player is not creative")
                    if "run gamemode creative @s" in value:
                        self.assertFalse(mode[0], "Minecraft rejects a no-op gamemode change")
                        mode[0] = change_works
                class Channel:
                    def until(self, predicate, **kwargs):
                        self_state = {"inventory": []}
                        if not predicate(self_state):
                            raise CheckFailed("Inventory not empty")
                        return self_state
                session = Session(SimpleNamespace(control=Channel()), [], 123, SimpleNamespace(check_command=command, rcon=lambda c: None))
                session.uuid = "12345678-1234-1234-1234-123456789abc"
                if initial or change_works:
                    session.setup()
                    self.assertEqual(sum("run gamemode creative @s" in c for c in commands), int(not initial))
                    self.assertTrue(any("run give @s minecraft:gold_block 1" in c for c in commands))
                else:
                    with self.assertRaises(CheckFailed):
                        session.setup()

    def test_integrated_save_requires_flush_completion(self):
        calls = []
        for result in ({}, {"success": 0}, {"success": 1}):
            def call(operation, **kwargs):
                calls.append(operation)
                return result
            session = Session(SimpleNamespace(control=SimpleNamespace(call=call)), [], 123)
            if result.get("success") == 1:
                session.save()
            else:
                with self.assertRaises(CheckFailed):
                    session.save()
        self.assertEqual(calls, ["save_world"] * 3)

    def test_persistence_checks_do_not_reseed_and_disagreement_fails(self):
        commands = []
        class Channel:
            def until(self, predicate, **kwargs):
                state = {"world_ready": True, "uuid": "12345678-1234-1234-1234-123456789abc",
                         "dimension": "minecraft:overworld", "mods": {"neoforge": "21.1.248"},
                         "block": "minecraft:gold_block", "inventory": [{"id": "minecraft:diamond", "count": 2}]}
                if not predicate(state):
                    raise CheckFailed("Client inventory disagreed with server")
                return state
        client = SimpleNamespace(control=Channel(), options=SimpleNamespace(boot_timeout=1), pack=SimpleNamespace(neoforge="21.1.248"))
        server = SimpleNamespace(check_command=commands.append)
        with self.assertRaises(CheckFailed):
            Session(client, [], 123, server).verify()
        self.assertTrue(any("#nonce" in command for command in commands))
        self.assertFalse(any(command.startswith(("setblock", "give", "item replace", "forceload", "scoreboard objectives add")) for command in commands))

    def simulated_run(self, mode="development", failure=None, create_version=None, cleanup_failure=False):
        # These fakes exercise orchestration/reporting only; they do not validate Minecraft behavior.
        events = []
        root = self.root / "game"
        root.mkdir()
        if create_version:
            (root / "mods").mkdir()
            (root / "mods" / "create.jar").write_bytes(test_mvt_inputs.modjar("create", create_version))
        pack = Pack("1.21.1", "21.1.248", {"kind": "simulated"}, {"client": root, "server": root}, {"client": {}, "server": {}})
        class FakeServer:
            def __init__(self, options, pack, adapters, out, development):
                self.nonce, self.name, self.network, self.development = 123, "server", "network", development
                self.report = {}
            def prepare(self): events.append("server_prepare")
            def boot(self, phase): events.append("boot_"+phase)
            def tick(self): events.append("server_tick")
            def isolate(self): events.append("isolate")
            def save(self): events.append("server_save")
            def stop(self): events.append("server_stop")
            def cleanup(self):
                events.append("server_cleanup")
                return []
            def redact(self, text): return text
        class FakeClient:
            def __init__(self, options, pack, work, evidence, network):
                self.options, self.pack, self.network = options, pack, network
                self.control = self
            def prepare(self, **kwargs):
                events.append("client_prepare")
                return {"mode": mode}
            def start(self): events.append("client_start")
            def quit(self): events.append("client_quit")
            def call(self, operation, **kwargs): events.append(operation)
            def until(self, predicate, **kwargs):
                return {"title_ready": True, "world_ready": True}
            def cleanup(self):
                events.append("client_cleanup")
                return ["deliberate cleanup failure"] if cleanup_failure else []
        class FakeSession:
            verified = 0
            def __init__(self, client, adapters, nonce, server): self.nonce = nonce
            def ready(self): events.append("ready")
            def setup(self): events.append("fixture")
            def interact(self): events.append("interact")
            def verify(self):
                FakeSession.verified += 1
                events.append("verify")
                if failure and FakeSession.verified == 2:
                    raise CheckFailed("Deliberate persisted-state disagreement")
        with patch("mvt.__main__.resolve", return_value=(pack, ("client", "server"))), \
             patch("mvt.__main__.Server", FakeServer), patch("mvt.__main__.Client", FakeClient), patch("mvt.__main__.Session", FakeSession):
            code = main(["run", "--pack-sha", "a"*40, "--suite", "client-server-smoke", "--client-mode", mode,
                         "--accept-eula", "--output", str(self.root / "result")])
        return code, json.loads((self.root / "result" / "report.json").read_text()), events

    def test_simulated_development_orchestration_reconnect_restart_and_cleanup(self):
        code, report, events = self.simulated_run()
        self.assertEqual(code, 0)
        self.assertEqual(events.count("fixture"), 1)
        self.assertEqual(events.count("verify"), 2)
        self.assertEqual(events.count("client_start"), 2)
        self.assertEqual(events[-3:], ["client_cleanup", "client_cleanup", "server_cleanup"])
        self.assertFalse(report["server_online_mode"])
        self.assertEqual(report["authentication"], "NOT_TESTED")
        self.assertEqual(report["calibration"], "NOT_PERFORMED")

    def test_simulated_authenticated_lane_never_disables_online_mode(self):
        code, report, events = self.simulated_run(mode="authenticated")
        self.assertEqual(code, 0)
        self.assertTrue(report["server_online_mode"])
        self.assertEqual(report["authentication"], "PASSED_ONLINE_MODE_JOIN")

    def test_simulated_persistence_failure_stops_assertions_and_always_cleans_up(self):
        code, report, events = self.simulated_run(failure=True)
        self.assertEqual(code, 1)
        self.assertEqual(next(t for t in report["tests"] if t["name"] == "verify_persistence")["status"], "failed")
        self.assertEqual(next(t for t in report["tests"] if t["name"] == "quit_saved_client")["status"], "skipped")
        self.assertEqual(events[-3:], ["client_cleanup", "client_cleanup", "server_cleanup"])

    def test_simulated_unavailable_adapter_preserves_completed_core_evidence(self):
        code, report, events = self.simulated_run(create_version="7.0.0")
        self.assertEqual(code, 2)
        self.assertTrue(all(t["status"] == "passed" for t in report["tests"][:-1]))
        self.assertEqual(report["tests"][-1]["status"], "error")
        self.assertEqual(report["adapters"]["unavailable"][0]["id"], "create")

    def test_simulated_cleanup_failure_cannot_return_success(self):
        code, report, events = self.simulated_run(cleanup_failure=True)
        self.assertEqual(code, 2)
        self.assertEqual(report["status"], "incomplete")
        self.assertTrue(report["cleanup_errors"])


if __name__ == "__main__":
    unittest.main()
