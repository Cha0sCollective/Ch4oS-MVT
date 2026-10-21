# Headless development calibration

Date: 2026-10-21. Branch: `codex/headless-client-testing-plan`.

**Status: `CALIBRATED_DEVELOPMENT`.** The offline auditor accepted all twelve clean
live repetitions and **37 failure/compatibility probes** against one fixed harness
source. Prism and Packwiz installations match all 315 client-file hashes. The
[aggregate certificate](../calibration/evidence/development-20261021.json) records
the source/image identities, outcomes and SHA-256 ledger for the retained evidence.
Authentication remains `NOT_TESTED`, logs remain unreviewed and
`release_gate_approved` remains false.

## Clean reference matrix

| Reference | Minecraft / NeoForge | Client smoke | Client-server smoke |
| --- | --- | --- | --- |
| Create Ch4oS, pinned `8106370ce2a239ae1727c4ed08173d08238e766b` | 1.21.1 / 21.1.248 | 3 consecutive passes, 14 checks each | 3 consecutive passes, 24 checks each |
| Declared minimal NeoForge-only fixture, no third-party mods | 1.21.1 / 21.1.233 | 3 consecutive passes, 14 checks each | 3 consecutive passes, 24 checks each |

These twelve runs completed **228 report checks**. Each run used two real client
processes and preserved the same saved world across restart. Dedicated-server
runs additionally disconnected/reconnected, cross-checked client actions using
RCON, and restarted the server without reseeding fixtures. Both clients and both
server boots/stops must have matching runtime identities and clean exit records.
Create revision 1 was selected automatically for all Ch4oS reference runs.

Measured report-step totals, excluding builds and helper overhead, were
184–192 seconds for Ch4oS client smoke and 264–265 seconds for its client-server
suite. The minimal reference took approximately 37–38 seconds and 66 seconds,
respectively. These are local observations under the fixed profile below.

The independent reference is explicitly authored in
[`calibration/reference.json`](../calibration/reference.json). It is a minimal
NeoForge-only pack, not a stripped copy of Ch4oS or a representative third-party
pack. Its Prism archive uses the real importer. Only this known calibration
definition declares an empty server counterpart; ordinary client-only exports
still require an authoritative or explicit matching server source.

The authored minimal reference declares `earlyWindowControl = false` in its
client `config/fml.toml`. Before this setting, the last redaction case crashed
on startup in FML 4.0.42's `DisplayWindow` / `SimpleBufferBuilder`, with a
`UnionFileSystemProvider` `FileSystemNotFoundException`; the probe had not fired.
The calibration stopped and retained that failure. Disabling the optional early
loading window follows [NeoForge's documented configuration](https://neoforged.net/meta/displayerrors/).
All no-Create repetitions and probes were rerun against the new declared input.
This changes no gameplay assertions and does not modify Ch4oS or arbitrary packs.
All twelve final minimal-reference client logs confirm the early-window provider
was disabled; the actual clients still completed gameplay and persistence checks.
The [startup diagnostic](../calibration/evidence/early-window-startup-20261021.json)
preserves the original failure identity; the original reference/fault phase
directories are excluded from the final certificate.

## Failure and coverage matrix

The operator-only [calibration driver](../calibration/README.md) runs the real MVT
CLI, actual clients/servers and original assertions. Its separate oracle ledger
does not rewrite raw failed/incomplete reports as passing suites.

| Probe group | Cases | Required observation |
| --- | --- | --- |
| Occupied placement target, altered chest transfer, authoritative inventory disagreement, changed saved nonce, removed saved region | 5 per suite | Exit 1 at the intended assertion; actual state mutation independently verified |
| Client SIGKILL | 1 per suite | Exit 1 for premature game exit |
| Paused client, stale completion from another run, actual cgroup OOM, supervisor SIGTERM, held cleanup network | 5 per suite | Exit 2; no passing suite, owned resources removed |
| Synthetic token-shaped command | 1 per suite | Suite passes; marker absent and redaction present in exported evidence |
| Rejected inputs/prerequisites | 9 | Wrong Minecraft/loader, traversal, hook, missing/mismatched counterpart, absent account profile, wrong image, unsupported adapter metadata rejected |
| Ch4oS Prism plus explicit server / Create; Ch4oS core-only; actual incompatible development/production join; missing explicit Create | 4 | Correct passing assertions or precise expected failure/coverage result |

After an injected world mutation, state-observation waits are bounded to 15
seconds; paused-client operations use two seconds. Clean-run deadlines and MVT
assertions are unchanged. The network-holder probe preserves MVT's cleanup error
before removing its own holder and abandoned network. Persistence traces are
audited for fixture reseeding. No candidate mod is removed, patched or substituted
to obtain a passing result. The Prism Ch4oS client must have the same installed
file hashes as its ordinary Packwiz reference.

## Worker and identities

All game runs are serial on local Docker Desktop Linux Engine **29.7.2**, WSL2,
24 CPUs and approximately 15.2 GiB memory, with cgroup v2. Server heap **4G**,
server container **6G**, client container **8G**, trusted supervisor **1G**.
The client retains the existing 50% heap, invisible Xvfb/Mesa and low-resolution
headless profile. The dedicated server uses `--nogui` with no virtual display.
There are no screenshots, visual assertions or graphics-performance tests.

| Runtime | Local immutable image identity |
| --- | --- |
| Client 21.1.248 | `sha256:f384f6253acb2f29e97021f4a87c90a0d202b18d7ddce1a2767101f6a460507c` |
| Server 21.1.248 | `sha256:e0483c8b541c38d586d7f2be54cd0134bee0576d9ae686a5ec7eba7c7eb25283` |
| Client 21.1.233 | `sha256:de75f35e57b9994aa7aa70ff0e49607497e756dc8785183b3c860e8416ff7e30` |
| Server 21.1.233 | `sha256:202f43d869a35f5546e3db2d6aaacf9677e3fa63c5d61cd2fa124cdbef386b13` |

The calibrated harness byte fingerprint is
`ab21f5593a526d085e427034075cf90cfd1846a59d0299b56c8b7933e13cc70a`.
Candidate source hashes, installed-file inventories, automation/bootstrap hashes,
runtime flags and resources are retained in the raw reports. Game images and
dependencies remain private; no complete Minecraft distribution is committed.

## Findings and retained evidence

Calibration exposed an RCON-transcript redaction gap in the expanded server
wrapper. Its command and response artifacts now apply the same redaction as
client traces and logs, with a regression test. The legacy `smoke.py` is unchanged.
The local helper's ownership-label inspection now uses JSON, fixing Windows
PowerShell 5.1 argument handling. All **69 Python checks pass on Windows and
Linux**, including the 15 original lifecycle tests.

The full Ch4oS Prism export initially exposed an overly narrow game-file
allowlist: it rejected the pack's `third-party-notices` license file. The importer
now preserves UTF-8 `.txt` and `.md` notices up to 1 MiB, with original bytes and
hashes; executable, binary, invalid-text and oversized notices remain rejected.
The complete matrix was repeated after this harness change. No license or mod is
omitted from the candidate to obtain a pass.
The [import diagnostic](../calibration/evidence/prism-notice-import-20261021.json)
retains the original rejection and its report hash.

An earlier pre-fix attempt was intentionally interrupted and is not counted.
Another third-repetition attempt lost its supervisor image between suites when
an overlapping worker rebuild replaced its tag. That evidence is retained under
`ch4os-fixed-3-worker-interrupted`; the complete third pair was rerun serially.
Do not rebuild or retag images while a run is active.

Local raw evidence is under the ignored `mvt-results-calibration-20261021/`:
ordinary Ch4oS repetitions in `ch4os-final-1` through `ch4os-final-3`, plus the
`reference-final-v3`, `faults-final-v3`, `inputs-final-v3` and `auxiliary-final-v3` phase
directories. Each contains schema-2 JSON/JUnit, bounded traces/logs, runtime state
and separate oracle records. Inputs, caches and dependencies remain in the
private worker volume. Reproduction and audit commands are in
[`calibration/README.md`](../calibration/README.md).

The final worker audit found **zero** disposable MVT game/supervisor containers,
private scenario networks and temporary game installations. Reusable private
images, verified caches and evidence remain available.

Recheck this exact exported matrix without launching any game:

```powershell
python -B -m calibration.verify --root mvt-results-calibration-20261021 `
    --ch4os-prefix ch4os-final- --reference reference-final-v3 `
    --faults faults-final-v3 --inputs inputs-final-v3 `
    --auxiliary auxiliary-final-v3 `
    --output mvt-results-calibration-20261021/rechecked-certificate.json
```

## Scope

This matrix covers the tested development pair, pinned Ch4oS candidate and
minimal reference on these two exact NeoForge versions and resources. Other
1.21.1/21.1.x packs and versions require their own runtime validation. Basic
Create casing/alloy behavior is the focused adapter coverage; other mod contracts,
two-client behavior, upgrade worlds and MapSyncer-specific checks remain later
work. The licensed authenticated lane, production compatibility, hosted workflow
execution, log baselines and release-gate adoption are separate acceptance work.
Individual schema-2 reports keep `calibration: NOT_PERFORMED`; the separate
audited certificate establishes `CALIBRATED_DEVELOPMENT` for this exact matrix.
The existing Packwiz release entry point and original workflow remain unchanged.
