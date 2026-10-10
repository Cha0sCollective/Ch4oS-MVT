"""Share only a run's subdirectories with sibling Docker containers."""
from __future__ import annotations

import os
from pathlib import Path
import re

from .errors import Unsupported


def game_mount(source: Path, destination: str) -> str:
    volume = os.environ.get("MVT_SHARED_VOLUME")
    root = os.environ.get("MVT_SHARED_ROOT")
    if not volume and not root:
        if "," in str(source):
            raise Unsupported("Docker bind path cannot contain a comma")
        return f"type=bind,src={source},dst={destination}"
    if not volume or not root or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", volume):
        raise Unsupported("Container worker requires a valid shared volume and absolute root")
    shared = Path(root)
    if not shared.is_absolute():
        raise Unsupported("Shared volume root must be absolute")
    try:
        relative = source.resolve(strict=True).relative_to(shared.resolve(strict=True))
    except (ValueError, OSError) as exc:
        raise Unsupported("Game mount must be an existing directory inside the worker volume") from exc
    if not relative.parts or not source.is_dir() or "," in relative.as_posix():
        raise Unsupported("Mount a game subdirectory, never the whole worker volume")
    return f"type=volume,src={volume},dst={destination},volume-subpath={relative.as_posix()},volume-nocopy"
