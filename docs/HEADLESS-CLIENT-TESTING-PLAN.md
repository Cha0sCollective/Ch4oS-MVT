# Extend MVT with headless client testing

Status (2026-10-21): Create Ch4oS passed the single-player suite and all 24 client-server report checks using matching Docker development runtimes. The dedicated development-server mode resolved the earlier Create Abyss channel mismatch. See the [development-server validation](DEVELOPMENT-SERVER-VALIDATION.md), [original worker report](LOCAL-WORKER-VALIDATION.md) and [implemented commands and remaining acceptance](HEADLESS-USAGE.md). Full calibration and licensed authenticated joins remain pending. Saved for review on 2026-09-22 and revised on 2026-10-09 for pack-independent inputs and runtime-selected adapters.

## Summary

Generalize MVT to NeoForge packs running Minecraft 1.21.1, then add two suites alongside the existing dedicated-server lifecycle suite:

- **Client smoke:** launch the real client pack, load a single-player world, interact, save, and reopen it.
- **Client–server smoke:** authenticate, join a disposable server, verify interactions and synchronization, reconnect, and verify persistence after restart.

Use one licensed test account, an isolated Linux worker, and a small MVT-only client automation mod. Headless execution may use an invisible virtual display and software OpenGL; screenshots, visual assertions, and graphics performance testing are explicitly outside MVT.

Approved addition (2026-10-10): support explicit `development` client mode using
NeoForge's development player and an isolated offline-mode disposable server.
It reports authentication as `NOT_TESTED`; the licensed authenticated lane remains
separate. This enables worker-based client/gameplay checks without setting up a
test account and does not assert a license exemption. Implementation/build work
now includes a local Docker Linux worker; the original feasibility and calibration
requirements below still apply before adopting a release gate.

Approved addition (2026-10-21): an explicit dedicated development-server mode
matches the development client when mods enable development-only content. Its
server bootstrap is a recorded source overlay for GameTest runtime settings and
identity only; gameplay actions and independent RCON assertions are unchanged.
Production servers remain free of MVT mod overlays. Authentication is untested
in the development pair, which does not replace production compatibility checks.

Keep the core lifecycle and interaction tests independent of individual mods. Select additional mod-specific test adapters at runtime from the installed mod inventory, starting with Create. Support the exact NeoForge version declared by each pack when it is compatible with Minecraft 1.21.1 and MVT's test components; do not substitute a newer loader automatically. Forge, Fabric, other loaders, and other Minecraft versions are outside scope.

The existing harness provides useful isolation, assertions, and reporting. All **15 current unit tests passed** during the original planning review on 2026-09-22; no Minecraft runtime tests were performed in that review.

## Accounts and headless execution

- Have a designated owner acquire a dedicated Microsoft account with a purchased Java Edition entitlement through the [official Minecraft store](https://www.minecraft.net/en-us/store/minecraft-java-bedrock-edition-pc). Treat this as an operator prerequisite, not an automated purchase.
- Use a pinned Prism Launcher installation for sign-in, entitlement checks, and session renewal. Its [command-line interface](https://prismlauncher.org/wiki/getting-started/command-line-interface/) supports selecting an instance and account.
- Require initial interactive sign-in and occasional owner reauthentication. Normal test runs remain unattended. Authentication failures produce `AUTH_REQUIRED` or an infrastructure error; never silently switch the server to offline mode.
- Serialize runs using the account. A later two-player suite will require a second independently licensed account.
- Keep account storage private to the launcher, outside repositories, installation caches, and uploaded evidence. Launch candidate game code inside a sandbox through a launcher wrapper; expose only required game files and runtime session credentials. Candidate mods necessarily share access to the game’s session, making a dedicated account important.
- Download Minecraft through authorized sources and keep game installations private. The [Minecraft EULA](https://www.minecraft.net/en-us/eula) permits purchased-client use and modding, but does not establish a special CI license; do not assume a free automated-testing exemption.
- Run the client under Xvfb with Mesa software rendering. [Mesa LLVMpipe](https://docs.mesa3d.org/drivers/llvmpipe.html) provides software rasterization. No physical display or GPU is required by the proposed design, but full-pack compatibility must be demonstrated before adoption.

## Implementation changes

### Harness and provisioning

- Extract reusable provisioning, process supervision, assertions, cleanup, and reporting from `smoke.py`. Preserve its existing command and lifecycle behavior.
- Add `python -m mvt run --pack-sha <sha> --suite <suite> --accept-eula --output <directory>`, supporting `server-lifecycle`, `client-smoke`, and `client-server-smoke`. Accept `--pack-repo` and `--pack-path` for other pinned Packwiz repositories, defaulting to the existing Ch4oS repository and `pack/` path.
- Add a mutually exclusive `--prism-zip <path>` input for NeoForge 1.21.1 Prism instance exports. Hash the archive, read instance/runtime metadata, and resolve bundled files or a declared Packwiz source through MVT's installer without executing embedded launch scripts. Reject unsafe archive paths and unsupported instance layouts explicitly.
- For Packwiz inputs, resolve client and server installations from the same immutable source and respect side selection and pack-default optional-mod settings. For Prism inputs, use authoritative side metadata or an explicitly supplied matching server pack; a client ZIP without either supports client testing only. Do not guess which mods to remove to construct a server. Record resolved sources, selections, and installed-file hashes. [Packwiz’s installer](https://packwiz.infra.link/tutorials/installing/packwiz-installer/) already supports side-specific installation.
- Cache verified downloads privately, but create fresh writable instances and worlds per run. Record the automation mod as an explicit test overlay; never modify the source pack or silently remove incompatible mods.
- Require Minecraft 1.21.1 and NeoForge. Resolve and pin the pack's exact loader version, and check compatibility with the automation mod and chosen adapters before launch. Calibrate against the Ch4oS loader and at least one different NeoForge version compatible with Minecraft 1.21.1; unsupported combinations must produce an explicit incomplete-run result. Pin Java 21, launcher, installer, worker image, and automation-mod build.
- Use an isolated per-run network for client and server, with no public game or RCON ports. Keep authentication/download access available while denying access to production infrastructure.

### Core tests and mod-specific adapters

- Make the generalized core tests use vanilla blocks/items and a per-run persistence marker. Packs without Create must be able to complete the core suites. Retain the existing Ch4oS command's Create assertions for backward compatibility through the Create adapter.
- Ship versioned adapters in MVT, separate from pack inputs. Each declares required mod IDs, compatible mod/loader versions, supported suites, and its scenarios. Runtime selection uses verified installed metadata; a scenario must also confirm the required mods loaded successfully.
- Default to `--adapters auto`, enabling every compatible adapter whose required mods are installed on the sides its tests exercise. Allow a comma-separated explicit adapter list or `--adapters none` for core-only runs. Record the exact selected adapters, versions, and scenarios before testing.
- An explicitly requested adapter with missing or unsupported prerequisites makes the run incomplete and exits `2`. In auto mode, an installed mod recognized by an applicable adapter but having an incompatible version likewise produces unavailable coverage and an incomplete run; core results are still preserved. Mods with no adapter remain visible as having no focused coverage; their presence alone does not prevent core testing. Operators can explicitly choose core-only testing with `--adapters none`.
- Implement Create as the first adapter, preserving casing/alloy persistence checks and adding client-observed Create interactions. Keep registry IDs and Create-specific assertions inside that adapter. Additional adapters are later additions using the same contract; adapter combinations must use separate fixture areas and avoid overlapping state.

### Client automation

- Build a client-only NeoForge mod that performs normal client actions and observes client state. The dedicated server remains free of test instrumentation.
- Connect it to the Python supervisor through a private, versioned command/event interface. Include run identity, request identity, sequence numbers, deadlines, and explicit completion events; reject stale or malformed responses.
- Execute game actions on the appropriate client thread. Verify readiness through client/world state and heartbeat events rather than log messages alone.
- Use RCON for fixture preparation and independent server assertions. Interactions under test must travel through normal client networking; directly editing server state cannot satisfy them.

### Required scenarios

| Suite | Assertions |
| --- | --- |
| Client startup | Pack loads, resource loading completes, title state becomes ready, and the client remains responsive. |
| Single-player lifecycle | Create a disposable fixed-seed world; interact with vanilla block/item state and the persistence marker; save, exit, reopen, and verify persisted state before recreating anything. Selected adapters add their own mod-state checks. |
| Authenticated join | Join an `online-mode=true` server; complete mod negotiation; observe the player, expected dimension, and fixture chunks on the client. |
| Bidirectional synchronization | Move the player, place a vanilla block, open a chest, and transfer a vanilla item using client actions. Confirm final state independently on client and server. The selected Create adapter additionally exercises casing placement and alloy transfer. |
| Reconnect and persistence | Disconnect and reconnect; then cleanly restart server and client. Verify player inventory and fixture contents without reseeding them. |

## Evidence, CI, and acceptance

- Preserve JSON/JUnit reporting and exit codes: `0` for completed passing assertions, `1` for assertion failures, and `2` for execution/infrastructure errors. Missing prerequisites and zero executed scenarios never pass.
- Version the expanded report schema. Record input identity, client/server inventories, exact NeoForge version, selected/unavailable adapters and coverage, runtime identities, test-overlay hash, headless settings, phase timings, client events, server probes, disconnect reasons, and sanitized crash evidence. Distinguish passing core tests from incomplete adapter coverage.
- Exclude credentials, raw launch arguments, account files, and complete game installations from artifacts. Retain existing “logs unreviewed” and “not release approval” semantics.
- Keep credential-free unit tests on hosted Ubuntu/Windows runners. Add manually dispatched client suites on the dedicated Linux worker, using trusted MVT code and reviewed candidate commits; fork PRs must not run with account access.
- Test stale events, timeouts, client crashes, failed authentication, failed joins, inventory disagreement, persistence corruption, cleanup, and credential redaction. Distinguish resource/provider failures from demonstrated pack failures.
- Test a NeoForge 1.21.1 pack without Create, Ch4oS with Create auto-selected, explicit adapter selection, unsupported adapter versions, rejected loaders/Minecraft versions, and at least two supported NeoForge versions. Verify Packwiz and Prism inputs, unsafe ZIP rejection, and explicit client-only coverage when a Prism ZIP has no authoritative server configuration.

Deliver in this order:

1. **Feasibility:** prove the complete Ch4oS client launches headlessly, authenticates, joins, and exits repeatedly. If software rendering is incompatible, report the blocker without dropping mods or claiming coverage.
2. **Pack generalization:** implement source inputs, vanilla core fixtures, adapter selection, and the Create adapter. Demonstrate dedicated-server tests on packs with and without Create while preserving existing Ch4oS behavior.
3. **Client suite:** implement provisioning, the automation interface, and single-player lifecycle checks.
4. **Interaction suite:** implement authenticated joining, bidirectional assertions, reconnect, and restart persistence, including selected Create checks.
5. **Calibration:** require three consecutive known-good passes per reference pack, plus deliberate failures that demonstrate each major assertion detects regressions.

Two-client synchronization, MapSyncer-specific contracts, upgrade-world testing, and all graphical testing remain separate later work. One account and one concurrent client are the defaults for this first expansion.
