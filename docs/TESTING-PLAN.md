# Ch4oS Minimum Viable Testing

Research and proposal: 2026-09-15. Scope: repeatable, non-visual validation of the actual Packwiz modpack. This is a testing project, not a replacement launcher, Packwiz installer, AMP manager, or production server.

## Decision

Use a normal NeoForge dedicated server, provisioned using the existing Packwiz server installation path, controlled by a small deterministic CLI over RCON. Run it locally or in GitHub Actions with the same harness. Add a test-only NeoForge GameTest module for assertions that console commands cannot adequately express. Add a real headless client only when connection/client coverage is valuable.

Do not start with desktop/computer-use automation. Do not let each agent invent an installer, shell script, interpretation of log severity, or test world. Give agents named suites, a command to run them, structured evidence, and a rule that new regression cases become checked-in tests.

**Reuse immutable installation artifacts and test definitions. Do not reuse uncontrolled mutable server state.** A fresh disposable world/process is useful isolation; repeatedly downloading and rediscovering the setup is the waste.

## Inspected repository state

The source repository was read only. At inspection, `main` and `public` pointed at `625ae3bea9775a1757b63265a392a0fcec430fd6`; `beta` pointed at `a1abcbd07107766ba6db2d92e590eb7356da4cf6`.

The accepted pack is version 0.2.0, Minecraft 1.21.1, NeoForge 21.1.248. Server documentation specifies Java 21 and Packwiz bootstrap `-g -s server`; AMP owns production lifecycle management. The Create descriptor selects Create 6.0.10. The pack README identifies Tectonic, Terralith and Distant Horizons, making normal-world generation and save compatibility relevant in addition to a flat laboratory fixture.

Beta adds MapSyncer and `defaultconfigs/mapsyncer-server.toml`. Its map documentation distinguishes new-world default installation from explicitly handling an existing world's server config; it also says map merging/reconnect behavior awaits in-game testing. Test those as distinct cases, rather than claiming that a server boot validates map synchronization.

Sources: [pack manifest](https://github.com/Cha0sCollective/Create-Ch4os-Packwiz/blob/625ae3bea9775a1757b63265a392a0fcec430fd6/pack/pack.toml), [server installation](https://github.com/Cha0sCollective/Create-Ch4os-Packwiz/blob/625ae3bea9775a1757b63265a392a0fcec430fd6/docs/SERVER.md), [Create descriptor](https://github.com/Cha0sCollective/Create-Ch4os-Packwiz/blob/625ae3bea9775a1757b63265a392a0fcec430fd6/pack/mods/create.pw.toml), [README](https://github.com/Cha0sCollective/Create-Ch4os-Packwiz/blob/625ae3bea9775a1757b63265a392a0fcec430fd6/README.md), [beta map documentation](https://github.com/Cha0sCollective/Create-Ch4os-Packwiz/blob/a1abcbd07107766ba6db2d92e590eb7356da4cf6/docs/MAPS.md).

## Available tools and fit

| Option | Fit and decision |
| --- | --- |
| itzg/minecraft-server + Packwiz + RCON | First choice for ordinary dedicated-server smoke tests. Supports `TYPE=NEOFORGE`, exact versions, `PACKWIZ_URL`, server-side filtering, health checks and bundled `rcon-cli`. It provisions/controls the server; it does not supply pack-specific assertions or a log regression oracle. |
| NeoForge 1.21.1 GameTests | First choice for deterministic in-game behavior assertions. Use a separate test module and small structures for machine behavior, recipes and serialization. The documented Gradle game-test server has an exit status suitable for CI, but requires a mod-development project; the Packwiz repository is not one. |
| HeadlessMC + MC-Runtime-Test | Later client-side smoke lane. The project documents NeoForge/1.21 support and client launch/singleplayer smoke via HeadlessMC/Xvfb. Full-pack multiplayer joining needs additional automation; advertised loader support is not proof this pack runs. |
| Mineflayer | Good bot API to evaluate for player-like behavior, not the default here. The linked Forge protocol plugin documents the older `FML|HS` handshake. That does not establish modern NeoForge/custom-payload compatibility for this pack. Require a connection proof before investing. |
| PackTest | Attractive data-pack-authored tests, but the project is a Fabric mod, not a drop-in NeoForge 1.21.1 solution. Do not switch loaders or add a compatibility layer just to obtain its test DSL. |
| RCON MCP adapters | Existing adapters can expose a running Docker server to agents. They are control interfaces, not test frameworks: they do not automatically provide fixtures, assertions, evidence keys, or baseline approval. Wrap the stable harness rather than expose production RCON. |
| KubeJS / MockBukkit | KubeJS is a scripting tool that could host assertions if already justified by the pack. Do not add it solely to avoid a small harness. MockBukkit targets Bukkit/Paper plugin mocks, not NeoForge pack integration. |

Primary tool references: [Docker Packwiz](https://docker-minecraft-server.readthedocs.io/en/latest/mods-and-plugins/packwiz/), [NeoForge configuration](https://docker-minecraft-server.readthedocs.io/en/latest/types-and-platforms/server-types/forge/#neoforge), [RCON commands](https://docker-minecraft-server.readthedocs.io/en/latest/sending-commands/commands/), [Packwiz installer](https://packwiz.infra.link/tutorials/installing/packwiz-installer/), [NeoForge 1.21.1 GameTests](https://docs.neoforged.net/docs/1.21.1/misc/gametest/), [MC-Runtime-Test](https://github.com/headlesshq/mc-runtime-test), [Mineflayer](https://github.com/PrismarineJS/mineflayer), [Forge protocol plugin](https://github.com/PrismarineJS/node-minecraft-protocol-forge), [PackTest](https://github.com/misode/packtest), [RCON MCP](https://github.com/rgbkrk/rcon-mcp), [KubeJS](https://kubejs.com/), [MockBukkit](https://github.com/MockBukkit/MockBukkit).

## Execution architecture

A trusted MVT runner takes an immutable candidate commit, suite revision and fixture revision. It reads the pack; it does not execute source-repository workflow scripts or modify Packwiz metadata to make checks pass.

1. Validate metadata and materialize the exact server-side pack. Check the index hash before installing. Record selected optional mods and preserved-config choices. Reject unexpected installed JARs, missing required JARs, invalid hashes, missing dependencies and client-only contamination. Never silently remove a problematic mod to get a green result.
2. Prepare a disposable instance from a verified immutable installation bundle, then copy a fixture or create a normal seeded world. Keep all generated config, world and operator data in the run directory/volume.
3. Launch the real NeoForge dedicated server without test-only mods for the initial smoke lane. Wait for readiness using fresh-process output plus a successful main-thread query and advancing game ticks. A listening socket or a historical `Done` line is insufficient.
4. Execute named scenarios. Record commands, responses, expectations, timings and failed prerequisites. Ensure unexpected command syntax/output fails closed. RCON transport success does not imply Minecraft command success.
5. Save, request a graceful stop, wait for process termination, inspect exit/OOM state, restart the same test world and read back persisted state before initializing anything. Cleanup after failure does not count as a successful shutdown assertion.
6. Compare phase-specific logs and semantic snapshots. Publish JSON, JUnit, a human summary and redacted raw logs regardless of success. Preserve useful failure state under a bounded retention policy. Never upload credentials or a production world.

Initially keep this as a CLI, not a service. A proposed future interface is `mvt run --candidate <sha> --suite smoke --baseline <id>`. This interface is a design, not an already implemented command. Agents with only remote GitHub access can dispatch an MVT workflow with the same SHA and retrieve its artifact. An optional MCP wrapper can later offer `run_suite`, `get_result`, `get_failure_logs` and `describe_suites`.

## Coverage roadmap

| Lane | Checks | What passing does not prove |
| --- | --- | --- |
| Static | Packwiz hash chain, metadata syntax, download identity, server-side selection, optional-mod policy, expected installed JAR inventory, config schema where available | Minecraft actually launches or behaves correctly |
| Normal-server smoke | Boot, main-thread responsiveness, ticking, representative block/item IDs, idle settling, save/stop/restart, persisted nonce/block/container contents | Recipes, natural loot, client joining, all chunks/dimensions |
| World/config compatibility | Fresh normal seeded world, selected new chunks/dimensions, old-world clone loaded by candidate, effective new/existing-world config | Arbitrary old saves or every world-generation location |
| Focused behavioral tests | Loaded recipes/tags/loot tables, selected Create processing, item transfer, block-entity save/reload, one critical interaction per integration | Every possible factory or interaction |
| Optional real client | Matching modpack client launch, join, disconnect/reconnect, player inventory persistence and selected handshakes | Rendering correctness or every UI path |

### First lifecycle scenarios

Create a random per-run scoreboard nonce, place a known mod block and put known mod items into a chest in a bounded force-loaded area. Read them immediately. Run `save-all flush`; require success. Send `stop`; require the server to exit on its own within a timeout without a forced kill. Restart that same world and assert the original nonce, block and inventory before writing the fixture again. Add a second save/shutdown cycle.

This avoids three weak tests: searching only for a save message, checking only that `level.dat` exists, and recreating the marker before reading it after restart. A fresh random marker also prevents a stale fixture from making persistence appear successful.

Item existence, item creation and item acquisition are different assertions. A command-created `create:andesite_alloy` proves little about its recipe or natural source. For gameplay semantics, inspect the effective recipe/tag/loot definitions after loading and run a deterministic crafting or processing fixture. Do not try to `/give @p` on a server with no player. Commands cannot replace all player-dependent APIs.

### High-value Ch4oS additions

For Create, select a short approved list of critical IDs, then add a simple powered processing fixture with known inputs, a bounded tick budget, expected outputs and a restart check for stored items/block entities. Registry lookup alone is not a Create processing test. Keep fake-player behavior distinct from real client networking.

For Tectonic/Terralith, retain a normal-generation lane with fixed seed and selected coordinates. A pregenerated laboratory world cannot detect new chunk-generation failures. Do not require byte-for-byte chunk equality or assume all mod behavior becomes deterministic from the world seed alone.

For the current beta, test MapSyncer installation, new-world default configuration, the documented existing-world config procedure, server command completion and output on a small saved-region fixture. Client map merging, transfer and reconnect remain explicitly outside a server-only result. Similarly, DH server-side generation/cache work is distinct from a client displaying LODs.

For upgrades, boot the accepted version, seed and save a fixture, stop it, clone its whole required save/config state, and run the candidate against the clone. Test both existing chunks and a few new chunks. Do not equate candidate-to-candidate restart with an old-to-new upgrade. Never use a live AMP world as the test target.

### Adding GameTests without testing the wrong installation

Build a small MVT test-only mod in this repository. Stage the resolved server-side pack and effective configs into its runtime rather than testing NeoForge plus only the test mod. Restrict execution to MVT's selected test namespace. Do not assume upstream mods ship their development tests. Verify mod IDs/versions between the ordinary server and the GameTest lane.

Keep the normal-server lane: a special GameTest server is not a complete substitute for production-style startup, saving and shutdown. Pin APIs/docs to 1.21.1; newer Minecraft GameTest documentation differs. NeoForge documents a NeoGradle force-exit caveat for `runGameTestServer`; apply the relevant build-tool configuration and require a nonzero test count plus a completion report. Never interpret zero discovered tests as a pass.

## Baseline and result policy

A startup log is a useful baseline, but not the only oracle. Store a baseline record containing an owner-approved pack SHA, image digest, actual Java runtime, loader version, suite and normalizer versions, fixture hash, relevant properties and resource class. Preserve the original logs and semantic snapshots. Establish the baseline from several successful repeated runs on the same environment; do not automatically bless the current release merely because it is named `public`.

Compare by lifecycle phase: installation, fresh boot, scenarios, save/shutdown, saved-world boot. Normalize only known volatile fields such as timestamps, a known temporary root and thread-instance suffixes. Retain severity, logger/mod name, resource IDs, versions, missing filenames, exception type and relevant stack context. Group known multiline events and compare signature counts rather than imposing a global line ordering on concurrent logs. Do not remove every number, all WARN/ERROR lines or all mixin messages.

Proposed decisions:

- FAIL: an explicit scenario fails, the server crashes, save/readback fails, a fatal signature appears, or an unapproved ERROR event appears. A genuine fatal condition cannot be allowlisted into success.
- REVIEW_REQUIRED: a new nonfatal warning or unexplained semantic snapshot change appears. Reports expose it; agents do not silently approve it.
- INCONCLUSIVE/INFRA_ERROR: artifact host/network failure, unavailable Docker, insufficient resources or environmental failure prevents valid evidence. Preserve the distinction from a confirmed pack regression.
- NOT_RUN/SKIPPED: prerequisites failed or a suite was intentionally omitted. These are not passes.

Known noisy nonfatal errors/warnings require narrow fingerprints with rationale, owner and review/expiry conditions. Compare their frequencies too; an existing warning suddenly occurring thousands of times matters. Baseline promotion must be separate from candidate execution and human approved. When environmental drift or noise is suspected, run the old baseline and candidate in the same pinned environment; the comparison still does not replace explicit correctness assertions.

## Caching and efficiency

Use three different stores. Download caches hold hash-addressed upstream artifacts. Materialized installation bundles hold an exact, verified server install with no generated world or credentials. Evidence records hold immutable results for a precise input identity. A world fixture is a fourth separately versioned input copied per run, never a shared writable cache.

Key runtime evidence by the server-relevant pack content, selected optional mods, effective configs, Minecraft/NeoForge/Java/image, harness/test-mod/suite revisions, fixture, properties and resource class. Keep the original tested commit in the evidence even when a documentation-only commit reuses the same runtime fingerprint. Do not mistake a cache hit for a test execution. Verify restored artifacts against hashes and the complete expected inventory; stale unmanaged JARs must not survive deletion from the pack.

Run one boot for a batch of compatible scenarios, not one boot per item. Reuse a verified installation for a candidate, but copy it for mutating suites. Run server-relevant changes through runtime smoke tests; reuse prior runtime evidence only when every relevant input is identical. Keep separate client and server fingerprints. Add periodic cold-install checks so caches cannot conceal distribution or download failures. Cache availability is an optimization, never a correctness requirement.

After measuring, a persistent worker with fast local storage is likely the best improvement for repeated agent iterations. Persist caches and a queue, not one contaminated always-running test world. Keep a bounded per-run VM/container lifetime, per-run directories and a cleanup reaper for hard cancellation.

## GitHub Actions and infrastructure

The pack repository can remain untouched. Start with manual `workflow_dispatch` in MVT, using a full source commit SHA. Later add a scheduled MVT job to resolve `beta`/`main` once and test only changed relevant inputs, or use a GitHub App to route approved source PR events. A separate repository does not automatically receive another repository's `pull_request` events. Source-repository check reporting requires separately authorized access; the default MVT token is not a general cross-repository writer.

Current GitHub docs list standard private-repository Ubuntu x64 runners at 2 CPUs / 8 GB RAM and public ones at 4 CPUs / 16 GB RAM. MVT was private when inspected. Allow native memory and the OS space beyond the Java heap. The example's 4 GB heap / 6 GB container cap is exploratory, not measured sizing for this pack. Treat OOM as a capacity investigation before blaming gameplay changes. Do not make the repository public just to obtain larger runners.

Recommended progression: prove the small lane on a standard hosted runner; for frequent/full-pack runs provision a dedicated Linux worker or disposable VM with approximately 16-32 GB RAM and SSD storage as an initial engineering budget, then size from observed peak memory and timings. Prefer sequential baseline/candidate runs on a small worker. No GPU is needed for the initial server-only lane. Larger GitHub-hosted runners are an alternative subject to account eligibility and pricing; verify the account's terms rather than assuming self-hosting or private runs are free.

Security: treat mod JARs and pack-provided scripts as executable code. Do not run untrusted PRs on the production AMP host or a persistent runner with valuable network access. Prefer disposable VMs for untrusted candidates. Keep workflow permissions read-only; do not inject GitHub/production credentials into the game container. Never mount the Docker socket or repository checkout into it. Do not publish RCON or game ports for a no-client smoke test. Agent debugging commands must target an explicitly allocated disposable run. Do not run untrusted code through privileged `pull_request_target` workflows. Pin reviewed Actions commits and image/tool versions; avoid floating `latest` for accepted baselines.

Sources: [GitHub runner resources](https://docs.github.com/en/actions/reference/runners/github-hosted-runners), [GitHub workflow security](https://docs.github.com/en/actions/reference/security/secure-use), [Docker JVM memory settings](https://docker-minecraft-server.readthedocs.io/en/latest/configuration/jvm-options/).

## Included examples and activation

`examples/smoke.py` is a deliberately limited cold-start lifecycle prototype using only Python's standard library and Docker. It reads an immutable source SHA, checks the pack/index link, delegates server-side installation to itzg/Packwiz, creates its own disposable volume, checks startup/ticking/Create block and item/persistence, writes JSON/JUnit/command traces/phase logs and cleans up. It never attaches to a pre-existing or live server. It does not implement log comparison, persistent caches, installed-mod auditing, upgrade worlds, recipes, GameTests or a client. Its green status is explicitly `lifecycle_passed_logs_unreviewed`, not a release approval.

`examples/test_smoke.py` contains 10 unit tests for harness behavior, including stale probe prevention, read-before-reinitialize persistence, rejection of unknown command output, SHA validation, failure classification, skipped prerequisites and secret redaction. These tests do not validate actual Minecraft responses or loader compatibility.

`examples/smoke-workflow.yml` is inert outside `.github/workflows`. To activate it, review it and copy it to `.github/workflows/smoke.yml` **in MVT only**. It is manual-only, uses read-only repository permissions, pins the checkout/artifact Actions commits, uploads evidence on failure and requires explicit Minecraft EULA acceptance. No workflow was dispatched as part of preparing these examples.

Run harness-only checks without accepting the EULA or starting Minecraft:

```sh
python3 -m unittest discover -s examples -p 'test_*.py' -v
```

After reviewing and accepting the [Minecraft EULA](https://www.minecraft.net/en-us/eula), an operator can deliberately launch the example on a disposable Linux/Docker worker:

```sh
python3 examples/smoke.py --accept-eula \
  --pack-sha 625ae3bea9775a1757b63265a392a0fcec430fd6 \
  --output mvt-results-first-run
```

The output directory must not already exist. The default `java21` image tag is exploratory: replace it with a reviewed digest using `--image` before creating comparable baselines. Commands use expected English dedicated-server responses and fail closed on other formats. Bootstrap installation tools may still resolve upstream releases; a production materialization layer should pin/record them as well as the image and game artifacts. Network access to providers is required, and unavailable downloads must not be bypassed through unapproved mirrors. Do not redistribute cached mod JARs as repository artifacts without appropriate permission.

## Implementation order and acceptance

First run and calibrate the lifecycle example on an approved worker, correcting any observed command/loader incompatibilities without weakening assertions. Establish repeatable baseline evidence and a small approved item/block contract. Next add deterministic log classification plus immutable download/install caching and installed-mod auditing. Then add old-world/config fixtures and a handful of targeted Create GameTests. Add optional real-client tests only for the gaps that matter.

Before calling the system a release gate, require: repeated known-good runs, deliberate broken-item and missing-mod failures, a deliberately corrupted persistence marker caught after restart, no stale-success behavior on invalid commands, reliable cleanup/artifacts after timeouts, a clear missing-baseline result, and a demonstrated reduction in warm-run installation cost. Capture actual timings; no full-pack runtime, throughput, or success-rate claim is made by this research/example package.
