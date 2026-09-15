# Ch4oS Modpack Verification and Testing

Research and initial implementation: 2026-09-15. Scope: repeatable, non-visual verification of the actual Create: Ch4oS Packwiz modpack. MVT is a testing project, not a replacement launcher, Packwiz installer, AMP manager, or production server.

## Decision

Use the real NeoForge dedicated-server pack, provisioned from an immutable Packwiz commit, controlled by a deterministic Python harness over RCON, and executed in disposable Docker state. Run the same harness locally or from GitHub Actions.

Do not let each agent invent a new installer, test world, shell script, or interpretation of success. Agents should select named suites, run the stable harness, inspect structured evidence, and add reproducible regression cases when they discover bugs.

**Reuse immutable installation artifacts, caches and test definitions. Do not reuse uncontrolled writable server state.** Fresh worlds and processes provide isolation; repeated downloads and rediscovery of setup are the waste.

The source modpack repository remains read-only to MVT.

## Current implemented lane: dedicated-server lifecycle

The canonical harness is `smoke.py`; its harness-only unit tests are `test_smoke.py`. `.github/workflows/smoke.yml` is the active GitHub Actions entry point.

Given a full 40-character commit SHA from `Cha0sCollective/Create-Ch4os-Packwiz`, the lifecycle suite currently:

1. downloads `pack/pack.toml` and `pack/index.toml` from that immutable commit;
2. verifies the committed Packwiz index SHA-256 link;
3. reads and pins the candidate's Minecraft and NeoForge versions;
4. resolves and records the Docker image identity before container creation;
5. creates a uniquely named disposable Docker volume/container with no published game or RCON ports;
6. boots the real NeoForge dedicated server and requires a fresh `Done` event plus a successful main-thread time query;
7. proves the game clock advances by at least 100 ticks;
8. creates a per-run scoreboard nonce, a representative Create block and Create item state in a force-loaded fixture;
9. verifies that state before saving;
10. runs `save-all flush`, requests `stop`, and requires a clean zero-exit shutdown without a forced kill;
11. restarts the same world without recreating the fixture;
12. verifies the original nonce, `create:andesite_casing`, and `create:andesite_alloy` container state survived;
13. proves the restored server continues ticking;
14. saves and cleanly shuts down a second time;
15. emits JSON, JUnit, command traces, phase logs, safe container state and provenance evidence.

A successful run is reported as `lifecycle_passed_logs_unreviewed`. That deliberately means **the implemented lifecycle assertions passed, not that the modpack is approved for release**.

The lifecycle lane does not yet implement semantic log comparison, persistent installation caches, installed-mod inventory auditing, old-version upgrade fixtures, recipes/loot semantics, NeoForge GameTests, or a real client.

## Harness correctness rules

The harness should fail closed.

- RCON transport success is not Minecraft-command success.
- Assertions use Minecraft's `execute store success` into a probe score, then parse the probe score exactly.
- Unknown or localized output is not silently accepted.
- Each command probe resets and re-reads its success state so stale success cannot make a later command pass.
- The persistence verifier must read saved state before creating or repairing any fixture state.
- Cleanup after an error never counts as a passing graceful-shutdown test.
- OOM/resource failures are infrastructure errors rather than automatically being blamed on pack behavior.
- Missing/skipped prerequisites are not passes.
- The harness never refreshes Packwiz metadata or removes a failing mod to obtain green evidence.

Harness unit tests exercise parser failure behavior, SHA validation, stale-probe prevention, persistence read-before-reinitialize behavior, nonce mismatch, downstream skipping, infrastructure-error classification and secret redaction. Those unit tests do **not** validate Minecraft itself; only an actual lifecycle run can do that.

## Result and evidence contract

The runner records the candidate pack SHA, Packwiz manifest/index hashes, requested and resolved container image identity, Java runtime, host platform, resource limits, harness hash, test timings and per-step status.

Important outputs include:

- `report.json` — structured suite status and provenance;
- `junit.xml` — CI-readable test status;
- `commands.jsonl` — RCON commands and responses by lifecycle phase;
- phase logs and safe container-state snapshots;
- the exact tested `pack.toml` and `index.toml`;
- `workflow-commit.txt` when run through GitHub Actions.

Current exit semantics:

- `0`: all implemented lifecycle assertions passed; logs remain unreviewed;
- `1`: an explicit pack/server assertion failed;
- `2`: infrastructure or another execution error prevented valid passing evidence.

Future result states should preserve the same distinction between confirmed regressions and inconclusive infrastructure failures.

## Source-pack snapshot that informed the first suite

At initial design time, `main` and `public` of the Packwiz repository pointed at `625ae3bea9775a1757b63265a392a0fcec430fd6`; `beta` pointed at `a1abcbd07107766ba6db2d92e590eb7356da4cf6`.

The accepted pack was Minecraft 1.21.1 / NeoForge 21.1.248 / Java 21. The pack includes Create integrations plus Tectonic, Terralith and Distant Horizons. Beta added MapSyncer and new/existing-world configuration considerations. Those facts motivated separate lifecycle, normal-world-generation, upgrade/config and eventual client lanes instead of treating a successful boot as proof of every integration.

## Execution architecture

A trusted MVT runner takes an immutable candidate commit, suite revision and fixture revision. It reads pack content; it does not execute arbitrary source-repository workflow code and does not modify the candidate to make checks pass.

The intended long-term execution model is:

1. **Resolve and verify inputs.** Validate Packwiz metadata, hashes, selected optional/server-side content and runtime versions.
2. **Materialize or restore an immutable installation.** Cache verified downloads/installations by content identity, never by a mutable branch name alone.
3. **Create disposable runtime state.** Copy a fixture or create a deterministic normal world into a fresh per-run directory/volume.
4. **Run named scenarios.** Record commands, expectations, timings and failed prerequisites.
5. **Save and restart where persistence matters.** Read persisted state before any reinitialization.
6. **Classify evidence.** Compare explicit assertions plus phase-specific logs/semantic snapshots.
7. **Publish immutable evidence.** Include enough provenance to reproduce or compare the run without credentials or production data.

A future stable CLI may expose higher-level commands such as `mvt run --candidate <sha> --suite <name> --baseline <id>`. Agents should invoke that stable interface rather than directly operating an arbitrary server wherever possible.

## Coverage roadmap

| Lane | Checks | Passing does not prove |
| --- | --- | --- |
| Static | Packwiz hash chain, metadata syntax, download identity, server-side selection, optional-mod policy, expected installed JAR inventory, config schema where available | Minecraft launches or behaves correctly |
| Dedicated-server lifecycle | Boot, responsiveness, ticking, representative Create IDs, save/stop/restart, persisted nonce/block/container contents | Recipes, natural loot, clients, every dimension/chunk |
| World/config compatibility | Normal seeded world, selected new chunks/dimensions, old-world clone loaded by candidate, effective new/existing-world configs | Every possible world or migration |
| Focused behavioral tests | Effective recipes/tags/loot tables, selected Create processing/item transfer, block-entity serialization, critical integration contracts | Every possible factory or interaction |
| Optional real client | Matching client launch, join, disconnect/reconnect, inventory persistence and selected handshakes | Rendering correctness or every UI path |

### Normal world generation

Retain a normal-generation lane with a fixed seed and selected coordinates for Tectonic/Terralith and related integrations. A tiny laboratory fixture cannot detect new-chunk generation failures. Avoid byte-for-byte chunk equality as a general oracle; world behavior may include legitimate nondeterminism outside the selected contracts.

### Upgrade and configuration testing

A same-version restart is not an upgrade test.

For upgrade testing, create and save a fixture under an owner-approved baseline pack, stop it, clone the complete required save/config state, then run the candidate against the disposable clone. Check existing chunks/state and selected newly generated chunks. Never point this suite at a live AMP world.

For systems such as MapSyncer that distinguish new-world defaults from existing-world configuration, test those paths separately. Server-side command completion must not be presented as proof of client map merging, transfer or reconnect behavior.

### Focused NeoForge GameTests

Add a small MVT-only NeoForge test module when console commands become inadequate for deterministic behavior contracts. Good first candidates are representative Create processing, item transfer and block-entity save/reload.

The GameTest runtime must contain the resolved pack and effective configuration, not merely NeoForge plus the MVT test mod. Require a nonzero discovered test count and explicit completion report; zero discovered tests must never count as success.

Keep the ordinary dedicated-server lifecycle lane even after GameTests exist. A special test server is not a substitute for production-style startup, persistence and shutdown evidence.

### Optional real client

Use a headless real client only for gaps that genuinely require one: modded networking/handshakes, join/disconnect/reconnect, player inventory state and client/server integrations. Rendering and visual correctness remain outside the initial non-visual scope.

## Baseline and log policy

A startup log baseline is useful, but raw text equality is not the final oracle.

A baseline record should contain an owner-approved pack SHA, image digest, Java/Minecraft/NeoForge versions, suite/normalizer versions, fixture identity, relevant properties and resource class. Preserve original logs alongside normalized signatures.

Compare logs by lifecycle phase: installation, fresh boot, scenarios, save/shutdown and saved-world boot. Normalize narrowly: timestamps, known temporary roots and other demonstrated volatile values. Preserve severity, logger/mod identity, resource IDs, versions, filenames, exception types and useful stack context.

Proposed decisions:

- **FAIL** — an explicit scenario fails, the server crashes, persistence fails, a fatal signature appears, or a new unapproved error is present;
- **REVIEW_REQUIRED** — a new nonfatal warning or unexplained semantic change needs a human decision;
- **INCONCLUSIVE / INFRA_ERROR** — network, provider, Docker or resource failure prevented valid testing;
- **NOT_RUN / SKIPPED** — a prerequisite failed or the suite was intentionally omitted.

Known noisy messages need narrow fingerprints with rationale and review conditions. Baseline promotion must be a separate human-approved operation; the candidate under test must never automatically redefine its failures as the new normal.

## Caching and efficiency

Keep separate stores for:

- hash-addressed upstream download cache;
- verified immutable server installation bundles with no world/credentials;
- separately versioned world/config fixtures copied per run;
- immutable evidence records for exact input identities.

Cache availability is an optimization, not a correctness requirement. Verify restored artifacts and complete expected inventories so a stale unmanaged JAR cannot survive removal from the Packwiz pack.

Batch compatible scenarios into one boot. Reuse prior runtime evidence only when every relevant server input is identical, while still recording the candidate commit that reused it. Add periodic cold-install runs so a warm cache cannot permanently hide distribution/download failures.

For frequent full-pack testing, a persistent worker or disposable VM pool with fast local storage is likely more efficient than repeatedly provisioning everything on generic hosted runners. Persist caches, not a contaminated always-running Minecraft world.

## GitHub Actions and infrastructure

The modpack repository can remain unchanged. MVT's active workflow currently provides:

- automatic harness-only unit tests for pull requests that modify the harness/workflow;
- an explicit `workflow_dispatch` path for full Minecraft lifecycle runs;
- read-only repository permissions;
- pinned reviewed GitHub Action commits;
- no repository credentials persisted by checkout;
- no published Minecraft/RCON ports;
- bounded job/runtime and evidence retention;
- evidence upload even when the lifecycle suite fails.

The manual lifecycle job requires explicit Minecraft EULA acceptance. A full source SHA is passed through an environment variable and validated by the harness rather than interpolated directly into shell code.

The initial 4 GiB Java heap / 6 GiB container cap is exploratory. Treat OOM as a sizing investigation before attributing it to gameplay. For frequent or larger lanes, provision a dedicated Linux worker/disposable VM with adequate SSD and memory, then size it from measured peak usage and timings.

Treat mod JARs and pack-provided executable content as untrusted code. Do not run candidate packs on the production AMP host or on a persistent worker that has valuable credentials/network access. Prefer disposable isolation for untrusted candidates and never mount a Docker socket inside the Minecraft container.

## Acceptance and next milestones

The lifecycle lane becomes trustworthy after it has been exercised on a real worker and shown to:

- pass repeatedly for an approved known-good pack;
- fail for a deliberately invalid mod/item assertion;
- detect a deliberately corrupted persistence marker after restart;
- reject invalid/unknown commands without stale-success behavior;
- preserve useful evidence after failure/timeouts;
- distinguish OOM/provider/network failures from confirmed pack failures.

After that calibration, the next high-value milestones are:

1. semantic startup/runtime log classification and owner-approved baseline records;
2. immutable download/install caching plus installed-mod inventory verification;
3. old-world and new/existing-world configuration fixtures;
4. a small set of targeted Create/GameTest behavioral contracts;
5. optional real-client coverage only for networking/player-dependent gaps.

Do not call MVT a release gate until those policies and failure cases have been deliberately demonstrated. The immediate goal is a trustworthy, reusable verification system that prevents agents from repeatedly rebuilding ad-hoc Minecraft test environments.
