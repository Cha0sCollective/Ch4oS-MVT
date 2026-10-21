# Development suite calibration

This operator-only driver runs real Minecraft clients and servers against the
ordinary MVT assertions. It is separate from `python -m mvt` and the release
workflow. It requires the private Linux Docker worker described in
[HEADLESS-USAGE](../docs/HEADLESS-USAGE.md), an owned `mvt-worker-data` volume,
and local client/server development images for **both 21.1.233 and 21.1.248**.
Build each exact version using the Dockerfiles below (replace `21.1.233` with
`21.1.248` for the second pair):

```powershell
docker build -f worker/Dockerfile --build-arg NEOFORGE_VERSION=21.1.233 `
    -t mvt-client-dev:21.1.233 .
docker build -f worker/Dockerfile.server `
    --build-arg CLIENT_RUNTIME_IMAGE=mvt-client-dev:21.1.233 `
    --build-arg NEOFORGE_VERSION=21.1.233 -t mvt-server-dev:21.1.233 .
```

Run only reviewed MVT code and reviewed candidate packs. The supervisor controls
Docker; game containers have isolated networks and never receive its socket.
Keep the images, verified cache, generated pack archives and game dependencies
private. Run game cases serially using the documented 4G server heap, 6G server
container and 8G client container profile. A supervisor uses another 1G.
Do not rebuild or retag worker/runtime images during an active run: the helper
resolves their identities once, and a replaced untagged image may become
unavailable between suites on a containerd-backed Docker engine.

## Run the matrix

Use a new output root. First run three ordinary Ch4oS repetitions, each containing
both suites:

```powershell
$pack = '8106370ce2a239ae1727c4ed08173d08238e766b'
foreach ($repeat in 1..3) {
    ./worker/local.ps1 -PackSha $pack -AcceptEula -ServerMode development `
        -Output "./mvt-results-calibration/ch4os-final-$repeat"
    if ($LASTEXITCODE -ne 0) { throw 'Clean reference repetition failed' }
}
foreach ($phase in 'reference','faults','inputs','auxiliary') {
    ./calibration/local.ps1 -Phase $phase -AcceptEula `
        -Output "./mvt-results-calibration/$phase-final"
    if ($LASTEXITCODE -ne 0) { throw "Calibration stopped in $phase" }
}
python -B -m calibration.verify --root mvt-results-calibration `
    --ch4os-prefix ch4os-final- `
    --reference reference-final --faults faults-final --inputs inputs-final `
    --auxiliary auxiliary-final --output mvt-results-calibration/certificate.json
```

`reference` runs six clean suites on a declared minimal NeoForge-only 21.1.233
fixture: three consecutive repetitions of each suite. It contains no third-party
mods. Its deterministic Prism archive uses the real importer. The authored
fixture disables NeoForge's optional early loading window with
`config/fml.toml: earlyWindowControl = false`, following the
[loader's guidance](https://neoforged.net/meta/displayerrors/), after a startup
failure in that loading window was observed. The actual game client, networking
and gameplay assertions still run. MVT does not change arbitrary candidate packs
to add this setting. The calibration
definition explicitly declares an empty server counterpart; the driver supplies
that declaration for this known fixture only. Ordinary user archives still need
an authoritative or explicit matching server counterpart. This is independent
vanilla-core coverage, not evidence for a representative third-party modpack.

`faults` runs twelve probes per suite. It changes actual world state for placement,
transfer, inventory disagreement, the saved nonce and a saved region; kills,
pauses or induces a cgroup OOM in the owned client; injects a stale completion
while that client is paused; sends SIGTERM to the supervisor; deliberately holds
a private network open during cleanup; and sends a synthetic token-shaped value
through real commands. Assertions and completion rules remain unchanged.
After a world mutation the calibration driver bounds state waits to 15 seconds;
paused-client operation waits are two seconds. Ordinary suite deadlines are
unchanged. Every mutation must be independently verified before its failure can
count. The cleanup probe removes its own holder after preserving MVT's failed
cleanup report.

`inputs` checks nine rejected inputs/prerequisites without starting a game:
wrong Minecraft, wrong loader, unsafe ZIP path, launch hook, client-only ZIP
without counterpart, mismatched counterpart loader, missing account profile,
wrong runtime image and unsupported Create metadata. Its Create JAR is a
metadata-only negative fixture and is never launched.

`auxiliary` checks a full Ch4oS Prism export with an explicit Packwiz server and
explicit Create selection, Ch4oS core-only mode, the real development-client vs
production-server channel mismatch, and unavailable explicit Create coverage on
the minimal reference. The Prism export includes every pinned client file; the
auditor compares its installed hashes with ordinary Packwiz runs.
This includes the pack's license notices: supported bundled exports retain
bounded UTF-8 `.txt`/`.md` files under `third-party-notices/`, rather than omitting
them to fit the import layout.

## Evidence and diagnosis

Each raw schema-2 report retains its actual result and exit code. Expected
failures remain failures. `case-result.json` and `phase.json` separately record
whether the planned oracle matched. A mismatched oracle stops the phase.
`-Case EXACT_CASE_ID` runs one case for diagnosis; a partial phase cannot certify
the full matrix. Always use new output directories and preserve failed evidence.

The offline auditor requires twelve fully completed clean repetitions, all
planned probes, matching source fingerprints and image identities, clean exits,
fault verification, no fixture reseeding, cleanup proof and report hashes. It
emits `CALIBRATED_DEVELOPMENT` only for that exact matrix. Changing calibration
Python sources or the reference definition requires rerunning its phases;
changing MVT code requires rerunning the entire matrix. Individual suite reports
keep `calibration: NOT_PERFORMED`; the separate aggregate certificate records
the reviewed scope rather than silently approving later runs.

Reports and traces remain under the ignored local results root and in the private
worker volume. The checked-in certificate can contain metadata, hashes and
relative evidence paths; never commit the pack ZIPs, complete installs or account
data. Calibration does not enable a release gate, review log baselines, test
graphics, or prove authenticated/production compatibility. An actual signed-in
account and separate online-mode validation are still required for that lane.
