#!/usr/bin/env python3  
# The shebang above tells the operating system to use Python 3 as the interpreter to run this script.

"""
----------------------------------------------------------------------------------------------------
Script Name: kismet_logger.py
Version: 1.0
Author: Michael Du Preez
Date: 2025-03-18

Overview:
    This script performs several tasks:
      - It launches and manages the Kismet tool, which is used for wireless network detection.
      - It monitors a Kismet-generated ".wiglecsv" file in real time (approximately once per second).
      - It reads and processes the contents of the CSV file, maintaining an in-memory dictionary 
        (called best_entries) to store only the strongest (highest RSSI) record for each unique MAC address.
      - If any update occurs, it writes an output file named "data.kismet" containing the processed data.
      - It prints a summary log every N iterations (default every 10 iterations) to avoid excessive logging.
      - It also manages GPS logging by running a separate script (gps_logger.py) in a background thread.
      - Additionally, it performs cleanup of old log files and ensures no duplicate or old processes interfere.

Usage:
    To run the script, use the following command in your terminal:
        python3 kismet_logger.py --log-dir ~/wardrivelog --log-level DEBUG --iteration-interval 10

Prerequisites:
    - Python 3.x must be installed on your system.
    - Kismet must be installed and available in the system's PATH.
    - The gps_logger.py script must exist in the same directory and be executable.
    - A Kismet-generated ".wiglecsv" file must be present in the specified log directory.
----------------------------------------------------------------------------------------------------
"""

# Import modules for interacting with the operating system and file system.
import os                      # Provides functions to interact with the operating system (e.g., file paths).
import sys                     # Provides access to system-specific parameters and functions.
import csv                     # Provides support for reading and writing CSV (Comma Separated Values) files.
import logging                 # Provides a flexible framework for emitting log messages from Python programs.
import argparse                # Facilitates parsing command-line arguments passed to the script.
import time                    # Provides various time-related functions (e.g., sleep, time stamps).
import subprocess              # Allows you to spawn new processes, connect to their input/output/error pipes, and obtain their return codes.
import signal                  # Provides mechanisms to use signal handlers in Python (e.g., for graceful shutdown).
import shutil                  # Offers a number of high-level operations on files and collections of files.
from datetime import datetime  # Provides classes for manipulating dates and times.
import threading               # Allows the program to run multiple threads concurrently.

# Define ANSI color codes for output formatting.
GREEN = "\x1b[32m"  # ANSI escape code for green text.
RESET = "\x1b[0m"   # ANSI escape code to reset text formatting back to default.

# Define constants for the CSV header lines.
HEADER_TOP = (
    "WigleWifi-1.4,appRelease=Kismet202307R1,model=Kismet202307R1,release=2023.07.R1,"
    "device=kismet,display=kismet,board=kismet,brand=kismet"
)  # First header line used by Kismet.
HEADER_COLUMNS = (
    "MAC,SSID,AuthMode,FirstSeen,Channel,RSSI,CurrentLatitude,CurrentLongitude,AltitudeMeters,AccuracyMeters,Type"
)  # Column headers that define the fields of the CSV file.

# Global variables to store processed data and the output file path.
best_entries = {}         # Dictionary to store the strongest (highest RSSI) entry per unique MAC address.
output_filename = ""      # String to hold the full path to the output file; will be set in the main() function.

# =============================================================================
# WiFi Logger Functions
# =============================================================================

def get_timestamped_filename(output_dir: str) -> str:
    """
    Return the full path for the output file always named "data.kismet" in the specified directory.
    
    Parameters:
        output_dir (str): The directory where the output file will be stored.
        
    Returns:
        str: The full file path to "data.kismet".
    """
    # Instead of using a timestamp, we always return "data.kismet" as the file name.
    return os.path.join(output_dir, "data.kismet")

def parse_wiglecsv_row(row: list) -> dict:
    """
    Convert a CSV row (a list of strings) into a dictionary using HEADER_COLUMNS as keys.
    
    Parameters:
        row (list): A list of strings representing one row from the CSV file.
        
    Returns:
        dict: A dictionary that maps each column header to its corresponding value from the row.
    """
    # Split the HEADER_COLUMNS string by commas to get a list of column names.
    columns = HEADER_COLUMNS.split(",")
    # Pair each column name with the corresponding value from the row and convert it into a dictionary.
    return dict(zip(columns, row))

def update_best_entries(new_entries: list) -> bool:
    """
    Update the global best_entries dictionary with new data from the CSV file.
    Only add or update an entry if the new RSSI value is stronger than the existing one.
    
    Parameters:
        new_entries (list): A list of CSV rows, where each row is a list of strings.
        
    Returns:
        bool: True if any entries were updated, False otherwise.
    """
    updated = False  # Initialize a flag to track if any updates have occurred.
    global best_entries  # Declare that we are using the global best_entries dictionary.
    # Loop through each new row in the list.
    for row in new_entries:
        try:
            # Convert the CSV row into a dictionary using the parse_wiglecsv_row function.
            entry = parse_wiglecsv_row(row)
            mac = entry["MAC"]  # Extract the MAC address from the entry.
            new_rssi = float(entry["RSSI"])  # Convert the RSSI value from string to float.
            # If the MAC address is not in best_entries, add the new entry.
            if mac not in best_entries:
                best_entries[mac] = entry  # Store the entry in the dictionary using MAC as the key.
                updated = True  # Set the flag to True indicating an update occurred.
            else:
                # If the MAC address is already in best_entries, compare the new RSSI with the existing one.
                current_rssi = float(best_entries[mac]["RSSI"])
                if new_rssi > current_rssi:  # Update only if the new RSSI is stronger.
                    best_entries[mac] = entry  # Replace the existing entry with the new one.
                    updated = True  # Set the flag to True indicating an update occurred.
        except Exception as e:
            # Log an error if there is an issue processing the current row.
            logging.error(f"Error processing row {row}: {e}", exc_info=True)
    # Return whether any updates were made.
    return updated

def write_output_file(filename: str) -> None:
    """
    Write the best_entries dictionary to a CSV file.
    The entries are sorted by the "FirstSeen" timestamp from oldest to newest.
    
    Parameters:
        filename (str): The full path to the output file where data will be written.
    """
    try:
        # Define a helper function to sort the dictionary items by the "FirstSeen" timestamp.
        def sort_key(item):
            entry = item[1]  # Extract the dictionary for the current entry.
            try:
                # Convert the "FirstSeen" string into a datetime object for proper sorting.
                return datetime.strptime(entry["FirstSeen"], "%Y-%m-%d %H:%M:%S")
            except Exception as e:
                # Log an error if there is a problem parsing the date and return a maximum datetime.
                logging.error(f"Error parsing date for entry {entry}: {e}", exc_info=True)
                return datetime.max

        # Sort the items in best_entries using the sort_key function.
        sorted_entries = sorted(best_entries.items(), key=sort_key)
        # Open the output file for writing; newline='' ensures proper CSV formatting.
        with open(filename, "w", newline='', encoding="utf-8") as f:
            f.write(HEADER_TOP + "\n")       # Write the first header line to the file.
            f.write(HEADER_COLUMNS + "\n")   # Write the column headers to the file.
            writer = csv.writer(f)           # Create a CSV writer object.
            # Loop through each sorted entry and write its values to the CSV file.
            for mac, row_data in sorted_entries:
                # Write a row by extracting values for each column; defaulting to an empty string if missing.
                writer.writerow([row_data.get(col, "") for col in HEADER_COLUMNS.split(",")])
    except Exception as e:
        # Log an error if there is any issue writing the output file.
        logging.error(f"Error writing output file {filename}: {e}", exc_info=True)

def read_new_entries(filepath: str) -> list:
    """
    Read CSV rows from the specified .wiglecsv file, ignoring the first two header lines.
    
    Parameters:
        filepath (str): The full path to the .wiglecsv file.
        
    Returns:
        list: A list of rows, where each row is represented as a list of strings.
    """
    try:
        # Open the .wiglecsv file for reading using UTF-8 encoding.
        with open(filepath, "r", encoding="utf-8") as f:
            lines = f.readlines()  # Read all lines from the file.
            # Process and return the lines starting from the third line (skipping headers), splitting by commas.
            return [line.strip().split(",") for line in lines[2:] if line.strip()]
    except Exception as e:
        # Log an error if there is an issue reading the file.
        logging.error(f"Error reading file {filepath}: {e}", exc_info=True)
        return []  # Return an empty list if reading fails.

def get_latest_wiglecsv(log_dir: str) -> str:
    """
    Find and return the full path of the most recently modified .wiglecsv file in the specified log directory.
    
    Parameters:
        log_dir (str): The directory to search for .wiglecsv files.
        
    Returns:
        str: The full path to the most recently modified .wiglecsv file, or an empty string if none exist.
    """
    try:
        # List all files in the directory that end with ".wiglecsv" and create their full paths.
        files = [os.path.join(log_dir, f) for f in os.listdir(log_dir) if f.endswith(".wiglecsv")]
        if not files:  # If there are no such files, return an empty string.
            return ""
        # Return the file with the most recent modification time.
        return max(files, key=os.path.getmtime)
    except Exception as e:
        # Log an error if scanning the directory fails.
        logging.error(f"Error scanning directory {log_dir} for .wiglecsv files: {e}", exc_info=True)
        return ""

# =============================================================================
# Kismet Launcher Functions and Classes
# =============================================================================

def signal_handler(sig, frame):
    """
    Handle termination signals (SIGINT, SIGTERM) to perform a graceful shutdown.
    
    Parameters:
        sig: The signal number.
        frame: The current stack frame (not used in this handler).
    """
    logging.info("Shutdown signal received. Exiting...")  # Log that a shutdown signal was received.
    sys.exit(0)  # Exit the program.

# Register the signal_handler for SIGINT (Ctrl+C) and SIGTERM.
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

def run_command(command: str, check: bool = True, suppress_error: bool = False, return_output: bool = False):
    """
    Execute a shell command and optionally return its output.
    
    Parameters:
        command (str): The shell command to execute.
        check (bool): If True, raise an exception if the command fails.
        suppress_error (bool): If True, do not log error details.
        return_output (bool): If True, return the output of the command.
        
    Returns:
        The result of the subprocess.run call, or the command output if return_output is True.
    """
    try:
        # Run the command in the shell, capturing both stdout and stderr.
        result = subprocess.run(
            command,
            shell=True,                      # Execute the command through the shell.
            check=check,                     # Check for errors if check is True.
            stdout=subprocess.PIPE,          # Capture standard output.
            stderr=subprocess.PIPE           # Capture standard error.
        )
        if return_output:
            # Decode and strip the output from stdout.
            output = result.stdout.decode().strip()
            logging.debug(f"Output for command '{command}': {output}")
            return output  # Return the decoded output.
        logging.info(f"Command succeeded: {command}")  # Log that the command succeeded.
        return result  # Return the complete result object.
    except subprocess.CalledProcessError as cpe:
        if not suppress_error:
            # Log the error details if errors are not suppressed.
            logging.error(f"Error executing command '{command}': {cpe}", exc_info=True)
        raise  # Re-raise the exception to indicate failure.

def run_gps_checker() -> None:
    """
    Run the gps_logger.py script and stream its output in real time.
    This function waits for gps_logger.py to complete and exits if it fails.
    """
    logging.info("Running gps_logger.py before launching kismet...")  # Log that gps_logger.py is starting.
    try:
        # Start the gps_logger.py process with unbuffered output.
        process = subprocess.Popen(
            ["python3", "-u", "gps_logger.py"],  # Command to run gps_logger.py.
            stdout=subprocess.PIPE,               # Capture the standard output.
            stderr=subprocess.STDOUT,             # Merge standard error into standard output.
            text=True                             # Treat the output as text (not bytes).
        )
        # Read the output of gps_logger.py line-by-line.
        while True:
            line = process.stdout.readline()     # Read one line from the process output.
            if line == '' and process.poll() is not None:
                break  # Exit the loop if the process has finished and no more output is available.
            if line:
                sys.stdout.write(line)           # Write the line to the standard output.
                sys.stdout.flush()               # Flush the output buffer immediately.
        retcode = process.poll()                 # Get the return code of the gps_logger.py process.
        if retcode != 0:
            # Log an error if gps_logger.py exited with a non-zero code and exit the program.
            logging.error(f"gps_logger.py exited with code {retcode}.")
            sys.exit(retcode)
    except Exception as e:
        # Log an error if gps_logger.py fails to run and exit the program.
        logging.error(f"Failed to run gps_logger.py: {e}", exc_info=True)
        sys.exit(1)

class KismetManager:
    """
    Manages Kismet operations including ensuring the log directory exists,
    cleaning up old files, killing existing Kismet processes, and launching Kismet in daemon mode.
    """
    def __init__(self, log_dir: str):
        """
        Initialize the KismetManager with the given log directory.
        
        Parameters:
            log_dir (str): The directory where Kismet logs will be stored.
        """
        self.log_dir = os.path.expanduser(log_dir)  # Expand the '~' to the user's home directory.
        self.kismet_cmd = "kismet"                  # Set the command name for Kismet.
        self.kismet_path = shutil.which(self.kismet_cmd)  # Locate the full path of the Kismet executable.
        if not self.kismet_path:
            # Log an error and exit if the Kismet command is not found in the PATH.
            logging.error("'kismet' command not found in PATH. Please ensure kismet is installed.")
            sys.exit(1)
        else:
            # Log the path where Kismet was found.
            logging.info(f"Found kismet at: {self.kismet_path}")

    def ensure_log_directory(self) -> None:
        """
        Ensure that the log directory exists; if not, create it.
        """
        if not os.path.exists(self.log_dir):
            try:
                os.makedirs(self.log_dir)  # Create the log directory.
                logging.info(f"Created log directory: {self.log_dir}")
            except Exception as e:
                # Log an error and exit if the directory cannot be created.
                logging.error(f"Failed to create log directory {self.log_dir}: {e}", exc_info=True)
                sys.exit(1)
        else:
            # Log that the log directory already exists.
            logging.info(f"Log directory already exists: {self.log_dir}")

    def cleanup_old_files(self) -> None:
        """
        Delete old Kismet-generated files in the log directory.
        This function removes files with extensions .kismet-journal and .wiglecsv,
        but leaves files ending with .kismet (i.e. output files) intact.
        """
        try:
            for filename in os.listdir(self.log_dir):  # Loop through each file in the log directory.
                if filename.endswith('.kismet-journal') or filename.endswith('.wiglecsv'):
                    file_path = os.path.join(self.log_dir, filename)  # Get the full path of the file.
                    if os.path.isfile(file_path):
                        os.remove(file_path)  # Remove the file.
                        logging.info(f"Deleted old file: {file_path}")  # Log that the file was deleted.
        except Exception as e:
            # Log an error if there is an issue cleaning up the files.
            logging.error(f"Error cleaning up old files in {self.log_dir}: {e}", exc_info=True)

    def kill_existing_kismet(self) -> None:
        """
        Terminate any running Kismet processes that match the located Kismet executable.
        This avoids conflicts with previously running instances.
        """
        current_pid = os.getpid()  # Get the current process ID.
        try:
            # Run the "pgrep" command to find process IDs of running Kismet processes.
            output = run_command("pgrep -f kismet", return_output=True, suppress_error=True)
            if output:
                pids = output.splitlines()  # Split the output into individual process IDs.
                logging.info(f"Found kismet processes with PIDs: {pids}")
                for pid_str in pids:  # Process each PID.
                    try:
                        pid = int(pid_str)  # Convert the PID from string to integer.
                        if pid == current_pid:
                            logging.debug(f"Skipping current process with PID: {pid}")  # Do not kill the current process.
                            continue
                        try:
                            # Attempt to get the executable path for the process.
                            exe_path = os.readlink(f"/proc/{pid}/exe")
                        except FileNotFoundError:
                            logging.info(f"Process {pid} disappeared before inspection; skipping.")
                            continue
                        except PermissionError:
                            # If permission is denied, force kill the process.
                            run_command(f"sudo kill -9 {pid}", check=True)
                            logging.info(f"Killed kismet process with PID: {pid} (permission error bypassed)")
                            continue
                        if exe_path == self.kismet_path:
                            # If the executable path matches the located Kismet executable, kill the process.
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
        Launch Kismet in daemon mode with the specified log directory.
        Daemon mode runs Kismet in the background.
        """
        # Build the command list to launch Kismet with the log directory and daemonization flag.
        command = [self.kismet_path, "-p", self.log_dir, "--daemonize"]
        logging.info(f"Launching kismet with command: {' '.join(command)}")
        try:
            # Launch Kismet as a subprocess, discarding its output.
            process = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,  # Discard standard output.
                stderr=subprocess.DEVNULL,  # Discard standard error.
                start_new_session=True       # Start in a new session to detach from the current terminal.
            )
            time.sleep(2)  # Wait briefly to allow Kismet to start.
            retcode = process.poll()  # Check if the process has terminated.
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
    The main function orchestrates the following steps:
      1. Parse command-line arguments and configure logging with a green [KISMET] prefix.
      2. Ensure the log directory exists, kill existing Kismet processes, and clean up old files.
         (Note: Output files ending with .kismet are preserved.)
      3. Start the gps_logger.py script in a separate daemon thread.
      4. Launch Kismet in daemon mode.
      5. Enter a polling loop that, once per second:
         - Reads the latest .wiglecsv file.
         - Updates the in-memory best_entries dictionary.
         - Writes the output file if any updates occur.
         - Logs a summary every N iterations (default every 10 iterations).
    """
    global output_filename  # Declare that we will modify the global output_filename variable.

    # Set up argument parsing for command-line options.
    parser = argparse.ArgumentParser(
        description="Launch kismet and monitor WiFi logs in real time."
    )
    # Define the --log-dir argument with a default directory.
    parser.add_argument("--log-dir", type=str, default="~/wardrivelog",
                        help="Directory to store kismet logs (default: ~/wardrivelog)")
    # Define the --log-level argument with choices for logging detail.
    parser.add_argument("--log-level", type=str, default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
                        help="Logging level (default: INFO)")
    # Define the --iteration-interval argument to control how often a summary log is printed.
    parser.add_argument("--iteration-interval", type=int, default=10,
                        help="Log summary every N iterations (default: 10)")
    args = parser.parse_args()  # Parse the command-line arguments provided by the user.

    # Configure logging with a green [KISMET] prefix and the specified log level.
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),  # Set the logging level.
        format=f"{GREEN}[KISMET]{RESET} %(asctime)s [%(levelname)s] %(message)s",  # Define the format for log messages.
        datefmt="%Y-%m-%d %H:%M:%S"  # Define the date and time format for log messages.
    )
    logging.debug("Parsed arguments: %s", args)  # Log the parsed arguments at DEBUG level.

    # Expand the log directory path (e.g., convert "~" to the full home directory path).
    log_dir = os.path.expanduser(args.log_dir)
    # Create the output filename using get_timestamped_filename which now returns "data.kismet".
    output_filename = get_timestamped_filename(log_dir)

    # Initialize the KismetManager with the specified log directory.
    manager = KismetManager(log_dir=log_dir)
    # Ensure that the log directory exists; create it if it does not.
    manager.ensure_log_directory()
    # Kill any existing Kismet processes to avoid conflicts.
    manager.kill_existing_kismet()
    # Clean up old Kismet-generated files (this preserves output files ending with .kismet).
    manager.cleanup_old_files()

    # Start the gps_logger.py script in a separate daemon thread to run concurrently.
    gps_thread = threading.Thread(target=run_gps_checker, daemon=True)
    gps_thread.start()

    # Launch Kismet in daemon mode.
    manager.launch_kismet()

    iteration = 0  # Initialize an iteration counter.
    try:
        # Begin an infinite loop to poll for new data approximately once per second.
        while True:
            iteration += 1  # Increment the iteration counter.
            time.sleep(1)   # Wait for 1 second between iterations.
            # Retrieve the most recently modified .wiglecsv file from the log directory.
            wiglecsv_file = get_latest_wiglecsv(log_dir)
            if not wiglecsv_file:
                continue  # If no .wiglecsv file is found, skip this iteration.
            try:
                # Get the size of the .wiglecsv file (in bytes).
                file_size = os.path.getsize(wiglecsv_file)
            except Exception as e:
                # Log an error if unable to get the file size and skip this iteration.
                logging.error(f"Error getting size of {wiglecsv_file}: {e}", exc_info=True)
                continue
            # Read new CSV entries from the .wiglecsv file.
            rows = read_new_entries(wiglecsv_file)
            parsed_count = len(rows)  # Count the number of rows parsed.
            # Update the best_entries dictionary with the new rows; 'updated' is True if changes occurred.
            updated = update_best_entries(rows)
            if updated:
                # If there are updates, write the best_entries data to the output file ("data.kismet").
                write_output_file(output_filename)
            # Log a summary every N iterations, where N is defined by the --iteration-interval argument.
            if iteration % args.iteration_interval == 0:
                logging.info(f"Iteration #{iteration}: Read {file_size} bytes, parsed {parsed_count} rows, "
                             f"best_entries: {len(best_entries)} {'(updated)' if updated else '(no change)'}")
    except KeyboardInterrupt:
        # If a keyboard interrupt (Ctrl+C) occurs, log the event and exit the polling loop.
        logging.info("Keyboard interrupt received. Exiting polling loop.")
    except Exception as e:
        # Log any unexpected errors that occur during the polling loop.
        logging.error(f"Unexpected error in polling loop: {e}", exc_info=True)
    # Wait for the gps_logger thread to finish (with a timeout of 1 second).
    gps_thread.join(timeout=1)
    # Log that the script is exiting gracefully.
    logging.info("kismet_logger.py has exited gracefully.")

# This block ensures that the main() function is executed only when the script is run directly,
# and not when it is imported as a module in another script.
if __name__ == "__main__":
    main()  # Call the main() function to start the script.
