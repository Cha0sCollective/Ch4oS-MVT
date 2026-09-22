# Extend MVT with headless client testing

Status: Proposed; saved for later review on 2026-09-22. This document does not implement or validate the proposed client suites.

## Summary

Add two suites alongside the existing dedicated-server lifecycle suite:

- **Client smoke:** launch the real client pack, load a single-player world, interact, save, and reopen it.
- **Client–server smoke:** authenticate, join a disposable server, verify interactions and synchronization, reconnect, and verify persistence after restart.

Use one licensed test account, an isolated Linux worker, and a small MVT-only client automation mod. Headless execution may use an invisible virtual display and software OpenGL; screenshots, visual assertions, and graphics performance testing are explicitly outside MVT.

The existing harness provides useful isolation, assertions, and reporting. All **15 current unit tests passed** during the planning review; no Minecraft runtime tests were performed.

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
- Add `python -m mvt run --pack-sha <sha> --suite <suite> --accept-eula --output <directory>`, supporting `server-lifecycle`, `client-smoke`, and `client-server-smoke`.
- Resolve client and server installations from the same immutable Packwiz commit. Respect side selection and pack-default optional-mod settings; record all selections and verify installed-file hashes. [Packwiz’s installer](https://packwiz.infra.link/tutorials/installing/packwiz-installer/) already supports side-specific installation.
- Cache verified downloads privately, but create fresh writable instances and worlds per run. Record the automation mod as an explicit test overlay; never modify the source pack or silently remove incompatible mods.
- Keep Minecraft and NeoForge within the currently supported 1.21.1/21.1.x range. Pin Java 21, launcher, installer, worker image, and automation-mod build.
- Use an isolated per-run network for client and server, with no public game or RCON ports. Keep authentication/download access available while denying access to production infrastructure.

### Client automation

- Build a client-only NeoForge mod that performs normal client actions and observes client state. The dedicated server remains free of test instrumentation.
- Connect it to the Python supervisor through a private, versioned command/event interface. Include run identity, request identity, sequence numbers, deadlines, and explicit completion events; reject stale or malformed responses.
- Execute game actions on the appropriate client thread. Verify readiness through client/world state and heartbeat events rather than log messages alone.
- Use RCON for fixture preparation and independent server assertions. Interactions under test must travel through normal client networking; directly editing server state cannot satisfy them.

### Required scenarios

| Suite | Assertions |
| --- | --- |
| Client startup | Pack loads, resource loading completes, title state becomes ready, and the client remains responsive. |
| Single-player lifecycle | Create a disposable fixed-seed world; interact with representative Create block/item state; save, exit, reopen, and verify persisted state before recreating anything. |
| Authenticated join | Join an `online-mode=true` server; complete mod negotiation; observe the player, expected dimension, and fixture chunks on the client. |
| Bidirectional synchronization | Move the player, place a Create casing, open a chest, and transfer Create alloy using client actions. Confirm final state independently on client and server. |
| Reconnect and persistence | Disconnect and reconnect; then cleanly restart server and client. Verify player inventory and fixture contents without reseeding them. |

## Evidence, CI, and acceptance

- Preserve JSON/JUnit reporting and exit codes: `0` for completed passing assertions, `1` for assertion failures, and `2` for execution/infrastructure errors. Missing prerequisites and zero executed scenarios never pass.
- Version the expanded report schema. Record client/server inventories, runtime identities, test-overlay hash, headless settings, phase timings, client events, server probes, disconnect reasons, and sanitized crash evidence.
- Exclude credentials, raw launch arguments, account files, and complete game installations from artifacts. Retain existing “logs unreviewed” and “not release approval” semantics.
- Keep credential-free unit tests on hosted Ubuntu/Windows runners. Add manually dispatched client suites on the dedicated Linux worker, using trusted MVT code and reviewed candidate commits; fork PRs must not run with account access.
- Test stale events, timeouts, client crashes, failed authentication, failed joins, inventory disagreement, persistence corruption, cleanup, and credential redaction. Distinguish resource/provider failures from demonstrated pack failures.

Deliver in this order:

1. **Feasibility:** prove the complete pack launches headlessly, authenticates, joins, and exits repeatedly. If software rendering is incompatible, report the blocker without dropping mods or claiming coverage.
2. **Client suite:** implement provisioning, the automation interface, and single-player lifecycle checks.
3. **Interaction suite:** implement authenticated joining, bidirectional assertions, reconnect, and restart persistence.
4. **Calibration:** require three consecutive known-good passes, plus deliberate failures that demonstrate each major assertion detects regressions.

Two-client synchronization, MapSyncer-specific contracts, upgrade-world testing, and all graphical testing remain separate later work. One account and one concurrent client are the defaults for this first expansion.
