"""Trusted development entry point; no candidate command/hook execution."""
import json
import os
from pathlib import Path

launch = json.loads(Path("/opt/mvt/client-mod/build/mvt-launch.json").read_text())
os.environ.update(launch["environment"])
os.chdir("/game")
# NeoForge development launches enable bundled GameTests by default. This lane
# exercises normal client gameplay; unrelated optional mod GameTest classes must
# not be registered. Place overrides after the prepared VM argument file.
java = launch["command"].copy()
java[java.index("-cp"):java.index("-cp")] = ["-Dneoforge.enableGameTest=false", "-Duser.home=/tmp", "-XX:MaxRAMPercentage=50.0"]
command = ["xvfb-run", "-a", "-s", "-screen 0 854x480x24 -nolisten tcp", *java]
os.execvp(command[0], command)
