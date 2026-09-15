# Ch4oS Modpack Verification and Testing

**MVT = Modpack Verification and Testing.** This repository provides repeatable, non-visual verification for the [Create: Ch4oS Packwiz modpack](https://github.com/Cha0sCollective/Create-Ch4os-Packwiz). It is deliberately separate from the modpack repository and never targets the production AMP server.

## Current lifecycle suite

The first active suite runs the real NeoForge dedicated-server pack in a disposable Docker volume. Given a full source commit SHA, it:

- verifies the committed Packwiz `pack.toml` → `index.toml` hash link;
- reads and pins the candidate's Minecraft and NeoForge versions;
- records the resolved server image identity and runtime evidence;
- boots a fresh server and proves the game clock advances;
- creates a per-run scoreboard nonce plus representative Create block/item state;
- runs `save-all flush` and requires a clean server-requested shutdown;
- restarts the same world without recreating the fixture;
- verifies the nonce, Create block and container contents survived;
- proves the restored world keeps ticking, saves again and shuts down cleanly;
- writes JSON, JUnit, command traces, phase logs and container-state evidence.

A successful lifecycle run is intentionally reported internally as `lifecycle_passed_logs_unreviewed`. It is **not** release approval. Log baselining, installation caching, upgrade-world fixtures, GameTests, recipe/loot semantics and real-client testing remain later MVT lanes.

See [the testing plan](docs/TESTING-PLAN.md) for the architecture and coverage roadmap.

## Agent-facing interface

Agents should treat MVT as a test appliance, not as code to regenerate for each task. The stable local interface is:

```text
python mvt.py check <full-pack-commit-sha> --accept-eula --output <new-results-directory>
```

`mvt.py` is a thin wrapper around the existing `smoke.py` lifecycle harness. It does not reimplement the tests. It preserves the harness exit code and returns a compact JSON result such as:

```json
{"status":"pass","suite":"lifecycle","pack_sha":"...","minecraft":"1.21.1","neoforge":"21.1.248","checks_passed":10,"checks_total":10,"evidence":"..."}
```

Use `--format text` for a human-readable one-line result. On failure, the wrapper reports `failed_step` and `detail` so an agent can inspect focused evidence instead of loading the whole lifecycle implementation or complete server logs into context.

See [Agent use of Ch4oS MVT](docs/AGENT-USAGE.md) for the canonical modpack-agent workflow and evidence triage order.

## GitHub Actions

`.github/workflows/smoke.yml` has two paths:

- Pull requests that change the harness, agent wrapper or workflow run the Python unit tests on both Ubuntu and Windows. They do not launch Minecraft and do not require EULA acceptance.
- `workflow_dispatch` runs the unit tests and, when the operator explicitly accepts the Minecraft EULA, the disposable dedicated-server lifecycle suite on Ubuntu.

For a manual lifecycle run, open **Actions → Ch4oS Modpack Verification and Testing → Run workflow** and provide:

- `pack_sha`: a full 40-character commit SHA from `Cha0sCollective/Create-Ch4os-Packwiz`;
- `accept_eula`: explicit acceptance of the Minecraft EULA for the test server;
- `server_image`: the Java 21 `itzg/minecraft-server` image. Use a reviewed digest when building comparable baselines.

The workflow has read-only repository permissions, does not publish Minecraft or RCON ports, does not mount the repository or Docker socket into the game container, and uploads bounded test evidence even when the lifecycle suite fails.

## Run locally

Requirements: Python 3.11 or newer plus Docker. On Windows, use Docker Desktop with Linux containers. The harness uses only Python's standard library and talks to Docker through the normal `docker` CLI.

Run harness-only tests without starting Minecraft.

PowerShell:

```powershell
python -m unittest discover -p 'test_*.py' -v
```

Linux/macOS shell:

```sh
python3 -m unittest discover -p 'test_*.py' -v
```

After reading and accepting the [Minecraft EULA](https://www.minecraft.net/en-us/eula), run the lifecycle suite against an immutable pack commit through the stable wrapper.

PowerShell:

```powershell
python mvt.py check 625ae3bea9775a1757b63265a392a0fcec430fd6 --accept-eula --output mvt-results-first-run
```

Linux/macOS shell:

```sh
python3 mvt.py check 625ae3bea9775a1757b63265a392a0fcec430fd6 \
  --accept-eula --output mvt-results-first-run
```

`smoke.py` remains the authoritative lifecycle implementation and can still be invoked directly for MVT development. Ordinary modpack automation should prefer `mvt.py check`.

The output directory must not already exist. The default 4 GiB Java heap and 6 GiB container limit are exploratory settings, not measured sizing guarantees for the full pack.

## Result semantics

The stable `mvt.py` result uses:

- `status=pass`: all implemented lifecycle assertions passed; logs are still unreviewed.
- `status=fail`: a pack/server assertion failed.
- `status=error`: infrastructure or another execution error prevented valid passing evidence.

The process exit code remains `0` for pass, `1` for assertion failure and `2` for infrastructure/execution error. A skipped/not-run check is never treated as a pass.

Do not weaken assertions, silently remove mods, refresh Packwiz metadata, regenerate the lifecycle harness, or bless a new baseline merely to make a candidate green. Add reproducible regressions as checked-in tests instead.
