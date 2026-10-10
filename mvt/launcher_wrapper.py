"""Trusted Prism wrapper. Never mount the launcher account root into the game."""
import json
from pathlib import Path
import subprocess
import sys


def main():
    config = json.loads(Path(sys.argv[1]).read_text())
    command = config["docker_command"] + ["--interactive"]
    instance, launcher = Path(config["instance"]), Path(config["launcher"])
    # The launcher sends normal launch data through stdin. Keep stdin inherited, never log it.
    game = instance / ".minecraft"
    for path in (game, instance / "natives", instance / "libraries", launcher / "libraries", launcher / "assets", launcher / "jars"):
        if path.exists():
            command += ["--mount", f"type=bind,src={path},dst={path}" + (",readonly" if path != game else "")]
    command += ["--workdir", str(instance / ".minecraft"), "--entrypoint", "/opt/mvt/authenticated.sh",
                config["image_id"], *sys.argv[3:]]  # Skip Prism's host Java binary.
    created = subprocess.run(["docker", *command], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if created.returncode:
        return 2
    (Path(sys.argv[1]).parent / "launched.json").write_text('{"started":true}')
    return subprocess.call(["docker", "start", "--attach", "--interactive", config["docker_command"][2]],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == "__main__":
    sys.exit(main())
