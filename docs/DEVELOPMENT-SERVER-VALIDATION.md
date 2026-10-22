# Matching development-server validation

Date: 2026-10-21. Branch: `codex/headless-client-testing-plan`.

Historical single-run record. The subsequent [development calibration](DEVELOPMENT-CALIBRATION.md)
completed the repeated reference matrix and deliberate-failure probes. The
source fingerprints and counts below describe this earlier run.

**The Docker client-server suite passed all 24 report checks.** Matching NeoForge
development launches resolved the Create Abyss channel mismatch observed in the
[original development-client/production-server run](LOCAL-WORKER-VALIDATION.md).
This validates the tested development pair; production compatibility and licensed
authentication remain separate acceptance work.

## Candidate and runtime identities

| Component | Tested identity |
| --- | --- |
| Packwiz source | `Cha0sCollective/Create-Ch4os-Packwiz`, `pack/` |
| Immutable pack commit | `8106370ce2a239ae1727c4ed08173d08238e766b` |
| Minecraft / NeoForge / Create | `1.21.1` / `21.1.248` / `6.0.10` |
| Worker | Local Docker Desktop Linux Engine `29.7.2`, WSL2, 24 CPUs, approximately 15.2 GiB memory |
| Client image | `sha256:f384f6253acb2f29e97021f4a87c90a0d202b18d7ddce1a2767101f6a460507c` |
| Development-server image | `sha256:e0483c8b541c38d586d7f2be54cd0134bee0576d9ae686a5ec7eba7c7eb25283` |
| Supervisor image | `sha256:5e42891c02f84ca91e4868bd2db4079e1d21343c3447fcf7e57a88c9f0418a70` |
| Server JVM | Temurin `21.0.11+10` |
| Server bootstrap JAR SHA-256 | `12f46d25ead18fcceca4cf84bdcdfada4202ac19bcfe57a0882526d1d81e18c1` |
| Client automation JAR SHA-256 | `10149074069e3d57f6e09fe6388e2451cab274e52c5b76058ca9193b770b6d44` |
| Harness SHA-256 from the running worker | `a1a1cf9142281a08625b56b0ab6a7a1821d935dcaecf76eca8d7757dff2daf2a` |

The pinned candidate and adapter selection match the original failed join.
Create adapter revision 1 was selected automatically. Candidate JAR hashes were
verified on installation and after server boots/client relaunch. No candidate mod
was removed, patched, replaced or made network-optional to obtain this pass.

## Live results

The run returned exit **0**, status `suite_passed_logs_unreviewed`, with no cleanup
errors. Measured report steps totaled approximately **286 seconds (4.8 minutes)**,
excluding image construction. The full sequence passed:

- Input verification, installed inventories, image/bootstrap validation.
- Dedicated development-server boot and advancing game clock; internal network
  verification; real client startup and successful mod negotiation/join.
- Vanilla gold-block placement and diamond transfer, plus Create andesite-casing
  placement and alloy transfer. Movement, chest access and inventory changes
  traveled through normal client networking and were cross-checked using RCON.
- Disconnect/reconnect and verification of existing fixtures and inventory.
- Client shutdown, server save/flush and server-requested clean stop.
- Restart of the same server world and a fresh client process, successful join,
  and persistence checks for the scoreboard nonce, placed blocks, emptied chests
  and player inventory without reseeding.
- Final client shutdown, server save/flush/stop, adapter coverage and cleanup.

Both server boots published a fresh matching runtime identity with
`fml_production: false`, `gametest_enabled: false`, Minecraft `1.21.1` and NeoForge
`21.1.248`. MVT removes the previous marker before restart, so a stale marker
cannot satisfy the second boot. Both client exits and both server stops recorded
exit code **0**, `Running: false` and `OOMKilled: false`.

The server used a 4G Java heap within a 6G container, the client an 8G container
with its established 50% heap/headless profile, and the supervisor a 1G container.
These are the measured settings for this run, not sizing guarantees for all packs.

## Evidence and repeat command

Local ignored evidence:

- `mvt-results-dev-server-ch4os-20261021-01/worker-run.json`
- `mvt-results-dev-server-ch4os-20261021-01/client-server-smoke/report.json`
- `mvt-results-dev-server-ch4os-20261021-01/client-server-smoke/junit.xml`
- The same suite directory contains bounded control traces, heartbeats, client
  logs/state and `server/` RCON traces, phase logs and state files.

Repeat from the repository root in PowerShell:

```powershell
./worker/local.ps1 -PackSha 8106370ce2a239ae1727c4ed08173d08238e766b -AcceptEula `
  -Suite client-server-smoke -ServerMode development
```

The helper builds the exact-version server image when absent. Use `-BuildServer`
after changing the bootstrap/server launcher. Each output directory must be new;
the default generates a fresh one. Images and the verified named-volume cache
remain reusable. All run game containers, supervisor containers, private networks
and temporary installs are removed on normal completion.

## Implementation and limits

The server uses a prepared ModDevGradle `server()` launch. A server-only source
overlay disables the automatic GameTest IDE trigger and reports runtime identity;
it does not run fixtures, alter gameplay state, add commands or register packets.
The overlay's identity is explicit above. FML's production flag is not spoofed.
The original production-server entry point and default mode remain available.

All **60 Python regression tests passed on Windows and Linux**, including the
15 original lifecycle tests and new checks for wrong images/versions, invalid
runtime flags, stale restart markers, network isolation, ambiguous-create cleanup,
offline launcher settings and refusal to combine authenticated clients with a
development server. The server bootstrap and prepared launch built against
NeoForge **21.1.233** locally and **21.1.248** in Docker; only 21.1.248 has this
live interaction result. Workflow changes have not been executed in GitHub Actions.

`authentication` remains **`NOT_TESTED`**, `server_online_mode` is false,
`calibration` remains **`NOT_PERFORMED`**, and `release_gate_approved` is false.
Full repeated/two-pack calibration and failure injection are still required.
No screenshots, visual assertions or graphics-performance tests were performed.
Focused Create coverage is basic casing/alloy behavior; other mod-specific
contracts remain outside this adapter. Production-server/client validation still
requires the separate authenticated lane.
