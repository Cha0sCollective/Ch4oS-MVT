"""Trusted supervisor, with Docker access; game containers never receive it."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import signal

from mvt.__main__ import main

root = Path(os.environ["MVT_SHARED_ROOT"])
if not os.environ.get("MVT_SHARED_VOLUME") or not root.is_dir():
    raise SystemExit("Mount the named worker volume and set MVT_SHARED_VOLUME")
for directory in ("tmp", "cache", "results"):
    (root / directory).mkdir(exist_ok=True)
tempfile.tempdir = str(root / "tmp")
version = subprocess.run(["docker", "version", "--format", "{{json .Server.APIVersion}}"],
                         capture_output=True, text=True, check=True)
if tuple(map(int, json.loads(version.stdout).split("."))) < (1, 45):
    raise SystemExit("Container worker requires Docker Engine 26+ for volume subdirectory mounts")
def interrupt(signum, frame):
    raise KeyboardInterrupt
signal.signal(signal.SIGTERM, interrupt)
raise SystemExit(main(sys.argv[1:]))
