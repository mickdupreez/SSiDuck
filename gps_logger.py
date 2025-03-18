#!/usr/bin/env python3
# This line tells the system to use Python 3 to run this script.
"""
----------------------------------------------------------------------------------------------------
Script Name: gps_logger.py
Version: 1.0
Author: Michael Du Preez
Date: 2025-03-16

Overview:
    This script sets up and monitors a virtual GPS device that is created from UDP data via the
    'socat' utility. It uses a JSON settings file to obtain the UDP IP and Port, then ensures that
    a virtual device (such as /dev/ttyGPS0) exists and is receiving live GPS data.

Functionality:
    - Loads configuration (UDP IP and port) from a JSON settings file.
    - Uses two helper classes:
         • SocatManager: Manages socat processes (checks, kills, and starts them).
         • GPSDeviceManager: Checks for the device, reads data, and verifies that GPS data flows.
    - If the device is present and data is flowing, no further action is taken.
    - Otherwise, any existing socat processes are terminated, and new ones are started with retries.
    - Permissions are set on the device and data flow is verified.
    - A built-in test suite is available with the '--test' flag.

Prerequisites:
    - A valid JSON settings file (default: settings.json) with keys "udp_ip" and "udp_port".
    - 'socat' must be installed and accessible (sudo privileges may be required).
    - Python 3.x is required.
----------------------------------------------------------------------------------------------------
"""

# =============================================================================
# IMPORTS
# =============================================================================
import subprocess    # To execute shell commands (e.g., for socat and killing processes).  # Import the module used to run external commands (shell commands).
import time          # For implementing delays and handling timing operations.  # Module providing functions related to time, like delays.
import os            # For operating system interactions, such as reading device files.
# Module to interact with the operating system, e.g., checking if files exist.
import json          # To parse the JSON settings file.
# Module to load and read settings from a JSON file.
import fcntl         # To set file descriptors to non-blocking mode.
# Module to set the device file in a non-blocking (asynchronous) read mode.
import logging       # To log informational, debugging, and error messages.
# Module for detailed logging and debugging.
import argparse      # To parse command-line arguments.
# Module for handling command-line arguments passed to the script.
import signal        # For handling OS signals (SIGINT, SIGTERM) gracefully.
# Module for catching signals (like Ctrl+C) to shut down cleanly.
import sys           # For system-specific functions like exiting.
# Module that provides functions to interact closely with the system.
from typing import Optional, Dict  # For adding type hints.
# This allows specifying detailed types for clearer code.
from pathlib import Path  # For robust file existence checks.
# Simplifies operations with file and directory paths.
import tempfile      # For creating temporary files during tests.
# Used to handle temporary files during tests.

# =============================================================================
# CONSTANTS & GLOBALS
# =============================================================================
DEFAULT_SETTINGS_FILE = "settings.json"  # Default JSON file for settings.  # This is the default JSON settings file the script looks for.
DEFAULT_DEVICE = "/dev/ttyGPS0"          # Default virtual GPS device.  # Default file path for the virtual GPS device created by socat.
DEFAULT_READ_DURATION = 2                # Duration (in seconds) to attempt reading data.  # How many seconds the script spends checking GPS data each time.
DEFAULT_DELAY = 5                        # Delay (in seconds) between data flow checks.  # Delay between two consecutive GPS checks.
DEFAULT_SOCAT_ATTEMPTS = 10              # Maximum attempts to start socat.  # How many times the script tries to start the GPS service before giving up.
DEFAULT_FLOW_ATTEMPTS = 10               # Maximum attempts to verify GPS data flow.  # Number of attempts to verify that GPS data is actively being received.

# Global flag to indicate that a shutdown has been requested (e.g., via Ctrl+C)
shutdown_requested = False
# Indicates whether the script is shutting down (e.g., Ctrl+C pressed).

# =============================================================================
# SIGNAL HANDLING
# =============================================================================
def signal_handler(sig, frame):
# Function to handle signals (such as Ctrl+C) gracefully.
    """
    Handle termination signals (SIGINT, SIGTERM) to perform a graceful shutdown.

    - Sets a global shutdown flag.
    - Kills any running socat processes.
    - Exits the program.

    Args:
        sig: Signal number.
        frame: Current stack frame (unused).
    """
    global shutdown_requested
    logging.info("Shutdown signal received. Initiating cleanup...")
    shutdown_requested = True  # Notify the program to exit any running loops.
    SocatManager.kill_global()  # Kill any socat processes (using a class method).
    sys.exit(0)               # Exit the script cleanly.

# Register the signal handler for SIGINT (Ctrl+C) and SIGTERM.
signal.signal(signal.SIGINT, signal_handler)  # Registers the above signal handler for graceful exit.
signal.signal(signal.SIGTERM, signal_handler)  # Registers the above signal handler for graceful exit.

# =============================================================================
# UTILITY FUNCTION FOR RUNNING SHELL COMMANDS
# =============================================================================
def run_command(command: str, check: bool = True, suppress_error: bool = False, return_output: bool = False) -> Optional[subprocess.CompletedProcess]:
# Function to run shell commands safely and handle outputs or errors.
    """
    Execute a shell command and optionally return its output.

    Args:
        command (str): The shell command to execute.
        check (bool): Raise an exception if the command exits non-zero.
        suppress_error (bool): If True, suppress error messages.
        return_output (bool): If True, return the command's stdout as a string.

    Returns:
        subprocess.CompletedProcess: If not returning output.
        str: The stdout output if return_output is True.
        None: If an error occurs and is suppressed.
    """
    try:
        result = subprocess.run(
            command,
            shell=True,
            check=check,
            stdout=subprocess.PIPE,  # Capture standard output.
            stderr=subprocess.PIPE   # Capture standard error.
        )
        if return_output:
            output = result.stdout.decode().strip()  # Decode the output.
            logging.debug(f"Command output for '{command}': {output}")
            return output
        if result.returncode == 0:
            if not suppress_error:
                logging.info(f"Command succeeded: {command}")
        else:
            if not suppress_error:
                logging.error(f"Command failed ({command}): {result.stderr.decode().strip()}")
        return result
    except subprocess.CalledProcessError as cpe:
        if not suppress_error:
            logging.error(f"Exception while executing command '{command}': {cpe}", exc_info=True)
        return None

# =============================================================================
# CLASS: SocatManager
# =============================================================================
class SocatManager:
# Manages 'socat', which makes UDP data appear as a GPS device file.
    """
    Class to manage socat processes which create the virtual GPS device.

    Methods:
        - is_running: Check if a socat process is running.
        - kill: Kill all running socat processes.
        - start: Start a new socat process with given UDP parameters.
    """
    def __init__(self, device: str, udp_ip: str, udp_port: int):
        """
        Initialize the SocatManager with the device and UDP configuration.

        Args:
            device (str): The virtual GPS device path.
            udp_ip (str): The UDP IP address to bind.
            udp_port (int): The UDP port to listen on.
        """
        self.device = device
        self.udp_ip = udp_ip
        self.udp_port = udp_port

    def is_running(self) -> bool:
        """
        Check if any socat process is currently running.

        Returns:
            bool: True if a socat process is found, False otherwise.
        """
        result = run_command("ps aux | grep '[s]ocat'", return_output=True, suppress_error=True)
        if result:
            for line in result.splitlines():
                if "socat" in line and "grep" not in line:
                    logging.debug(f"socat process found: {line}")
                    return True
        return False

    @staticmethod
    def kill_global() -> None:
        """
        Kill all running socat processes using pkill.
        """
        if run_command("ps aux | grep '[s]ocat'", return_output=True, suppress_error=True):
            run_command("sudo pkill -f socat", check=False, suppress_error=True)
            time.sleep(1)  # Allow time for termination.
            logging.info("Killed existing socat process(es).")
        else:
            logging.info("No existing socat process found to kill.")

    def kill(self) -> None:
        """
        Kill socat processes using the static method.
        """
        SocatManager.kill_global()

    def start(self) -> bool:
        """
        Start a socat process that creates a virtual GPS device.

        Returns:
            bool: True if the socat process is running after the attempt, False otherwise.
        """
        # Construct the socat command with the UDP parameters.
        cmd = (
            f"sudo socat -d -d pty,raw,echo=0,link={self.device} "
            f"UDP4-RECV:{self.udp_port},bind={self.udp_ip} > /dev/null 2>&1 &"
        )
        run_command(cmd, check=False, suppress_error=True)
        time.sleep(1)  # Wait briefly to allow socat to initialize.
        return self.is_running()

# =============================================================================
# CLASS: GPSDeviceManager
# =============================================================================
class GPSDeviceManager:
# Manages and checks the virtual GPS device, ensuring GPS data is flowing.
    """
    Class to handle operations on the virtual GPS device.

    Methods:
        - exists: Check if the device file exists.
        - get_last_line: Read data from the device and return the last line.
        - is_data_flowing: Determine if new data is arriving from the device.
    """
    def __init__(self, device: str, read_duration: int = DEFAULT_READ_DURATION):  # How many seconds the script spends checking GPS data each time.
        """
        Initialize the GPSDeviceManager.

        Args:
            device (str): The path to the virtual GPS device.
            read_duration (int): Time in seconds to read data per attempt.
        """
        self.device = device
        self.read_duration = read_duration

    def exists(self) -> bool:
        """
        Check if the virtual GPS device file exists.

        Returns:
            bool: True if the device exists, False otherwise.
        """
        exists = Path(self.device).exists()
        logging.debug(f"GPS device {self.device} existence check: {exists}")
        return exists

    def get_last_line(self) -> str:
        """
        Read data from the GPS device in non-blocking mode and return the last complete line.

        Returns:
            str: The last line of data read, or an empty string if no data is received.
        """
        last_line = ""
        try:
            # Open the device in binary mode with no buffering.
            with open(self.device, "rb", buffering=0) as gps_stream:
                fd = gps_stream.fileno()  # Get file descriptor.
                # Set the file descriptor to non-blocking mode.
                fcntl.fcntl(fd, fcntl.F_SETFL, os.O_NONBLOCK)
                start_time = time.time()
                while (time.time() - start_time) < self.read_duration:
                    if shutdown_requested:
                        logging.info("Shutdown requested during data read; exiting loop.")
                        break
                    try:
                        chunk = os.read(fd, 4096)
                        if chunk:
                            decoded = chunk.decode(errors="ignore")
                            lines = decoded.splitlines()
                            if lines:
                                last_line = lines[-1]
                    except BlockingIOError:
                        # No data available; continue loop.
                        pass
                    time.sleep(0.1)  # Short delay to reduce CPU load.
        except Exception as e:
            logging.error(f"Unable to read from {self.device}: {e}", exc_info=True)
        return last_line

    def is_data_flowing(self, delay: int = DEFAULT_DELAY) -> bool:  # Delay between two consecutive GPS checks.
        """
        Verify that data is actively flowing from the GPS device.

        The function captures two snapshots of data separated by a delay. If the snapshots differ,
        it is assumed that data is flowing.

        Args:
            delay (int): Time in seconds to wait between snapshots.

        Returns:
            bool: True if data flow is detected, False otherwise.
        """
        logging.info(f"Verifying GPS data flow with a delay of {delay} seconds.")
        first_line = self.get_last_line()
        logging.debug(f"First snapshot: {first_line}")
        time.sleep(delay)
        second_line = self.get_last_line()
        logging.debug(f"Second snapshot: {second_line}")
        if not second_line or first_line == second_line:
            logging.error("GPS data is not flowing (snapshots are identical or empty).")
            return False
        logging.info("GPS data is actively changing (flowing).")
        return True

# =============================================================================
# FUNCTION: load_settings
# =============================================================================
def load_settings(settings_file: str) -> Optional[Dict]:
    """
    Load configuration settings from a JSON file.

    Args:
        settings_file (str): Path to the JSON configuration file.

    Returns:
        dict: Configuration settings if loaded successfully, None otherwise.
    """
    try:
        with open(settings_file, "r") as f:
            settings = json.load(f)
        logging.debug(f"Settings loaded successfully: {settings}")
        return settings
    except FileNotFoundError as fnf_err:
        logging.error(f"Settings file not found: {fnf_err}")
    except json.JSONDecodeError as json_err:
        logging.error(f"Error decoding JSON from settings file: {json_err}")
    except Exception as e:
        logging.error(f"Unexpected error while loading settings: {e}", exc_info=True)
    return None

# =============================================================================
# CLASS: GPSSetup
# =============================================================================
class GPSSetup:
# Coordinates setup and checks of GPS device and data flow.
    """
    Class to coordinate the entire GPS device setup process.

    It loads configuration settings, verifies device existence and data flow, manages socat
    processes, and ensures that the virtual device is properly configured.
    """
    def __init__(self, settings_file: str, device: str, socat_attempts: int, flow_attempts: int):
        """
        Initialize the GPSSetup instance.

        Args:
            settings_file (str): Path to the JSON settings file.
            device (str): Path to the virtual GPS device.
            socat_attempts (int): Maximum number of attempts to start socat.
            flow_attempts (int): Maximum number of attempts to verify GPS data flow.

        Raises:
            ValueError: If the settings cannot be loaded or required keys are missing.
        """
        self.settings_file = settings_file
        self.device = device
        self.socat_attempts = socat_attempts
        self.flow_attempts = flow_attempts

        # Load configuration settings.
        self.settings = load_settings(settings_file)
        if not self.settings:
            raise ValueError("Could not load settings from the file.")
        self.udp_ip = self.settings.get("udp_ip")
        self.udp_port = self.settings.get("udp_port")
        if not self.udp_ip or not self.udp_port:
            raise ValueError("Settings file is missing 'udp_ip' or 'udp_port'.")

        # Initialize helper classes for socat and GPS device management.
        self.socat_manager = SocatManager(device=self.device, udp_ip=self.udp_ip, udp_port=self.udp_port)
        self.gps_device_manager = GPSDeviceManager(device=self.device)

    def run(self) -> None:
        """
        Run the GPS device setup process.

        Steps:
          1. Verify the device exists and that data is flowing.
          2. If data is stagnant or the device is missing, kill any running socat processes.
          3. Attempt to start socat until it runs.
          4. Set device permissions and verify GPS data flow.
        """
        logging.info(f"Using UDP IP: {self.udp_ip} and UDP Port: {self.udp_port}")

        # Check if the device exists and data is flowing.
        if self.gps_device_manager.exists():
            logging.info("GPS device found. Verifying data flow...")
            if self.gps_device_manager.is_data_flowing():
                logging.info("GPS data is flowing. No restart required.")
                return
            else:
                logging.warning("GPS device exists but data is stagnant. Restarting socat...")
        else:
            logging.info("GPS device not found. Proceeding to set up socat.")

        # Kill any running socat processes.
        self.socat_manager.kill()

        # Attempt to start socat repeatedly.
        for attempt in range(self.socat_attempts):
            if shutdown_requested:
                logging.info("Shutdown requested during socat startup attempts.")
                return
            if self.socat_manager.start():
                logging.info(f"socat started successfully after {attempt + 1} attempt(s).")
                break
        else:
            logging.error("GPS Connection failed after multiple attempts. Exiting setup.")
            return

        # If the virtual device now exists, set proper permissions.
        if self.gps_device_manager.exists():
            run_command(f"sudo chmod 666 {self.device}", check=False, suppress_error=True)
            # Verify that GPS data is flowing, retrying if necessary.
            for attempt in range(self.flow_attempts):
                if shutdown_requested:
                    logging.info("Shutdown requested during GPS data flow verification.")
                    return
                if self.gps_device_manager.is_data_flowing():
                    logging.info(f"GPS data is flowing after {attempt + 1} attempt(s).")
                    return
                logging.warning(f"GPS data is still not flowing. Retrying ({attempt + 1}/{self.flow_attempts})...")
                time.sleep(5)
            logging.error("GPS data is not flowing after multiple attempts. Please check the GPS data source.")

# =============================================================================
# COMMAND-LINE ARGUMENT PARSING
# =============================================================================
def parse_arguments() -> argparse.Namespace:
# Parses command-line arguments to customize script behavior.
    """
    Parse command-line arguments to allow overriding default configuration values.

    Returns:
        argparse.Namespace: Parsed command-line arguments.
    """
    parser = argparse.ArgumentParser(
        description="Set up and monitor a virtual GPS device using socat."
    )
    parser.add_argument("--settings", type=str, default=DEFAULT_SETTINGS_FILE,  # This is the default JSON settings file the script looks for.
                        help="Path to the settings JSON file.")
    parser.add_argument("--device", type=str, default=DEFAULT_DEVICE,  # Default file path for the virtual GPS device created by socat.
                        help="Path to the virtual GPS device.")
    parser.add_argument("--read-duration", type=int, default=DEFAULT_READ_DURATION,  # How many seconds the script spends checking GPS data each time.
                        help="Duration (in seconds) to read data from the device.")
    parser.add_argument("--delay", type=int, default=DEFAULT_DELAY,  # Delay between two consecutive GPS checks.
                        help="Delay (in seconds) between data flow checks.")
    parser.add_argument("--socat-attempts", type=int, default=DEFAULT_SOCAT_ATTEMPTS,  # How many times the script tries to start the GPS service before giving up.
                        help="Number of attempts to start socat.")
    parser.add_argument("--flow-attempts", type=int, default=DEFAULT_FLOW_ATTEMPTS,  # Number of attempts to verify that GPS data is actively being received.
                        help="Number of attempts to verify GPS data flow.")
    parser.add_argument("--log-level", type=str, default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
                        help="Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL).")
    parser.add_argument("--test", action="store_true",
                        help="Run test cases for the GPS setup functions.")
    return parser.parse_args()

# =============================================================================
# TESTING FUNCTIONALITY
# =============================================================================
def run_tests() -> None:
# Function to run internal tests, checking critical features.
    """
    Run simple test cases to validate the core functions.

    The tests include:
      1. Creating a temporary settings JSON file and testing load_settings().
      2. Verifying that gps_device_exists() returns True for a known file (e.g., '/dev/null').
    """
    logging.info("Running tests...")

    # Create a temporary settings file with sample data.
    test_settings = {"udp_ip": "127.0.0.1", "udp_port": 5000}
    with tempfile.NamedTemporaryFile(mode="w+", delete=False) as tmp:
        json.dump(test_settings, tmp)
        tmp_path = tmp.name
    logging.debug(f"Temporary settings file created at {tmp_path}")

    # Test load_settings() function.
    loaded_settings = load_settings(tmp_path)
    if loaded_settings == test_settings:
        logging.info("load_settings() test passed.")
    else:
        logging.error("load_settings() test failed.")

    # Clean up the temporary settings file.
    try:
        Path(tmp_path).unlink()
        logging.debug("Temporary settings file deleted.")
    except Exception as e:
        logging.error(f"Failed to delete temporary settings file: {e}", exc_info=True)

    # Test gps_device_exists() using '/dev/null' (commonly exists on Unix-like systems).
    gps_device_manager = GPSDeviceManager("/dev/null")
    if gps_device_manager.exists():
        logging.info("gps_device_exists() test passed for '/dev/null'.")
    else:
        logging.error("gps_device_exists() test failed for '/dev/null'.")

    logging.info("Tests completed.")

# =============================================================================
# MAIN FUNCTION
# =============================================================================
def main() -> None:
# The primary function of the script, running the overall setup.
    """
    Main entry point for the script.

    Steps:
      1. Parse command-line arguments.
      2. Configure logging.
      3. If test mode is enabled, run tests and exit.
      4. Otherwise, initialize GPSSetup and run the GPS device setup process.
      5. Handle any unexpected errors gracefully.
    """
    args = parse_arguments()

    # Define purple color code and reset code.
    PURPLE = "\x1b[35m"
    RESET = "\x1b[0m"

    # Configure logging with a purple [GPS] prefix.
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format=f"{PURPLE}[GPS]{RESET} %(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    logging.debug("Parsed arguments: %s", args)

    # If the '--test' flag is set, run tests and exit.
    if args.test:
        run_tests()
        return

    try:
        # Initialize GPSSetup with command-line arguments.
        setup = GPSSetup(
            settings_file=args.settings,
            device=args.device,
            socat_attempts=args.socat_attempts,
            flow_attempts=args.flow_attempts
        )
        # Execute the GPS device setup process.
        setup.run()
    except Exception as e:
        logging.error(f"An unexpected error occurred: {e}", exc_info=True)
        sys.exit(1)

# =============================================================================
# EXECUTION START POINT
# =============================================================================
if __name__ == "__main__":
# Ensures this script runs the 'main' function when executed directly.
    main()
