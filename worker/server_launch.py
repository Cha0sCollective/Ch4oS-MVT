"""Prepared dedicated development runtime; no pack-supplied launch hooks."""
import json
import os
from pathlib import Path
import re


def prepare(directory, launch, environment):
    if environment.get("EULA") != "TRUE" or environment.get("MVT_SERVER_MODE") != "development":
        raise ValueError("Explicit EULA acceptance and development server mode required")
    password = environment.get("RCON_PASSWORD", "")
    heap = environment.get("MVT_SERVER_HEAP", "")
    if not re.fullmatch(r"[A-Za-z0-9]{16,128}", password) or not re.fullmatch(r"[1-9]\d*[GMgm]", heap):
        raise ValueError("Missing or invalid private server resource configuration")
    properties = {"online-mode": "false", "enable-rcon": "true", "rcon.password": password,
                  "rcon.port": "25575", "server-port": "25565", "server-ip": "", "level-name": "world",
                  "level-seed": "8675309", "spawn-protection": "0", "difficulty": "peaceful",
                  "view-distance": "4", "simulation-distance": "4", "enforce-secure-profile": "false"}
    # Java properties use the final occurrence; preserve unrelated candidate settings.
    with (directory / "server.properties").open("a", encoding="utf-8") as file:
        file.write("\n# MVT disposable development settings\n")
        file.write("\n".join(k+"="+v for k, v in properties.items())+"\n")
    (directory / "eula.txt").write_text("eula=true\n")
    command = launch["command"].copy()
    command[command.index("-cp"):command.index("-cp")] = [
        "-Xms512M", "-Xmx"+heap, "-Dneoforge.enableGameTest=false", "-Duser.home=/tmp", "-Djava.awt.headless=true"]
    return command


if __name__ == "__main__":
    launch = json.loads(Path("/opt/mvt/server-mod/build/mvt-server-launch.json").read_text())
    os.environ.update(launch["environment"])
    os.chdir("/data")
    command = prepare(Path("/data"), launch, os.environ)
    os.execvp(command[0], command)
