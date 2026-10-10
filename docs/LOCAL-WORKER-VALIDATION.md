# Local Docker development-worker validation

Date: 2026-10-10. Branch: `codex/headless-client-testing-plan`.

The live single-player suite passed. The client-server suite failed at channel
negotiation because this pack enables Create Abyss only in development, while
the dedicated server uses production mode. This is development-mode evidence;
authentication,
full calibration, CI execution and release approval remain pending.

## Candidate and worker

| Component | Tested identity |
| --- | --- |
| Packwiz repository | `Cha0sCollective/Create-Ch4os-Packwiz`, `pack/` |
| Immutable pack commit | `8106370ce2a239ae1727c4ed08173d08238e766b` |
| Pack version | Create Ch4oS New World `0.3.0` |
| Minecraft / NeoForge / Create | `1.21.1` / `21.1.248` / `6.0.10` |
| Docker host | Local Docker Desktop, Linux Engine `29.7.2`, WSL2 |
| Available engine resources | 24 logical CPUs; approximately 15.2 GiB memory |
| Client image ID | `sha256:f384f6253acb2f29e97021f4a87c90a0d202b18d7ddce1a2767101f6a460507c` |
| Final client-server supervisor image ID | `sha256:491b96180c3447143cdc35cc94ae6f2a3b690b0095c093d8e734344e1dc973e5` |
| Dedicated server image | `itzg/minecraft-server@sha256:aae986843ab9b00effb816c6404d28519671bcaa49bf234589d4a0e74c56ccb5` |

MVT verified the Packwiz manifest/index and downloaded the candidate's declared
client/server files with hash checks. Its runtime-selected Create adapter adds
andesite casing/alloy checks to the vanilla gold-block/diamond fixtures.
No candidate mod was removed, replaced or patched to obtain a pass.

## Results

| Validation | Result |
| --- | --- |
| Python regressions on Windows and Linux | 53 passed, including all 15 original harness tests |
| Java 21 build and prepared launch export, NeoForge 21.1.233 | Passed |
| Java 21 build and prepared launch export, NeoForge 21.1.248 | Passed in the client Docker build |
| Live `client-smoke` | Passed: all 14 report checks; approximately 4.8 minutes of measured steps |
| Live `client-server-smoke` | Failed at join: NeoForge mod-channel negotiation; approximately 2.3 minutes of measured steps |
| Licensed session authentication | `NOT_TESTED` |
| Graphical testing | Outside MVT; no screenshots or visual assertions |

Single-player evidence is in the local ignored
`mvt-results-local-ch4os-20261010-11/client-smoke/` directory: `report.json`,
`junit.xml`, control traces, heartbeats, bounded logs and container state.
Both clients exited with code 0 and without an OOM kill. The suite proved normal
vanilla/Create interactions, save/flush, clean shutdown, fresh-process reopening
and persisted marker/block/player inventory without reseeding. Its status is
`suite_passed_logs_unreviewed`; calibration is `NOT_PERFORMED` and
`release_gate_approved` is false.

The single-player supervisor ID was
`sha256:bfefb496fa6a99435fae10bdc9f6d047c59d84e0848f6e1949efa97d09aa38e6`.
The final supervisor adds immediate detection of NeoForge's special mismatch
screen, which is not a vanilla `DisconnectedScreen`. The helper records exact
worker/client image IDs in `worker-run.json` beside the exported suite directories.

## Development/production compatibility blocker

The final interaction report and bounded logs are in
`mvt-results-local-ch4os-20261010-13/client-server-smoke/`. The server boot/tick,
network isolation and client startup passed. The join failed promptly with exit
1; later interactions, reconnect and restart checks were skipped. Cleanup
completed without reported errors. The first diagnostic run was interrupted
after the same rejection, before mismatch-screen detection was corrected; it
remains separate incomplete evidence in output `-12`.

NeoForge identified `create_abyss:struggle_shark` as required by the client but
missing on the server. Both installs contain exactly the same
`mods/create_submarine-3.3.0.jar`, SHA-256
`99709e004c8a7a36cb3439c0200ffd0c8ff5d7ca4fb77d49f76395d803864ba1`, which
also declares Create Abyss `1.0.0`. Both loader versions are `21.1.248`.

Offline bytecode inspection of that verified artifact confirmed
`com.maxenonyme.AbyssDimension.CreateAbyss.enabled()` returns
`!FMLEnvironment.production`. Its constructor returns before content/network
registration in production; development registers `StruggleSharkPayload` through
`playToServer`. This explains the runtime mismatch. **It does not demonstrate
that two ordinary production clients/servers are incompatible.** The passing
single-player result also covers this pack's development-enabled content, not
identical production behavior.

For this candidate, interaction validation needs either a licensed production
client or an explicitly designed matching development-server lane. The latter
would test development behavior and is not implemented here. No mod was removed,
no channel requirement was bypassed, and no production flag was spoofed to pass.

## Runtime corrections

The first live attempts exposed development-worker issues, retained as failed
reports in the local ignored results directories:

- Connector needs the clean mapped vanilla artifact in a development launch.
  The image now provides it through `connector.clean.path`.
- NeoForge's development launch automatically enables bundled GameTests through
  both a property and the vanilla IDE flag. This gameplay lane disables both,
  avoiding unrelated optional mod test-class registration.
- The original client heap/native split exceeded the 8G container during world
  creation. The development heap now uses 50%; the temporary headless profile
  uses render distance 2, simulation distance 5 and 20 FPS. This has loaded the
  world without that memory kill. It is not graphics-performance coverage.
- Configured Defaults declares `FMLModType: LIBRARY`; it is hash-audited and
  reported as a loader library, rather than required in gameplay `ModList`.
  Continuity's verified Connector placeholder version is accepted only with
  Connector's exact `+` to `_` normalization; source/runtime versions are recorded.
- Player-only commands now use the UUID-selected player's `@s`, and creative
  mode is verified before attempting a change. Integrated saves use the same
  `saveEverything(true, true, true)` flush API as the dedicated `save-all flush`
  command, because integrated servers do not register that command.
- NeoForge's special mismatch screen now produces an immediate failed join,
  rather than waiting for the generic world-readiness deadline.

## Repeat locally

From the repository root in PowerShell:

```powershell
./worker/local.ps1 -PackSha 8106370ce2a239ae1727c4ed08173d08238e766b -AcceptEula `
  -ServerImage 'itzg/minecraft-server@sha256:aae986843ab9b00effb816c6404d28519671bcaa49bf234589d4a0e74c56ccb5'
```

This runs both suites. Use `-Suite client-smoke` or `-Suite client-server-smoke`
for one suite; use `-BuildClient` after changing client code or its Dockerfile.
Output directories must be new. Reports export to Windows; the named
`mvt-worker-data` volume retains verified downloads and reports. The trusted
supervisor receives the Docker socket, while game containers receive only their
own game/control subdirectories and no published ports. No Docker/WSL global
resource settings were changed.

The original `smoke.py`, its tests and release workflow are unchanged. Full
three-run/two-pack calibration, failure injection and licensed online-mode joins
still follow the [acceptance checklist](HEADLESS-USAGE.md#evidence-and-remaining-acceptance).
