"""Private file protocol; process liveness is never scenario success."""
from __future__ import annotations

import json
from pathlib import Path
import time
import tempfile
import uuid

from .errors import CheckFailed, InfrastructureError
from .report import sanitize
from .files import read_regular

PROTOCOL = 1


class Control:
    def __init__(self, directory: Path, alive=lambda: True):
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=False)
        self.run_id = uuid.uuid4().hex
        self.sequence = 0
        self.alive = alive
        # The candidate sees /control, never this sibling host-owned trace.
        self.trace_path = directory.with_name(directory.name+"-trace.jsonl")

    def call(self, operation: str, *, timeout=60, **arguments) -> dict:
        self.sequence += 1
        request_id = uuid.uuid4().hex
        request = {"protocol": PROTOCOL, "run_id": self.run_id, "request_id": request_id,
                   "sequence": self.sequence, "operation": operation, "arguments": arguments}
        path = self.directory / "request.json"
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.directory, delete=False,
                                         prefix="request-", suffix=".tmp") as file:
            temp = Path(file.name)
            json.dump(request, file)
        temp.replace(path)
        self.trace({"event": "request", **request})
        deadline = time.monotonic() + timeout
        response_path = self.directory / "response.json"
        while time.monotonic() < deadline:
            if response_path.exists():
                try:
                    response = json.loads(read_regular(response_path, 1_000_000).decode("utf-8"))
                except (ValueError, OSError) as exc:
                    raise InfrastructureError("Malformed client response") from exc
                if not isinstance(response, dict) or not isinstance(response.get("sequence"), int):
                    raise InfrastructureError("Malformed client response identity")
                if response.get("run_id") != self.run_id or response["sequence"] < self.sequence:
                    pass  # A prior atomic response cannot satisfy this request.
                else:
                    if any(response.get(k) != request[k] for k in ("protocol", "run_id", "request_id", "sequence")):
                        raise InfrastructureError("Client protocol identity/order mismatch")
                    if response.get("status") == "failed":
                        raise CheckFailed("Client assertion/action failed: " + str(response.get("detail", "unknown")))
                    if response.get("status") != "completed" or not isinstance(response.get("result"), dict):
                        raise InfrastructureError("Invalid client completion response")
                    self.trace({"event": "completion", **response})
                    return response["result"]
            if not self.alive():
                raise CheckFailed("Client exited before completing the requested scenario")
            time.sleep(0.05)
        raise InfrastructureError(f"Client operation deadline exceeded: {operation}")

    def trace(self, value):
        with self.trace_path.open("a", encoding="utf-8") as file:
            file.write(sanitize(json.dumps(value)) + "\n")

    def until(self, predicate, *, timeout=120, **arguments):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = self.call("snapshot", timeout=max(0.1, deadline-time.monotonic()), **arguments)
            if predicate(state):
                return state
            time.sleep(0.2)
        raise CheckFailed("Client did not reach the expected observable state")
