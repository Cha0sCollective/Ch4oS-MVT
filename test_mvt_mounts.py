import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mvt.errors import Unsupported
from mvt.mounts import game_mount


class MountTests(unittest.TestCase):
    def test_local_bind_and_volume_subdirectory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            game = root / "run" / "client"
            game.mkdir(parents=True)
            with patch.dict(os.environ, {"MVT_SHARED_VOLUME": "", "MVT_SHARED_ROOT": ""}):
                self.assertEqual(game_mount(game, "/game"), f"type=bind,src={game},dst=/game")
            with patch.dict(os.environ, {"MVT_SHARED_VOLUME": "mvt-data", "MVT_SHARED_ROOT": str(root)}):
                self.assertEqual(game_mount(game, "/game"), "type=volume,src=mvt-data,dst=/game,volume-subpath=run/client,volume-nocopy")
                for bad in (root, root / "missing", root.parent):
                    with self.assertRaises(Unsupported):
                        game_mount(bad, "/game")

    def test_partial_or_invalid_configuration_fails_closed(self):
        for env in ({"MVT_SHARED_VOLUME": "mvt-data"}, {"MVT_SHARED_ROOT": "/data"},
                    {"MVT_SHARED_VOLUME": "mvt,data", "MVT_SHARED_ROOT": "/data"},
                    {"MVT_SHARED_VOLUME": "mvt-data", "MVT_SHARED_ROOT": "relative"}):
            with patch.dict(os.environ, {"MVT_SHARED_VOLUME": "", "MVT_SHARED_ROOT": "", **env}):
                with self.assertRaises(Unsupported):
                    game_mount(Path("/data/game"), "/game")

    def test_symlink_escape_cannot_expose_worker_files(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as other:
            root = Path(directory).resolve()
            link = root / "client"
            try:
                link.symlink_to(Path(other).resolve(), target_is_directory=True)
            except OSError:
                self.skipTest("Symlink creation unavailable")
            with patch.dict(os.environ, {"MVT_SHARED_VOLUME": "mvt-data", "MVT_SHARED_ROOT": str(root)}):
                with self.assertRaises(Unsupported):
                    game_mount(link, "/game")
