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

A successful lifecycle run is intentionally reported as `lifecycle_passed_logs_unreviewed`. It is **not** release approval. Log baselining, installation caching, upgrade-world fixtures, GameTests, recipe/loot semantics and real-client testing remain later MVT lanes.

See [the testing plan](docs/TESTING-PLAN.md) for the architecture and coverage roadmap.

## GitHub Actions

`.github/workflows/smoke.yml` has two paths:

- Pull requests that change the harness or workflow run the Python unit tests on both Ubuntu and Windows. They do not launch Minecraft and do not require EULA acceptance.
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

After reading and accepting the [Minecraft EULA](https://www.minecraft.net/en-us/eula), run the lifecycle suite against an immutable pack commit.

PowerShell:

```powershell
python smoke.py --accept-eula --pack-sha 625ae3bea9775a1757b63265a392a0fcec430fd6 --output mvt-results-first-run
```

Linux/macOS shell:

```sh
python3 smoke.py --accept-eula \
  --pack-sha 625ae3bea9775a1757b63265a392a0fcec430fd6 \
  --output mvt-results-first-run
```

The output directory must not already exist. The default 4 GiB Java heap and 6 GiB container limit are exploratory settings, not measured sizing guarantees for the full pack.

## Result semantics

- Exit `0`: all implemented lifecycle assertions passed; logs are still unreviewed.
- Exit `1`: a pack/server assertion failed.
- Exit `2`: infrastructure or another execution error prevented valid passing evidence.
- A skipped/not-run check is never treated as a pass.

Do not weaken assertions, silently remove mods, refresh Packwiz metadata, or bless a new baseline merely to make a candidate green. Add reproducible regressions as checked-in tests instead.
