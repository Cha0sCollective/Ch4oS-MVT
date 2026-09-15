# Agent use of Ch4oS MVT

MVT is a test appliance. Agents working on the modpack should invoke it, read its result, and avoid reconstructing lifecycle tests from scratch.

## Stable interface

The canonical local command is:

```text
python mvt.py check <full-pack-commit-sha> --accept-eula --output <new-results-directory>
```

`mvt.py` is intentionally thin. It delegates the real lifecycle work to `smoke.py`; it does not contain a second implementation of the tests.

By default it emits one compact JSON object. A passing run looks like:

```json
{"status":"pass","suite":"lifecycle","pack_sha":"...","minecraft":"1.21.1","neoforge":"21.1.248","checks_passed":10,"checks_total":10,"evidence":"..."}
```

Use `--format text` when a human-readable one-line result is preferable.

## Modpack-agent workflow

1. Make the candidate changes in `Cha0sCollective/Create-Ch4os-Packwiz`.
2. Commit and push the candidate state.
3. Obtain its full 40-character commit SHA.
4. Run MVT against that SHA.
5. Use the compact result as the primary decision input.
6. Inspect detailed evidence only when the result is not `pass` or when a human explicitly requests log review.

Do not regenerate `smoke.py`, create a temporary replacement harness, or improvise a different Docker lifecycle for ordinary modpack verification.

## Result handling

`status=pass` means every implemented lifecycle assertion passed. The current suite still reports its detailed internal status as `lifecycle_passed_logs_unreviewed`; this is a lifecycle-test result, not a broad release approval.

`status=fail` means a pack/server assertion failed. Read the returned `failed_step` and `detail`, then inspect evidence in this order:

1. `report.json`
2. `commands.jsonl`
3. the log for the failed phase (`fresh.log` or `saved.log`)
4. additional evidence only when necessary

`status=error` means valid lifecycle evidence could not be obtained. Treat it as an MVT/infrastructure problem unless the evidence identifies the candidate pack as the cause.

Do not load complete server logs into model context when the failed step and a focused excerpt are sufficient.

## Local examples

PowerShell:

```powershell
$PackSha = git rev-parse HEAD
python C:\path\to\Ch4oS-MVT\mvt.py check $PackSha --accept-eula --output mvt-results
```

Linux/macOS shell:

```sh
PACK_SHA=$(git rev-parse HEAD)
python3 /path/to/Ch4oS-MVT/mvt.py check "$PACK_SHA" --accept-eula --output mvt-results
```

The operator or automation environment must already be configured to accept the Minecraft EULA before passing `--accept-eula`.

## Remote execution

When the agent does not have Docker, use the existing `Ch4oS Modpack Verification and Testing` GitHub Actions `workflow_dispatch` in this repository. Supply the exact pack SHA, explicit EULA acceptance configured by the operator, and the desired server image.

After the run, use the workflow result and uploaded `report.json` first. Download or inspect the larger logs only for a failed step that requires them.

## Maintenance boundary

Ordinary modpack tasks should not modify MVT internals. Modify `smoke.py` only when the assigned task is MVT maintenance or when collected evidence demonstrates a harness defect. Add a regression test for any confirmed harness bug before changing behavior.
