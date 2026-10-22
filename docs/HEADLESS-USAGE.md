# Expanded headless suites

Implementation status (2026-10-21): the runner, verified installer, control mod,
local Docker Linux worker and workflows are implemented. The pinned Create Ch4oS
single-player suite passed gameplay, save/reload persistence and clean shutdown.
The matching Docker development-server/client pair passed all 24 client-server
report checks, including interactions, reconnect and restart persistence.
The [development calibration](DEVELOPMENT-CALIBRATION.md) passed twelve clean live
runs and 37 failure/compatibility probes for pinned Ch4oS and a minimal no-Create
reference on NeoForge 21.1.248 and 21.1.233. The licensed account, authenticated
production lane, CI execution and log-baseline release gate remain pending.
Historical results are in the [original live report](LOCAL-WORKER-VALIDATION.md)
and [development-server validation](DEVELOPMENT-SERVER-VALIDATION.md).

Local validation: 69 Python tests passed on Windows and Linux (including all 15
original harness tests), and Java 21 builds plus prepared launch export succeeded
against NeoForge 21.1.233 and 21.1.248. Orchestration unit tests use explicit
simulated clients/servers; live game results are reported separately.

`smoke.py`, its schema-1 report, exit codes and original workflow are unchanged.
The Ch4oS Packwiz release process continues to call that entry point at its
existing pinned MVT revision. These new commands produce schema-2 reports and
do not approve a release or change a Packwiz source.

## Scope and inputs

Require Python 3.11+, Docker Linux containers and **Minecraft 1.21.1 / an exact
NeoForge 21.1.x version**. Client suites require Linux. Other loaders and Minecraft
versions return an explicit incomplete result. The bridge's two tested build
versions and reference packs establish the scope in the calibration report;
they do not establish runtime calibration for every 21.1.x version or modpack.

Supported sources:

- `--pack-sha <40-character commit>` with optional `--pack-repo owner/repo` and
  `--pack-path pack`. Defaults are the existing Ch4oS repository and `pack` path.
  Verify the manifest/index/file hash chain; install files for each declared side
  with pack-default optional selections. Direct HTTPS downloads with SHA-1,
  SHA-256 or SHA-512 are supported. Provider-only downloads need separate
  provisioning and currently return incomplete.
- `--prism-zip instance.zip`: a self-contained Prism export with bundled game
  files, Minecraft and NeoForge components, and no executable hooks. Import
  mods/config/defaultconfigs/kubejs/scripts/resourcepacks/shaderpacks/options.
  UTF-8 `.txt`/`.md` license documents under `third-party-notices/` are retained
  byte-for-byte and hashed (maximum 1 MiB each); binaries there are rejected.
  Reject traversal, duplicate destinations, links, private runtime/user files,
  custom runtime patches, unsupported hooks and oversized archives. Saved worlds
  are excluded; each run creates its own world. Standard LWJGL 3.3.3 is supported.
- A Prism export can instead declare a recognized data-only Packwiz bootstrap
  command pointing to a full immutable raw-GitHub commit. MVT resolves that source
  itself; it never executes the bootstrap or bundled launch commands. Only the
  descriptor, instance configuration and bootstrap JAR may be bundled in this
  layout. Mutable branch URLs and bundled overrides are rejected explicitly.

A bundled client-only ZIP cannot infer a server. Supply `--server-pack-sha` with
optional `--server-pack-repo` / `--server-pack-path` for an operator-selected
counterpart, which must have the same Minecraft/NeoForge versions. Its independent
source identity is recorded. MVT does not infer mod sides or certify that the
counterpart has identical configuration; the interaction suite tests whether the
chosen pair works together.

Inspect installations and adapter coverage without launching a game:

```sh
python3 -B -m mvt inspect --prism-zip /packs/instance.zip \
  --suite client-smoke --output mvt-results-inspection
```

Inspection success is `input_inspection_passed`; it establishes no game behavior.
Downloads are cached under `.mvt-cache` by verified content hash. Each run uses
fresh temporary installations, whose game files are deleted after cleanup.

## Select tests

`--suite server-lifecycle` runs fresh boot/ticks, vanilla chest/diamond and
gold-block fixtures, save/clean shutdown, restart and persistence. The same
Create casing/alloy fixture is added when its adapter is selected.

`--suite client-smoke` launches to the title screen, creates a fixed-seed local
world, moves/places a block/opens a chest/transfers items using normal client
actions, checks the integrated server independently, saves and exits, relaunches,
and reopens the same world before verifying the nonce, block and player inventory.

`--suite client-server-smoke` joins a disposable dedicated server, exercises the
same actions through normal client networking, cross-checks client state against
RCON, reconnects, then restarts both processes and verifies persistence without
recreating fixtures. The production server receives no test mod; the development
server includes a recorded runtime bootstrap, described below. No game/RCON ports are
published; the client can address only the per-run server on its private network.

`--adapters auto` selects compatible known adapters from the required sides'
verified installed inventories. The first adapter is `create`, revision `1`,
covering Create 6.0.x casing/alloy persistence and client interactions. `--adapters
create` requests it explicitly; `--adapters none` selects vanilla core tests.
Missing explicit prerequisites or a recognized installed Create version outside
that range make coverage incomplete, while preserving completed core evidence.
Mods without adapters appear as having no focused coverage. Adapter definitions
are trusted MVT code in `mvt/adapters.py`; pack files cannot register executable
adapters. Selected client mod versions are confirmed after the world loads.

Shared JarJar dependency versions are recorded as installed candidates, rather
than rejected as duplicate top-level mods. The client snapshot confirms the
version the loader actually selected. Duplicate top-level mod IDs remain an error.
Archives declaring FML `LIBRARY` or `LANGPROVIDER` are recorded separately under
`loader_libraries`; their installed bytes are hash-audited, and they cannot
activate gameplay adapters. They are not required to appear in NeoForge's
gameplay `ModList`, and their individual service behavior has no focused coverage.
Connector placeholder archives retain their source versions in the inventory.
When their verified Fabric metadata agrees and Connector is loaded, MVT also
accepts Connector's exact `+` to `_` runtime-version conversion and records it in
`loader_normalizations`. This does not permit a different mod version or a
different Minecraft loader.

## Development and authenticated clients

`--client-mode development` is the default for the new client commands. It uses
NeoForge's prepared development run with the fixed `MVT_Dev` identity, no Prism
account, and an offline-mode disposable server. A production server needs download
access during installation, then its public Docker bridge is disconnected.
A prepared development server uses only an internal network from creation;
scenario traffic uses an internal
per-run network. It reports **`authentication: NOT_TESTED`** even after a passing
interaction suite. It tests real client initialization and gameplay protocols;
it does not test Microsoft sign-in, entitlement, Mojang session authentication or
the production launcher environment. NeoForge's development player and the
offline-mode prerequisite are described in its [development setup documentation](https://docs.neoforged.net/docs/1.21.1/gettingstarted/).
This is not a special license exemption; follow the [Minecraft EULA](https://www.minecraft.net/en-us/eula).

Mods can gate content and networking on `FMLEnvironment.production`. A development
client can therefore expose experimental registries/channels absent from a
production dedicated server, even with identical JARs and loader versions. Report
that compatibility blocker without treating it as a confirmed production pack
regression. The original Ch4oS run encountered this through Create Abyss bundled
in Create Submarine. Select a matching development server with
`--server-mode development --development-server-image <prepared-image>` to test
the pair in the same development environment.

`--server-mode production` remains the default. Offline authentication settings
do not change FML's production identity. Development server mode is allowed only
for `client-server-smoke` with `--client-mode development`; it cannot silently
replace an authenticated server or the production lifecycle suite.

The dedicated development image uses ModDevGradle's `server()` run, the candidate's
exact NeoForge version and Java 21. It is not a GameTest server and starts without
a GUI or virtual display. Its `mvt_server_bootstrap` source overlay disables the
automatic GameTest IDE trigger and publishes a bounded runtime identity; it does
not register gameplay commands, fixtures or packet handlers. Its build hash is
recorded separately from the candidate's mod hashes. MVT requires a fresh marker
on each boot with the matching run/version, `fml_production: false` and
`gametest_enabled: false`. Candidate JARs remain intact. Normal client networking
and independent RCON assertions still test every interaction and persisted state.

`--client-mode authenticated` requires an existing dedicated licensed Java account,
interactive sign-in to a dedicated Prism root, its account profile name and the
built automation JAR. It never buys an account or switches to offline mode. The
server stays online-mode; a completed join is recorded as
`PASSED_ONLINE_MODE_JOIN` after the suite completes. Initial sign-in/renewal or
another run holding the account lock yields `AUTH_REQUIRED` and exit 2. An
authenticated single-player suite still reports session authentication as untested
because it makes no online-mode server join.

The [Prism CLI](https://prismlauncher.org/wiki/getting-started/command-line-interface/)
selects the dedicated root/instance/account. MVT generates its own wrapper and
fresh instance; export hooks do not survive import. Only the game instance and
launcher libraries/assets/JAR directories enter the game container; the launcher
account store and Docker socket do not. Candidate mods share the client's current
session inside the game, which is why the account must be dedicated. Runs are
serialized by a worker-local advisory lock and CI concurrency.

Xvfb and [Mesa LLVMpipe](https://docs.mesa3d.org/drivers/llvmpipe.html) provide an
invisible display and software OpenGL. **MVT does not take screenshots, compare
pixels, check rendering or measure graphics performance.** A pack that cannot
initialize under the software renderer must be reported as unsupported/unvalidated;
do not remove its mods to obtain a pass.

## Provision and run the worker

### Local Docker worker on Windows

Docker Desktop in Linux-container mode can host the entire development worker.
From the repository root in PowerShell, after accepting the Minecraft EULA:

```powershell
./worker/local.ps1 -PackSha FULL_COMMIT_SHA -AcceptEula
```

The script resolves the exact NeoForge version from that immutable Packwiz
manifest, builds the client image if missing, builds the trusted supervisor,
and runs `client-smoke` followed by `client-server-smoke`. Use `-Suite` to select
one, `-BuildClient` after changing the automation mod or client Dockerfile, and
`-Output` for a new evidence directory. `-PackRepo` and `-PackPath` select other
supported Packwiz sources. This helper always uses the development client mode.

For matching development runtimes on both sides:

```powershell
./worker/local.ps1 -PackSha FULL_COMMIT_SHA -AcceptEula `
  -Suite client-server-smoke -ServerMode development
```

This builds the exact-version private server image if missing. Use `-BuildServer`
after changing `server-mod`, `worker/Dockerfile.server` or `worker/server_launch.py`.
`-BuildClient` rebuilds the development server too when that mode is selected.
Without `-ServerMode development`, the helper retains its production-server
launch with offline authentication for the development client.

The supervisor uses Docker's socket to create sibling game containers; only
trusted MVT code runs in it. Candidate game containers receive no Docker socket,
Windows folder, account store, or published port. A named volume holds verified
downloads and temporary installs. Docker Engine 26+ `volume-subpath` mounts expose
only the individual game/control directories, never the full worker volume.
The script records supervisor/client image IDs in `worker-run.json`, exports
reports to Windows and removes completed supervisor containers;
the harness removes its game containers, networks and temporary worlds. The
images and `mvt-worker-data` download cache remain available for the next run.

The helper defaults to an 8G client container and a 4G server heap in a 6G server
container, plus a 1G supervisor. The development JVM uses 50% of its container
limit, leaving room for native allocations and software OpenGL. The automation
applies a recorded headless profile: render distance 2, simulation distance 5,
20 FPS, VSync off and no pause on focus loss. These settings apply only to the
temporary client. Check Docker's available memory before running;
`-ClientMemory`, `-ServerMemory` and `-ServerHeap` override these limits. Do not
publish the client image, which contains Minecraft dependencies. A licensed Prism
account is not required by this development launch; authentication remains untested.

### Dedicated Linux worker

Use a dedicated Linux machine/VM with Docker, Python 3.11+, a Docker CLI, sufficient
disk and approximately 20 GiB or more available memory for a full client/server
pack. The 6G server heap/8G server and client container limits are defaults for
exploration; measure and adjust them for calibration. OOM/deadlines are incomplete
evidence, not proof of a pack regression. Keep the worker away from production
networks and private user data. Enforce network restrictions on the worker itself:
Docker networks are not a substitute for a host/VM firewall. Installation and an
authenticated run need access to official download/authentication services.

Build a **private** image for the candidate's exact loader version from the repo
root (accept the terms of the downloaded software first):

```sh
docker build -f worker/Dockerfile --build-arg NEOFORGE_VERSION=21.1.248 \
  -t mvt-client:21.1.248 .
```

The build pins Java 21 and Gradle 8.14.4, prepares NeoForge's development runtime
and exports the plugin-generated Java command without launching Minecraft. No
automatic bundled GameTests are registered in the development gameplay lane.
The image includes the clean mapped vanilla artifact needed by Sinytra Connector
in development mode. These are launcher settings; candidate mod JARs stay intact.
The disposable dedicated world uses peaceful difficulty and zero spawn protection
so normal client placement can exercise fixtures without operator privileges. No
account is used during build. The image contains private game dependencies: do
not publish it or redistribute Minecraft files. Record the resulting image ID
and use a reviewed digest when moving it between private workers. Client image
preparation is explicit; the runner does not fetch an arbitrary client image.

Example development interaction run:

```sh
python3 -B -m mvt run --pack-sha FULL_COMMIT_SHA \
  --suite client-server-smoke --client-mode development \
  --client-image mvt-client:21.1.248 --accept-eula \
  --output mvt-results-development
```

To prepare and select a dedicated development server on a native Linux worker:

```sh
docker build -f worker/Dockerfile.server \
  --build-arg CLIENT_RUNTIME_IMAGE=mvt-client:21.1.248 \
  --build-arg NEOFORGE_VERSION=21.1.248 -t mvt-server-dev:21.1.248 .
python3 -B -m mvt run --pack-sha FULL_COMMIT_SHA \
  --suite client-server-smoke --client-mode development \
  --client-image mvt-client:21.1.248 --server-mode development \
  --development-server-image mvt-server-dev:21.1.248 \
  --accept-eula --output mvt-results-development-pair
```

`CLIENT_RUNTIME_IMAGE` is a prepared local client image from the preceding build,
used to reuse private Minecraft/Gradle dependencies. Only the dedicated server
launch is executed. The image also contains the RCON utility copied from a pinned
server image; it does not launch that image's production installer. Keep both
images private. Development-mode results do not establish production compatibility.

For authenticated runs, also install a pinned Prism Launcher and Xvfb on the host.
Create a private, signed-in launcher root, set its library/assets locations inside
that root, complete Java/runtime downloads through Prism and disable any global
pre/post launch hooks. The wrapper cannot mount custom libraries/assets outside
the dedicated root. Build `client-mod` with Java 21 and Gradle 8.14.4:

```sh
cd client-mod
gradle --no-daemon -Pneo_version=21.1.248 build
cd ..
python3 -B -m mvt run --pack-sha FULL_COMMIT_SHA \
  --suite client-server-smoke --client-mode authenticated \
  --client-image mvt-client:21.1.248 \
  --launcher-dir /private/mvt-prism --account-profile MVT-Test \
  --automation-jar client-mod/build/libs/mvt-client-0.2.0.jar \
  --accept-eula --output mvt-results-authenticated
```

The overlay is confined to temporary game installs and never added to Packwiz or
published modpack files. Client containers run with the worker user's UID, no
capabilities, a read-only image and private writable game/control/tmp directories.

For CI, provision the `mvt-client` self-hosted runner label and a restricted
`mvt-headless` environment allowing trusted MVT refs. Configure environment
variables `MVT_CLIENT_IMAGE`, `MVT_SERVER_IMAGE` and, only for authenticated runs,
`MVT_PRISM_ROOT`, `MVT_PRISM_PROFILE`, `MVT_AUTOMATION_JAR`. Keep credentials in the
worker's private launcher storage. `headless-client.yml` is manual only; PR tests
run on hosted runners without account access. Do not attach this dedicated worker
to unrelated untrusted repository workflows. Provisioning the environment and
worker is an operator task; the workflow file alone does not enforce those
repository settings.

For the manual workflow's development-server selection, also provision
`MVT_DEVELOPMENT_SERVER_IMAGE`. The workflow and CLI reject combining that server
mode with an authenticated client; hosted PR checks never receive an account.

## Evidence and remaining acceptance

Output directories must be new. Schema-2 JSON/JUnit reports include immutable
source identities, installed hashes/inventories, optional choices, adapters and
coverage, image/build identities, timings and cleanup failures. Private control
messages carry run/request/sequence identities and deadlines; stale completion,
process liveness alone and zero/skipped tests cannot pass.

Exit 0 means all selected assertions completed; status is
`suite_passed_logs_unreviewed`. Exit 1 is an assertion failure; exit 2 is an
execution/authentication/prerequisite/coverage error. Logs remain unreviewed and
`release_gate_approved` remains false. `calibration: NOT_PERFORMED` deliberately
does not change merely because a run passed.

Export bounded control traces/heartbeats and container state. Development logs
are redacted; authenticated raw client/server logs and crash files are omitted
because mods can print session information. Account files, launch arguments and
complete installs are never uploaded. Private failed-run diagnostics require
operator inspection before sharing. Cleanup removes the explicitly named
disposable containers, networks and Prism instance, then deletes temporary worlds.
Cleanup failures make a passing suite incomplete. A forced process/worker kill
cannot guarantee cleanup; inspect objects labelled `ch4os.mvt=disposable` before
removing only abandoned MVT resources.

Repeat the following when expanding the tested development scope. Items 1–4
have completed for the [recorded matrix](DEVELOPMENT-CALIBRATION.md); the licensed
authenticated lane and release-gate acceptance remain separate work:

The repeatable development calibration matrix and its evidence auditor are in
[`calibration/`](../calibration/README.md). It separates expected-failure probes
from raw suite results and requires all clean repetitions and planned probes
before issuing an aggregate development certificate. Authenticated acceptance
and log review remain separate requirements.

1. Provision the Linux worker; build the exact NeoForge image and prove headless
   startup for Ch4oS and a NeoForge 1.21.1 pack without Create.
2. Run each suite three consecutive times for both reference packs; include
   NeoForge 21.1.233 and 21.1.248 or replace them with the actual supported versions
   and update the build/calibration matrix.
3. Check Packwiz and compatible Prism inputs, explicit server counterparts,
   Create auto/explicit/core-only coverage and unsupported adapter versions.
4. Deliberately break join compatibility, block placement, chest transfer,
   client/server inventory agreement, the persistence nonce and saved world state;
   confirm assertion failures and no fixture reseeding. Exercise crash, timeout,
   OOM, interrupted-run cleanup and failed authentication; confirm exit 2 for
   missing infrastructure/credentials and no token-bearing artifacts.
5. Once the account is available, validate authenticated online-mode joins,
   reconnect and restart separately from development results. Review logs and
   evidence before any proposal to integrate these reports into release gates.

GameTests remain useful server-side checks. A server GameTest process does not
substitute for a real player's client networking or authenticated session; this
development mode launches the actual NeoForge client. Two-client, upgrade-world,
MapSyncer-specific and graphical testing remain separate future work.
