"""Opt-in expanded suites; smoke.py remains the release-facing compatibility entry point."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import secrets
import sys
import tempfile
import uuid
import zipfile
import smoke

from smoke import REPO, validate_sha
from .adapters import inventory, select
from .client import Client
from .errors import CheckFailed, InfrastructureError, Unsupported
from .inputs import Cache, packwiz, prism
from .report import Report, sanitize
from .scenarios import Session
from .server import Server

SUITES = ("server-lifecycle", "client-smoke", "client-server-smoke")
SCENARIOS = {
    "server-lifecycle": ["boot_fresh", "tick_fresh", "write_fixture", "save_fresh", "stop_fresh",
                         "boot_saved", "verify_persistence", "save_saved", "stop_saved"],
    "client-smoke": ["start_client", "create_world", "prepare_fixture", "client_interactions", "save_world",
                     "quit_client", "restart_client", "load_world", "verify_persistence", "quit_saved_client"],
    "client-server-smoke": ["boot_server", "tick_server", "isolate_network", "start_client", "join_server",
                            "prepare_fixture", "client_interactions", "disconnect", "reconnect",
                            "verify_reconnect", "quit_client", "save_server", "stop_server", "restart_server",
                            "restart_client", "join_saved_server", "verify_persistence", "quit_saved_client",
                            "save_saved_server", "stop_saved_server"],
}


def memory(value):
    if not re.fullmatch(r"[1-9]\d*[MGmg]", value):
        raise argparse.ArgumentTypeError("Use a positive memory size such as 6G or 8192M")
    return value.upper()


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    for command in ("inspect", "run"):
        p = commands.add_parser(command)
        source = p.add_mutually_exclusive_group(required=True)
        source.add_argument("--pack-sha", type=validate_sha)
        source.add_argument("--prism-zip", type=Path)
        p.add_argument("--pack-repo", default=REPO)
        p.add_argument("--pack-path", default="pack")
        p.add_argument("--server-pack-sha", type=validate_sha, help="Explicit counterpart for a client-only Prism ZIP")
        p.add_argument("--server-pack-repo", default=REPO)
        p.add_argument("--server-pack-path", default="pack")
        p.add_argument("--adapters", default="auto", help="auto, none, or comma-separated adapter IDs")
        p.add_argument("--cache", type=Path, default=Path(".mvt-cache"))
        p.add_argument("--output", type=Path, default=Path("mvt-results-" + uuid.uuid4().hex[:12]))
        p.add_argument("--suite", choices=SUITES, default="server-lifecycle")
        if command == "run":
            p.add_argument("--accept-eula", action="store_true")
            p.add_argument("--image", default="itzg/minecraft-server:java21")
            p.add_argument("--heap", type=memory, default="6G")
            p.add_argument("--container-memory", type=memory, default="8G")
            p.add_argument("--client-memory", type=memory, default="8G")
            p.add_argument("--boot-timeout", type=int, default=900)
            p.add_argument("--client-mode", choices=("development", "authenticated"), default="development")
            p.add_argument("--client-image", help="Prepared Linux image; use a reviewed immutable digest")
            p.add_argument("--launcher-dir", type=Path, help="Dedicated private Prism root (authenticated mode)")
            p.add_argument("--account-profile", help="Dedicated Prism profile name; never a password/token")
            p.add_argument("--automation-jar", type=Path)
            p.add_argument("--prism-launcher", default="prismlauncher")
    return root


def resolve(options, work):
    cache = Cache(options.cache)
    try:
        pack = (prism(options.prism_zip, work / "pack", cache) if options.prism_zip else
                packwiz(options.pack_repo, options.pack_sha, options.pack_path, work / "pack", cache))
    except (KeyError, ValueError, UnicodeError, zipfile.BadZipFile) as exc:
        raise CheckFailed("Invalid candidate archive/manifest metadata") from exc
    except OSError as exc:
        raise InfrastructureError("Candidate input cannot be read: " + type(exc).__name__) from exc
    if options.server_pack_sha:
        if not options.prism_zip or "server" in pack.roots:
            raise Unsupported("Explicit server source is only valid for a client-only Prism export")
        counterpart = packwiz(options.server_pack_repo, options.server_pack_sha, options.server_pack_path,
                              work / "counterpart", cache, ("server",))
        if (pack.minecraft, pack.neoforge) != (counterpart.minecraft, counterpart.neoforge):
            raise Unsupported("Explicit server source must declare the same Minecraft and NeoForge versions")
        pack.roots["server"] = counterpart.roots["server"]
        pack.hashes["server"] = counterpart.hashes["server"]
        pack.source["explicit_server_source"] = counterpart.source
        pack.selections += counterpart.selections
    required = ("client",) if options.suite == "client-smoke" else ("server",) if options.suite == "server-lifecycle" else ("client", "server")
    if not all(side in pack.roots for side in required):
        raise Unsupported("Prism export has client-only coverage; supply an explicit matching server Packwiz source")
    return pack, required


def execute(options, server, first, clients, step, adapters, work, report):
    if options.suite == "server-lifecycle":
        for name, action in (("boot_fresh", lambda: server.boot("fresh")), ("tick_fresh", server.tick),
                             ("write_fixture", server.write_fixture), ("save_fresh", server.save),
                             ("stop_fresh", server.stop), ("boot_saved", lambda: server.boot("saved")),
                             ("verify_persistence", server.verify_fixture), ("save_saved", server.save),
                             ("stop_saved", server.stop)):
            step(name, action)
        return
    if server:
        step("boot_server", lambda: server.boot("fresh"))
        step("tick_server", server.tick)
        step("isolate_network", server.isolate)
    step("start_client", first.start)
    world = "mvt-" + uuid.uuid4().hex
    session = Session(first, adapters, server.nonce if server else secrets.randbelow(1_000_000_000)+1, server)
    def enter(client, operation):
        if server:
            client.control.call("join", address=server.name + ":25565")
        else:
            client.control.call(operation, name=world, timeout=options.boot_timeout)
        session.ready()
    step("join_server" if server else "create_world", lambda: enter(first, "create_world"))
    step("prepare_fixture", session.setup)
    step("client_interactions", session.interact)
    if server:
        def disconnect():
            first.control.call("disconnect", timeout=120)
            first.control.until(lambda s: s.get("title_ready"))
        step("disconnect", disconnect)
        step("reconnect", lambda: enter(first, "join"))
        step("verify_reconnect", session.verify)
    else:
        step("save_world", session.save)
    step("quit_client", first.quit)
    if server:
        step("save_server", server.save)
        step("stop_server", server.stop)
        step("restart_server", lambda: server.boot("saved"))
    second = Client(options, first.pack, work, report.out, first.network)
    clients.append(second)
    def restart():
        second.prepare(verify_installation=False)
        second.start()
    step("restart_client", restart)
    session = Session(second, adapters, session.nonce, server)
    step("join_saved_server" if server else "load_world", lambda: enter(second, "load_world"))
    step("verify_persistence", session.verify)
    step("quit_saved_client", second.quit)
    if server:
        step("save_saved_server", server.save)
        step("stop_saved_server", server.stop)


def main(argv=None):
    options = parser().parse_args(argv)
    if options.command == "run" and options.boot_timeout <= 0:
        parser().error("--boot-timeout must be positive")
    names = ["input", "inventory"]
    if options.command == "run":
        names += ["prepare"] + SCENARIOS[options.suite]
    names += ["adapter_coverage"]
    try:
        report = Report(options.output, options.suite if options.command == "run" else "input-inspection", names)
    except OSError:
        print("Output directory must be new and writable", file=sys.stderr)
        return 2
    report.data.update(authentication="NOT_TESTED", headless={"display": "Xvfb", "renderer": "llvmpipe",
                                                            "visual_assertions": False})
    report.data["requested_input"] = {"kind": "prism" if options.prism_zip else "packwiz",
                                      "sha": options.pack_sha, "repository": sanitize(options.pack_repo)}
    if options.command == "run":
        report.data["resource_limits"] = {"server_heap": options.heap, "server_container": options.container_memory,
                                         "client_container": options.client_memory, "boot_timeout_seconds": options.boot_timeout}
    sources = [Path(smoke.__file__)] + sorted(Path(__file__).parent.glob("*.py"))
    report.data["harness_sha256"] = hashlib.sha256(b"".join(p.read_bytes() for p in sources)).hexdigest()
    clients, server, cleanup = [], None, []
    with tempfile.TemporaryDirectory(prefix="mvt-") as directory:
        work = Path(directory)
        try:
            def get_input():
                if options.command == "run" and not options.accept_eula:
                    raise Unsupported("Explicit --accept-eula is required before game execution")
                return resolve(options, work)
            pack, sides = report.step("input", get_input)
            report.data["pack"] = pack.evidence()
            def get_adapters():
                libraries = {side: [] for side in sides}
                normalizations = {side: {} for side in sides}
                inventories = {side: inventory(pack.roots[side], libraries=libraries[side], normalizations=normalizations[side]) for side in sides}
                selected, coverage = select(options.adapters, inventories)
                report.data.update(installed_mods=inventories, loader_libraries=libraries,
                                   loader_normalizations=normalizations, adapters=coverage)
                return selected
            adapters = report.step("inventory", get_adapters)
            if options.command == "run":
                if options.suite != "client-smoke":
                    server = Server(options, pack, adapters, report.out / "server",
                                    development=options.suite == "client-server-smoke" and options.client_mode == "development")
                first = None
                if options.suite != "server-lifecycle":
                    first = Client(options, pack, work, report.out, server.network if server else None)
                    clients.append(first)
                def prepare():
                    if first:
                        report.data["client_runtime"] = first.prepare()
                    if server:
                        server.prepare()
                report.step("prepare", prepare)
                execute(options, server, first, clients, report.step, adapters, work, report)
                if options.client_mode == "authenticated" and options.suite == "client-server-smoke":
                    report.data["authentication"] = "PASSED_ONLINE_MODE_JOIN"
            def coverage():
                if report.data["adapters"]["unavailable"]:
                    raise Unsupported("Core evidence retained; requested/recognized adapter coverage is unavailable")
            report.step("adapter_coverage", coverage)
        except (Exception, KeyboardInterrupt) as exc:
            if not report.code:
                report.code = 1 if isinstance(exc, CheckFailed) else 2
            report.data["error"] = sanitize(str(exc)) if not isinstance(exc, KeyboardInterrupt) else "Interrupted by operator"
        finally:
            for client in reversed(clients):
                try:
                    cleanup += client.cleanup()
                except Exception as exc:
                    cleanup.append(sanitize(str(exc)))
            if server:
                try:
                    cleanup += server.cleanup()
                except Exception as exc:
                    cleanup.append(server.redact(str(exc)))
                report.data["server_runtime"] = {k: server.report[k] for k in
                    ("image_id", "image_digests", "java_runtime", "versions") if k in server.report}
                report.data["server_online_mode"] = not server.development
            code = report.finish(cleanup)
            if options.command == "inspect" and code == 0:
                report.data["status"] = "input_inspection_passed"
                report.write()
    print(json.dumps({"status": report.data["status"], "exit_code": code, "report": str(report.out / "report.json")}))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
