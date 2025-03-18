#!/usr/bin/env python3
"""
----------------------------------------------------------------------------------------------------
Script Name: kismet_logger.py
Version: 1.0
Author: Michael Du Preez
Date: 2025-03-18

Overview:
    This script combines launching and managing kismet (including GPS checking, cleanup, and process
    termination) with real‐time monitoring of a kismet-generated .wiglecsv file. Every iteration
    (approximately once per second) the script reads the latest .wiglecsv file, processes its contents,
    updates an in-memory dictionary (keeping each MAC address unique with its strongest RSSI), and writes
    a timestamped output file (ending with ".kismet") if an update occurs. A summary log is printed only
    every N iterations (default every 10 iterations) to reduce logging volume.

Usage:
    python3 kismet_logger.py --log-dir ~/wardrivelog --log-level DEBUG --iteration-interval 10

Prerequisites:
    - Python 3.x must be installed.
    - Kismet must be installed and available in your system's PATH.
    - The gps_logger.py script must exist in the same directory and be executable.
    - A kismet-generated .wiglecsv file must be present in the specified log directory.
----------------------------------------------------------------------------------------------------
"""

import os                      # File system operations.
import sys                     # System-specific functions.
import csv                     # CSV processing.
import logging                 # Logging.
import argparse                # Command-line argument parsing.
import time                    # Time-related functions.
import subprocess              # Running shell commands.
import signal                  # Signal handling.
import shutil                  # High-level file operations.
from datetime import datetime  # Date/time formatting.
import threading               # Threading.

# ANSI color codes for green and reset.
GREEN = "\x1b[32m"
RESET = "\x1b[0m"

# CSV header constants.
HEADER_TOP = (
    "WigleWifi-1.4,appRelease=Kismet202307R1,model=Kismet202307R1,release=2023.07.R1,"
    "device=kismet,display=kismet,board=kismet,brand=kismet"
)
HEADER_COLUMNS = (
    "MAC,SSID,AuthMode,FirstSeen,Channel,RSSI,CurrentLatitude,CurrentLongitude,AltitudeMeters,AccuracyMeters,Type"
)

# Global variables.
best_entries = {}         # Dictionary to store the strongest entry per MAC.
output_filename = ""      # Full path to the output file (set in main()).

# =============================================================================
# WiFi Logger Functions
# =============================================================================
def get_timestamped_filename(output_dir: str) -> str:
    """
    Generate a unique timestamped filename ending with .kismet in the specified directory.
    """
    now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    return os.path.join(output_dir, f"{now}.kismet")

def parse_wiglecsv_row(row: list) -> dict:
    """
    Convert a CSV row (list of strings) into a dictionary using HEADER_COLUMNS as keys.
    """
    columns = HEADER_COLUMNS.split(",")
    return dict(zip(columns, row))

def update_best_entries(new_entries: list) -> bool:
    """
    Update the global best_entries dictionary with new data.
    Only add/update an entry if the new RSSI is stronger.
    Returns True if any updates occurred.
    """
    updated = False
    global best_entries
    for row in new_entries:
        try:
            entry = parse_wiglecsv_row(row)
            mac = entry["MAC"]
            new_rssi = float(entry["RSSI"])
            if mac not in best_entries:
                best_entries[mac] = entry
                updated = True
            else:
                current_rssi = float(best_entries[mac]["RSSI"])
                if new_rssi > current_rssi:
                    best_entries[mac] = entry
                    updated = True
        except Exception as e:
            logging.error(f"Error processing row {row}: {e}", exc_info=True)
    return updated

def write_output_file(filename: str) -> None:
    """
    Write the best_entries dictionary to a CSV file.
    The entries are sorted by the "FirstSeen" timestamp (oldest to newest).
    """
    try:
        def sort_key(item):
            entry = item[1]
            try:
                return datetime.strptime(entry["FirstSeen"], "%Y-%m-%d %H:%M:%S")
            except Exception as e:
                logging.error(f"Error parsing date for entry {entry}: {e}", exc_info=True)
                return datetime.max

        sorted_entries = sorted(best_entries.items(), key=sort_key)
        with open(filename, "w", newline='', encoding="utf-8") as f:
            f.write(HEADER_TOP + "\n")
            f.write(HEADER_COLUMNS + "\n")
            writer = csv.writer(f)
            for mac, row_data in sorted_entries:
                writer.writerow([row_data.get(col, "") for col in HEADER_COLUMNS.split(",")])
    except Exception as e:
        logging.error(f"Error writing output file {filename}: {e}", exc_info=True)

def read_new_entries(filepath: str) -> list:
    """
    Read CSV rows (ignoring the first two header lines) from the given .wiglecsv file.
    Returns a list of rows (each row is a list of strings).
    """
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            lines = f.readlines()
            return [line.strip().split(",") for line in lines[2:] if line.strip()]
    except Exception as e:
        logging.error(f"Error reading file {filepath}: {e}", exc_info=True)
        return []

def get_latest_wiglecsv(log_dir: str) -> str:
    """
    Return the full path of the most recently modified .wiglecsv file in the log_dir.
    Returns an empty string if none exist.
    """
    try:
        files = [os.path.join(log_dir, f) for f in os.listdir(log_dir) if f.endswith(".wiglecsv")]
        if not files:
            return ""
        return max(files, key=os.path.getmtime)
    except Exception as e:
        logging.error(f"Error scanning directory {log_dir} for .wiglecsv files: {e}", exc_info=True)
        return ""

# =============================================================================
# Kismet Launcher Functions and Classes
# =============================================================================
def signal_handler(sig, frame):
    """
    Handle termination signals (SIGINT, SIGTERM) for graceful shutdown.
    """
    logging.info("Shutdown signal received. Exiting...")
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

def run_command(command: str, check: bool = True, suppress_error: bool = False, return_output: bool = False):
    """
    Execute a shell command and optionally return its output.
    """
    try:
        result = subprocess.run(
            command,
            shell=True,
            check=check,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        if return_output:
            output = result.stdout.decode().strip()
            logging.debug(f"Output for command '{command}': {output}")
            return output
        logging.info(f"Command succeeded: {command}")
        return result
    except subprocess.CalledProcessError as cpe:
        if not suppress_error:
            logging.error(f"Error executing command '{command}': {cpe}", exc_info=True)
        raise

def run_gps_checker() -> None:
    """
    Run gps_logger.py and stream its output in real time.
    (Output from gps_logger.py is not prefixed by [KISMET].)
    """
    logging.info("Running gps_logger.py before launching kismet...")
    try:
        process = subprocess.Popen(
            ["python3", "-u", "gps_logger.py"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )
        while True:
            line = process.stdout.readline()
            if line == '' and process.poll() is not None:
                break
            if line:
                sys.stdout.write(line)
                sys.stdout.flush()
        retcode = process.poll()
        if retcode != 0:
            logging.error(f"gps_logger.py exited with code {retcode}.")
            sys.exit(retcode)
    except Exception as e:
        logging.error(f"Failed to run gps_logger.py: {e}", exc_info=True)
        sys.exit(1)

class KismetManager:
    """
    Manages kismet operations: ensuring log directory exists, cleaning up old files,
    killing existing kismet processes, and launching kismet in daemon mode.
    """
    def __init__(self, log_dir: str):
        self.log_dir = os.path.expanduser(log_dir)
        self.kismet_cmd = "kismet"
        self.kismet_path = shutil.which(self.kismet_cmd)
        if not self.kismet_path:
            logging.error("'kismet' command not found in PATH. Please ensure kismet is installed.")
            sys.exit(1)
        else:
            logging.info(f"Found kismet at: {self.kismet_path}")

    def ensure_log_directory(self) -> None:
        """
        Ensure the log directory exists; create it if necessary.
        """
        if not os.path.exists(self.log_dir):
            try:
                os.makedirs(self.log_dir)
                logging.info(f"Created log directory: {self.log_dir}")
            except Exception as e:
                logging.error(f"Failed to create log directory {self.log_dir}: {e}", exc_info=True)
                sys.exit(1)
        else:
            logging.info(f"Log directory already exists: {self.log_dir}")

    def cleanup_old_files(self) -> None:
        """
        Delete old kismet-generated files in the log directory.
        Files with extensions .kismet-journal and .wiglecsv will be removed.
        Files ending with .kismet (i.e. output files) will NOT be deleted.
        """
        try:
            for filename in os.listdir(self.log_dir):
                if filename.endswith('.kismet-journal') or filename.endswith('.wiglecsv'):
                    file_path = os.path.join(self.log_dir, filename)
                    if os.path.isfile(file_path):
                        os.remove(file_path)
                        logging.info(f"Deleted old file: {file_path}")
        except Exception as e:
            logging.error(f"Error cleaning up old files in {self.log_dir}: {e}", exc_info=True)

    def kill_existing_kismet(self) -> None:
        """
        Terminate any running kismet processes whose executable matches the located kismet binary.
        """
        current_pid = os.getpid()
        try:
            output = run_command("pgrep -f kismet", return_output=True, suppress_error=True)
            if output:
                pids = output.splitlines()
                logging.info(f"Found kismet processes with PIDs: {pids}")
                for pid_str in pids:
                    try:
                        pid = int(pid_str)
                        if pid == current_pid:
                            logging.debug(f"Skipping current process with PID: {pid}")
                            continue
                        try:
                            exe_path = os.readlink(f"/proc/{pid}/exe")
                        except FileNotFoundError:
                            logging.info(f"Process {pid} disappeared before inspection; skipping.")
                            continue
                        except PermissionError:
                            run_command(f"sudo kill -9 {pid}", check=True)
                            logging.info(f"Killed kismet process with PID: {pid} (permission error bypassed)")
                            continue
                        if exe_path == self.kismet_path:
                            run_command(f"sudo kill -9 {pid}", check=True)
                            logging.info(f"Killed kismet process with PID: {pid}")
                        else:
                            logging.debug(f"Skipping process {pid} with executable {exe_path}")
                    except Exception as kill_error:
                        logging.error(f"Error processing PID {pid_str}: {kill_error}", exc_info=True)
            else:
                logging.info("No running kismet processes found.")
        except Exception as e:
            logging.error(f"Error while attempting to kill kismet processes: {e}", exc_info=True)

    def launch_kismet(self) -> None:
        """
        Launch kismet in daemon mode with the specified log directory.
        """
        command = [self.kismet_path, "-p", self.log_dir, "--daemonize"]
        logging.info(f"Launching kismet with command: {' '.join(command)}")
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True
            )
            time.sleep(2)
            retcode = process.poll()
            if retcode is None:
                logging.warning("Kismet process is still running; assuming daemonization in progress.")
            elif retcode == 0:
                logging.info("Kismet launched successfully in daemon mode.")
            else:
                logging.error(f"Kismet exited with return code {retcode}.")
                sys.exit(1)
        except Exception as e:
            logging.error(f"Failed to launch kismet: {e}", exc_info=True)
            sys.exit(1)

# =============================================================================
# Main Polling Loop
# =============================================================================
def main() -> None:
    """
    Main entry point:
      1. Parse command-line arguments and configure logging with a green [KISMET] prefix.
      2. Ensure the log directory exists, kill existing kismet processes, and clean old files.
         (Note: No .kismet output file will ever be deleted.)
      3. Launch gps_logger.py in a separate thread and then launch kismet in daemon mode.
      4. Enter a polling loop (approximately once per second) that:
         - Reads the latest .wiglecsv file (if available)
         - Updates the in-memory best_entries dictionary
         - Writes the output file if updates occur
         - Logs a one-line summary only every N iterations (default every 10 iterations)
    """
    global output_filename

    parser = argparse.ArgumentParser(
        description="Launch kismet and monitor WiFi logs in real time."
    )
    parser.add_argument("--log-dir", type=str, default="~/wardrivelog",
                        help="Directory to store kismet logs (default: ~/wardrivelog)")
    parser.add_argument("--log-level", type=str, default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
                        help="Logging level (default: INFO)")
    parser.add_argument("--iteration-interval", type=int, default=10,
                        help="Log summary every N iterations (default: 10)")
    args = parser.parse_args()

    # Configure logging with a green [KISMET] prefix.
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format=f"{GREEN}[KISMET]{RESET} %(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    logging.debug("Parsed arguments: %s", args)

    # Expand log directory.
    log_dir = os.path.expanduser(args.log_dir)
    # Create the new output file in the log directory with a .kismet extension.
    output_filename = get_timestamped_filename(log_dir)

    # Initialize kismet management.
    manager = KismetManager(log_dir=log_dir)
    manager.ensure_log_directory()
    manager.kill_existing_kismet()
    # Clean up old files (this now leaves all .kismet files intact).
    manager.cleanup_old_files()

    # Start gps_logger.py in a separate daemon thread.
    gps_thread = threading.Thread(target=run_gps_checker, daemon=True)
    gps_thread.start()

    # Launch kismet in daemon mode.
    manager.launch_kismet()

    iteration = 0
    try:
        while True:
            iteration += 1
            time.sleep(1)
            wiglecsv_file = get_latest_wiglecsv(log_dir)
            if not wiglecsv_file:
                continue  # Skip iteration if no file found.
            try:
                file_size = os.path.getsize(wiglecsv_file)
            except Exception as e:
                logging.error(f"Error getting size of {wiglecsv_file}: {e}", exc_info=True)
                continue
            rows = read_new_entries(wiglecsv_file)
            parsed_count = len(rows)
            updated = update_best_entries(rows)
            if updated:
                write_output_file(output_filename)
            # Only log a summary every specified number of iterations.
            if iteration % args.iteration_interval == 0:
                logging.info(f"Iteration #{iteration}: Read {file_size} bytes, parsed {parsed_count} rows, "
                             f"best_entries: {len(best_entries)} {'(updated)' if updated else '(no change)'}")
    except KeyboardInterrupt:
        logging.info("Keyboard interrupt received. Exiting polling loop.")
    except Exception as e:
        logging.error(f"Unexpected error in polling loop: {e}", exc_info=True)
    gps_thread.join(timeout=1)
    logging.info("kismet_logger.py has exited gracefully.")

if __name__ == "__main__":
    main()
