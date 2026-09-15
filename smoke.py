import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class InfraError(Exception):
    pass

class TestFailure(Exception):
    pass

def run_cmd(cmd, check=True, capture_output=True, timeout=None):
    logging.debug(f"Running command: {' '.join(cmd)}")
    try:
        return subprocess.run(cmd, check=check, capture_output=capture_output, text=True, timeout=timeout)
    except FileNotFoundError:
        raise InfraError(f"Command not found: {cmd[0]}")
    except subprocess.CalledProcessError as e:
        if not check:
            return e
        raise InfraError(f"Command failed with exit code {e.returncode}: {e.stderr}")

def docker_rcon(container_name, password, command, timeout=10):
    cmd = [
        "docker", "exec", "-i", container_name,
        "rcon-cli", "--password", password, command
    ]
    try:
        res = run_cmd(cmd, timeout=timeout)
        # Redact password if it somehow ends up in output
        out = res.stdout.strip().replace(password, "***")
        return out
    except subprocess.TimeoutExpired:
        logging.error(f"RCON command '{command}' timed out")
        raise
    except subprocess.CalledProcessError as e:
        logging.error(f"RCON command '{command}' failed: {e.stderr}")
        raise

def wait_for_rcon(container_name, password, timeout=300):
    logging.info(f"Waiting for RCON to become available (up to {timeout}s)...")
    start = time.time()
    while time.time() - start < timeout:
        try:
            res = docker_rcon(container_name, password, "list", timeout=5)
            if "players online" in res or "online" in res:
                logging.info("RCON is ready and server is responsive!")
                return True
        except Exception:
            pass
        time.sleep(5)
    raise TimeoutError("Server did not become ready for RCON commands.")

def write_junit(test_cases, output_file):
    testsuite = ET.Element("testsuite", name="mvt-smoke", tests=str(len(test_cases)))
    for name, success, error_msg in test_cases:
        testcase = ET.SubElement(testsuite, "testcase", name=name)
        if not success:
            failure = ET.SubElement(testcase, "failure")
            failure.text = error_msg
    tree = ET.ElementTree(testsuite)
    tree.write(output_file, xml_declaration=True, encoding="utf-8")

def main():
    parser = argparse.ArgumentParser(description="Ch4oS MVT - Smoke Test Runner")
    parser.add_argument("--accept-eula", action="store_true", help="Accept the Minecraft EULA")
    parser.add_argument("--pack-sha", required=True, help="Git SHA of the Packwiz pack to test")
    parser.add_argument("--output", required=True, help="Output directory for logs and results")
    parser.add_argument("--image", default="itzg/minecraft-server:java21", help="Docker image to use")
    parser.add_argument("--test-block", default="create:andesite_casing", help="Block ID for persistence test")
    parser.add_argument("--test-item", default="create:andesite_alloy", help="Item ID for persistence test")
    parser.add_argument("--rcon-password", default="mvt-secret", help="RCON password to configure")
    
    args = parser.parse_args()

    if not args.accept_eula:
        logging.error("You must accept the EULA by passing --accept-eula")
        sys.exit(1)

    if not re.match(r"^[0-9a-f]{40}$", args.pack_sha):
        logging.error(f"Invalid pack SHA: {args.pack_sha}")
        sys.exit(1)

    if os.path.exists(args.output):
        logging.error(f"Output directory {args.output} already exists. Please provide a fresh path.")
        sys.exit(1)

    os.makedirs(args.output)
    
    pack_url = f"https://raw.githubusercontent.com/Cha0sCollective/Create-Ch4os-Packwiz/{args.pack_sha}/pack/pack.toml"
    container_name = f"mvt-smoke-{uuid.uuid4().hex[:8]}"
    volume_name = f"{container_name}-data"
    
    test_cases = []
    
    def record_test(name, success, error_msg=""):
        test_cases.append((name, success, error_msg))
        if success:
            logging.info(f"PASS: {name}")
        else:
            logging.error(f"FAIL: {name} - {error_msg}")

    try:
        # Create volume
        logging.info(f"Creating Docker volume: {volume_name}")
        run_cmd(["docker", "volume", "create", volume_name])
        
        # Start container
        logging.info(f"Starting server container: {container_name}")
        env_args = [
            "-e", "EULA=TRUE",
            "-e", "TYPE=NEOFORGE",
            "-e", f"PACKWIZ_URL={pack_url}",
            "-e", "PACKWIZ_BOOTSTRAP_EXTRA_ARGS=-g -s server",
            "-e", f"RCON_PASSWORD={args.rcon_password}",
            "-e", "ENABLE_RCON=true",
        ]
        
        run_cmd([
            "docker", "run", "-d",
            "--name", container_name,
            "-v", f"{volume_name}:/data",
        ] + env_args + [args.image])

        # Wait for boot
        try:
            wait_for_rcon(container_name, args.rcon_password, timeout=600)
            record_test("server_boot", True)
        except Exception as e:
            record_test("server_boot", False, str(e))
            raise

        # Scenario: Setup environment
        docker_rcon(container_name, args.rcon_password, "forceload add 0 0")
        
        # Nonce
        nonce = uuid.uuid4().hex[:8]
        docker_rcon(container_name, args.rcon_password, "scoreboard objectives add mvt_nonce dummy")
        docker_rcon(container_name, args.rcon_password, f"scoreboard players set test_run mvt_nonce {int(nonce, 16) % 1000000}")
        record_test("set_scoreboard_nonce", True)

        # Block
        block_pos = "0 100 0"
        res = docker_rcon(container_name, args.rcon_password, f"setblock {block_pos} {args.test_block}")
        if "Changed the block" in res or "placed" in res.lower() or "set" in res.lower():
            record_test("place_test_block", True)
        else:
            record_test("place_test_block", False, f"Unexpected response: {res}")

        # Item in Chest
        chest_pos = "0 100 1"
        docker_rcon(container_name, args.rcon_password, f"setblock {chest_pos} chest")
        res = docker_rcon(container_name, args.rcon_password, f"data merge block {chest_pos} {{Items:[{{Slot:0b, id:\"{args.test_item}\", Count:1b}}]}}")
        if "Modified block data" in res or "data" in res.lower() or "modified" in res.lower():
            record_test("place_test_item", True)
        else:
            record_test("place_test_item", False, f"Unexpected response: {res}")

        # Save and stop
        logging.info("Saving and stopping server...")
        res = docker_rcon(container_name, args.rcon_password, "save-all flush")
        if "Saved the game" in res or "Saved" in res:
            record_test("save_all", True)
        else:
            record_test("save_all", False, f"Unexpected response: {res}")

        docker_rcon(container_name, args.rcon_password, "stop")
        
        # Wait for container to exit
        logging.info("Waiting for container to terminate naturally...")
        start = time.time()
        exited = False
        while time.time() - start < 120:
            res = run_cmd(["docker", "inspect", "-f", "{{.State.Running}}", container_name])
            if res.stdout.strip() == "false":
                exited = True
                break
            time.sleep(2)
            
        if not exited:
            record_test("graceful_shutdown", False, "Server did not exit within timeout")
            raise Exception("Server failed to stop gracefully.")
        else:
            record_test("graceful_shutdown", True)

        # Check exit code
        res = run_cmd(["docker", "inspect", "-f", "{{.State.ExitCode}}", container_name])
        exit_code = int(res.stdout.strip())
        if exit_code == 0:
            record_test("exit_code_0", True)
        else:
            record_test("exit_code_0", False, f"Non-zero exit code: {exit_code}")

        # Restart same world
        logging.info("Restarting server with same volume...")
        run_cmd(["docker", "start", container_name])
        
        wait_for_rcon(container_name, args.rcon_password, timeout=300)
        
        # Assert persistence
        # Block
        block_res = docker_rcon(container_name, args.rcon_password, f"execute if block {block_pos} {args.test_block} run say BLOCK_SUCCESS")
        if "BLOCK_SUCCESS" in block_res:
            record_test("persistence_block", True)
        else:
            record_test("persistence_block", False, f"Block mismatch: {block_res}")

        # Item
        item_res = docker_rcon(container_name, args.rcon_password, f"data get block {chest_pos} Items[0].id")
        if args.test_item in item_res:
            record_test("persistence_item", True)
        else:
            record_test("persistence_item", False, f"Item not found: {item_res}")

        # Nonce
        score_res = docker_rcon(container_name, args.rcon_password, "scoreboard players get test_run mvt_nonce")
        if str(int(nonce, 16) % 1000000) in score_res:
            record_test("persistence_nonce", True)
        else:
            record_test("persistence_nonce", False, f"Nonce mismatch: {score_res}")

    except Exception as e:
        logging.error(f"Fatal error during execution: {e}")
        record_test("execution_fatal_error", False, str(e))
    finally:
        # Collect logs
        logging.info("Collecting container logs...")
        try:
            logs = run_cmd(["docker", "logs", container_name], check=False, capture_output=True).stdout
            with open(os.path.join(args.output, "server.log"), "w") as f:
                f.write(logs)
        except Exception as e:
            logging.error(f"Failed to collect logs: {e}")
            
        # Write results
        with open(os.path.join(args.output, "results.json"), "w") as f:
            json.dump([{"name": n, "success": s, "error": e} for n, s, e in test_cases], f, indent=2)
            
        write_junit(test_cases, os.path.join(args.output, "junit.xml"))

        # Cleanup
        logging.info("Cleaning up Docker resources...")
        run_cmd(["docker", "rm", "-f", container_name], check=False)
        run_cmd(["docker", "volume", "rm", "-f", volume_name], check=False)
        
        failures = [t for t in test_cases if not t[1]]
        if failures:
            logging.error(f"Completed with {len(failures)} failures.")
            sys.exit(1)
        else:
            logging.info("All tests passed!")
            sys.exit(0)

if __name__ == "__main__":
    main()
