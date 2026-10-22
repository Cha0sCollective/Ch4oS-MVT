"""Host reads must not follow links produced inside a candidate game container."""
import os
from pathlib import Path
import stat

from .errors import InfrastructureError


def read_regular(path: Path, limit: int) -> bytes:
    if path.is_symlink():
        raise InfrastructureError("Runtime evidence contains a symbolic link")
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise InfrastructureError("Runtime evidence is not a regular file")
            data = stream.read(limit+1)
    except OSError as exc:
        raise InfrastructureError("Runtime evidence cannot be safely read") from exc
    if len(data) > limit:
        raise InfrastructureError("Runtime evidence exceeds size limit")
    return data


def safe_tree(root: Path):
    if root.is_symlink() or not root.is_dir():
        raise InfrastructureError("Disposable game directory was replaced")
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            mode = (Path(directory) / name).lstat().st_mode
            if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                raise InfrastructureError("Disposable game state contains a link or special file")
