#!/usr/bin/env python3  
# The shebang above tells the operating system to use Python3 to run this script.

"""
----------------------------------------------------------------------------------------------------
Script Name: bettercap_logger.py
Version: 1.1
Author: Michael Du Preez
Date: 2025-03-17

Overview:
    This script launches the network analysis tool "bettercap" within a controlled pseudo-terminal 
    session. It sends commands to enable Bluetooth Low Energy (BLE) scanning and continuously logs 
    BLE data to a CSV file. The output file is now always named "data.bettercap" regardless of the 
    current date and time. This script captures BLE scan results, processes them, and writes them 
    to the CSV file, ensuring only the best (strongest signal) for each device is stored.

    The script performs the following tasks:
      1. Creates (or overwrites) a CSV file named "data.bettercap" in the specified log directory.
      2. Launches bettercap in a detached process so its output is not displayed in the parent terminal.
      3. Sends a command ("ble.recon on") to bettercap to start scanning for BLE devices.
      4. Enters an infinite loop where every second it:
         a. Flushes any stale output from the pseudo-terminal.
         b. Sends the "ble.show" command to retrieve current BLE scan results.
         c. Strips out ANSI color codes from the output.
         d. Extracts data from an ASCII table that includes columns for RSSI, MAC, Vendor, Flags, and Seen.
         e. Updates an in-memory dictionary with the best (strongest) RSSI for each unique MAC address.
         f. Rewrites the CSV file sorted by the "Seen" time from oldest to newest.
         g. Logs a summary every N iterations (default is every 10 iterations).
      5. Runs indefinitely until interrupted (Ctrl+C), at which point it sends a "quit" command to bettercap
         and terminates gracefully.

Usage:
    To run the script, use the following command in your terminal:
        sudo python3 bettercap_logger.py --log-level INFO --log-dir ~/wardrivelog --log-interval 10

Prerequisites:
    - Python 3.x must be installed on your system.
    - bettercap must be installed and available in your system's PATH.
    - Sudo privileges are required to run bettercap.
    - Basic knowledge of executing scripts from a terminal (though every line is well explained).
----------------------------------------------------------------------------------------------------
"""

# Import standard libraries used for various functionalities.
import os               # For interacting with the operating system (e.g., file paths, directories)
import sys              # For interacting with the Python interpreter (e.g., exiting the program)
import re               # For regular expression operations (e.g., removing ANSI color codes)
import subprocess       # For launching new processes (to start bettercap)
import time             # For handling time-related functions (e.g., delays, timestamps)
import logging          # For logging messages (info, warnings, errors)
import argparse         # For parsing command-line arguments passed to the script
import select           # For monitoring I/O streams (e.g., reading from a pseudo-terminal)
import signal           # For handling system signals (e.g., SIGINT, SIGTERM)
import fcntl            # For file control operations on file descriptors
from datetime import datetime  # For working with dates and times

# =============================================================================
# GLOBAL VARIABLES
# =============================================================================

bettercap_process = None    # Global variable to hold the process object for bettercap
pty_fd = None               # Global variable to store the file descriptor for the parent's side of the pseudo-terminal

# Dictionary to store the best scan entry for each device.
# The key is the MAC address and the value is a dictionary with columns: RSSI, MAC, Vendor, Flags, and Seen.
best_entries = {}

# =============================================================================
# SIGNAL HANDLER
# =============================================================================

def signal_handler(sig, frame):
    """
    Gracefully terminates the bettercap process when a shutdown signal (like SIGINT or SIGTERM) is received.
    
    Parameters:
        sig   : The signal number.
        frame : The current stack frame (not used but required for the handler signature).
    """
    # Log that a shutdown signal has been received.
    logging.info("Shutdown signal received. Terminating bettercap process...")
    try:
        # If the pseudo-terminal file descriptor is valid, send the "quit" command to bettercap.
        if pty_fd is not None:
            os.write(pty_fd, b"quit\n")  # Write the quit command to the pseudo-terminal.
            time.sleep(1)                # Wait briefly to allow the command to be processed.
    except Exception as e:
        # Log an error if there is an issue sending the quit command.
        logging.error("Error sending quit command: %s", e, exc_info=True)
    # Terminate the bettercap process if it is running.
    if bettercap_process:
        bettercap_process.terminate()
    # Exit the program.
    sys.exit(0)

# Register the signal handler for SIGINT (Ctrl+C) and SIGTERM.
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# =============================================================================
# FUNCTION: flush_input
# =============================================================================

def flush_input(fd, timeout=0.2):
    """
    Flushes any available data from the file descriptor to clear the input buffer.
    
    Parameters:
        fd      : The file descriptor to flush.
        timeout : The maximum time (in seconds) to wait for flushing (default is 0.2 seconds).
    """
    # Calculate the end time for flushing based on the current time plus the timeout.
    end_time = time.time() + timeout
    # Loop until the current time exceeds the end time.
    while time.time() < end_time:
        # Use select to check if the file descriptor has data available for reading.
        rlist, _, _ = select.select([fd], [], [], 0.05)
        if fd in rlist:
            try:
                # Read up to 1024 bytes from the file descriptor to clear any data.
                os.read(fd, 1024)
            except Exception:
                # If an error occurs (for example, no data available), break out of the loop.
                break

# =============================================================================
# FUNCTION: extract_box
# =============================================================================

def extract_box(text):
    """
    Extracts the first ASCII table (box) found within the provided text.
    
    Parameters:
        text : A string that may contain an ASCII table.
    
    Returns:
        A string containing the extracted ASCII table or the original text if no table is found.
    """
    # Split the input text into individual lines.
    lines = text.splitlines()
    start_index = None  # Variable to mark the start of the ASCII table.
    end_index = None    # Variable to mark the end of the ASCII table.
    # Loop over the lines to find the beginning of the table by looking for typical box characters.
    for i, line in enumerate(lines):
        if line.startswith("┌") or line.startswith("╔") or (line.startswith("+") and "-" in line):
            start_index = i  # Set the start index when a box border is found.
            break
    # If a starting line is found, search for the end of the table.
    if start_index is not None:
        for j in range(start_index, len(lines)):
            # Look for lines that denote the bottom border of the table.
            if lines[j].startswith("└") or lines[j].startswith("╝") or (lines[j].startswith("+") and "-" in lines[j]):
                end_index = j  # Set the end index when found.
                break
        # If no explicit end is found, assume the table goes to the end of the text.
        if end_index is None:
            end_index = len(lines) - 1
        # Join the identified lines back into a single string and return.
        return "\n".join(lines[start_index:end_index+1])
    else:
        # Return the original text if no table is identified.
        return text

# =============================================================================
# FUNCTION: parse_box_data
# =============================================================================

def parse_box_data(box_text):
    """
    Parses the ASCII table (box) text output by bettercap to extract BLE scan data.
    
    Parameters:
        box_text : A string containing the ASCII table.
    
    Returns:
        A list of dictionaries where each dictionary contains data columns:
        RSSI, MAC, Vendor, Flags, and Seen.
    """
    rows = []  # Initialize an empty list to store parsed rows.
    lines = box_text.splitlines()  # Split the ASCII table text into individual lines.
    top_border_index = None  # Variable to locate the table header border.
    # Search for the header border using common characters.
    for i, line in enumerate(lines):
        if line.startswith("├") or line.startswith("╟"):
            top_border_index = i  # Found the header border.
            break
    # Use an alternate method if the first approach did not work.
    if top_border_index is None:
        for i, line in enumerate(lines):
            if (line.startswith("+") and "-" in line and "┬" not in line) or "┼" in line:
                top_border_index = i  # Found an alternative header border.
                break
    # Return an empty list if no header border is found.
    if top_border_index is None:
        return rows
    data_lines = []  # List to store lines containing actual data rows.
    # Iterate over the lines following the header border.
    for line in lines[top_border_index+1:]:
        # Stop if the end of the table is reached.
        if line.startswith("└") or line.startswith("╚") or (line.startswith("+") and "-" in line):
            break
        # If the line contains the vertical bar, consider it as data.
        if "│" in line:
            data_lines.append(line)
    # Process each data line to extract column values.
    for dl in data_lines:
        parts = dl.split("│")  # Split the line into parts based on the vertical bar.
        # Continue only if there are enough parts for expected columns.
        if len(parts) < 7:
            continue
        # Extract and strip extra spaces from each part.
        rssi   = parts[1].strip()    # RSSI column
        mac    = parts[2].strip()    # MAC address column
        vendor = parts[3].strip()    # Vendor column
        flags  = parts[4].strip()    # Flags column
        seen   = parts[6].strip()    # Seen time column (based on table format)
        # Append a dictionary with the parsed values to the rows list.
        rows.append({
            "RSSI": rssi,
            "MAC": mac,
            "Vendor": vendor,
            "Flags": flags,
            "Seen": seen
        })
    # Return the list of parsed rows.
    return rows

# =============================================================================
# FUNCTION: parse_seen_time
# =============================================================================

def parse_seen_time(seen_str):
    """
    Converts the 'Seen' time string (in HH:MM:SS format) into a datetime object for accurate sorting.
    
    Parameters:
        seen_str : A string representing the time a device was seen (e.g., "12:34:56").
    
    Returns:
        A datetime object corresponding to the provided time on the current day, or a maximum datetime on error.
    """
    try:
        now = datetime.now()  # Retrieve the current date and time.
        hh, mm, ss = seen_str.split(":")  # Split the seen time into hours, minutes, and seconds.
        # Replace the current time components with those from the seen time, keeping the current date.
        return now.replace(hour=int(hh), minute=int(mm), second=int(ss), microsecond=0)
    except:
        # If parsing fails, return a maximum datetime to ensure this entry sorts last.
        return datetime.max

# =============================================================================
# FUNCTION: update_best_entries
# =============================================================================

def update_best_entries(new_rows):
    """
    Updates the global dictionary 'best_entries' with new data rows.
    Each device (identified by MAC address) is updated only if the new entry has a stronger RSSI.
    
    Parameters:
        new_rows : A list of dictionaries, each representing a parsed row from the BLE scan.
    
    Returns:
        A boolean value indicating whether any updates were made (True if updated, otherwise False).
    """
    global best_entries  # Declare that we are modifying the global best_entries dictionary.
    updated = False  # Flag to track if any update has been made.
    # Iterate over each new row.
    for row in new_rows:
        mac = row["MAC"]  # Retrieve the MAC address from the current row.
        try:
            # Convert the RSSI value from the row to a float for numerical comparison.
            rssi_val = float(row["RSSI"].lower().replace("dbm", "").strip())
        except:
            # Assign a very low default value if parsing fails.
            rssi_val = -9999.0
        # If the MAC address is not already present, add it to the dictionary.
        if mac not in best_entries:
            best_entries[mac] = row  # Store the new row under its MAC address.
            updated = True  # Mark that an update occurred.
        else:
            try:
                # Parse the previously stored RSSI value for this MAC address.
                old_rssi_val = float(best_entries[mac]["RSSI"].lower().replace("dbm", "").strip())
            except:
                old_rssi_val = -9999.0
            # Update the stored entry if the new RSSI is stronger.
            if rssi_val > old_rssi_val:
                best_entries[mac] = row  # Replace the old entry with the new one.
                updated = True  # Mark that an update occurred.
    # Return whether any updates were made.
    return updated

# =============================================================================
# FUNCTION: write_output_csv
# =============================================================================

def write_output_csv(csv_path):
    """
    Writes the global 'best_entries' dictionary to a CSV file.
    The entries are sorted by the 'Seen' time from oldest to newest.
    
    Parameters:
        csv_path : The full file path (including file name) where the CSV file is to be written.
    """
    global best_entries  # Declare the use of the global best_entries variable.
    
    def sort_key(item):
        # Helper function to extract the sorting key from each entry based on the 'Seen' time.
        return parse_seen_time(item[1]["Seen"])
    
    # Sort the entries by 'Seen' time.
    sorted_entries = sorted(best_entries.items(), key=sort_key)
    try:
        # Open the CSV file in write mode with UTF-8 encoding.
        with open(csv_path, "w", encoding="utf-8") as f:
            # Write the CSV header row.
            f.write("RSSI,MAC,Vendor,Flags,Seen\n")
            # Write each sorted entry as a row in the CSV file.
            for mac, row in sorted_entries:
                f.write("{},{},{},{},{}\n".format(
                    row["RSSI"], row["MAC"], row["Vendor"], row["Flags"], row["Seen"]
                ))
    except Exception as e:
        # Log an error if there is an issue writing the CSV file.
        logging.error("Error writing CSV file '%s': %s", csv_path, e, exc_info=True)

# =============================================================================
# MAIN FUNCTION
# =============================================================================

def main():
    """
    The main function orchestrates the following:
      - Parsing command-line arguments.
      - Configuring logging and preparing the log directory.
      - Launching bettercap in a pseudo-terminal.
      - Sending BLE scanning commands and continuously capturing and processing data.
      - Writing the parsed data to the CSV file (named "data.bettercap").
      - Handling graceful termination on user interruption.
    """
    global bettercap_process, pty_fd, best_entries  # Declare usage of global variables.

    # Initialize the argument parser for command-line parameters.
    parser = argparse.ArgumentParser(
        description="Launch bettercap, send BLE commands, and continuously log BLE data (no duplicates)."
    )
    # Add an argument for setting the logging level (DEBUG, INFO, WARNING, or ERROR).
    parser.add_argument("--log-level", type=str, default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                        help="Logging level (default: INFO)")
    # Add an argument to specify the directory for log files.
    parser.add_argument("--log-dir", type=str, default="~/wardrivelog",
                        help="Directory for wardriving logs (default: ~/wardrivelog)")
    # Add an argument to specify how often to log a summary (in iterations).
    parser.add_argument("--log-interval", type=int, default=10,
                        help="Log summary every N iterations (default: 10)")
    # Parse the provided command-line arguments.
    args = parser.parse_args()

    # Define ANSI escape codes for blue text and reset formatting.
    BLUE = "\x1b[34m"   # Blue text
    RESET = "\x1b[0m"   # Reset to default text

    # Configure logging with a specific format that includes a blue [BETTERCAP] prefix.
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),  # Set logging level based on user input.
        format=f"{BLUE}[BETTERCAP]{RESET} %(asctime)s [%(levelname)s] %(message)s",  # Format for log messages.
        datefmt="%Y-%m-%d %H:%M:%S"  # Date and time format for the logs.
    )
    # Log the parsed arguments at DEBUG level (only visible if logging level is DEBUG).
    logging.debug("Parsed arguments: %s", args)

    # Expand the '~' in the log directory path to the full home directory path.
    log_dir = os.path.expanduser(args.log_dir)
    # Check if the specified log directory exists.
    if not os.path.exists(log_dir):
        try:
            # Create the log directory if it does not exist.
            os.makedirs(log_dir)
            logging.info("Created log directory: %s", log_dir)
        except Exception as e:
            # Log an error and exit if the directory cannot be created.
            logging.error("Error creating log directory: %s", e, exc_info=True)
            sys.exit(1)

    # Set the CSV file path to "data.bettercap" inside the log directory.
    csv_path = os.path.join(log_dir, "data.bettercap")
    # Create and open the CSV file for writing.
    with open(csv_path, "w", encoding="utf-8") as f:
        f.write("RSSI,MAC,Vendor,Flags,Seen\n")  # Write the header row for the CSV file.
    logging.info("Created new log file: %s", csv_path)

    # Compile a regular expression pattern to match ANSI escape codes (used for stripping terminal colors).
    ansi_escape = re.compile(r'\x1B\[[0-?]*[ -/]*[@-~]')

    # Create a pseudo-terminal pair; pty_fd for the parent and slave_fd for the child process.
    pty_fd, slave_fd = os.openpty()
    # Retrieve the current flags for the parent file descriptor.
    flags = fcntl.fcntl(pty_fd, fcntl.F_GETFL)
    # Set the parent file descriptor to non-blocking mode by adding the O_NONBLOCK flag.
    fcntl.fcntl(pty_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

    try:
        # Log that bettercap is about to be launched.
        logging.info("Launching bettercap...")
        # Launch bettercap as a subprocess with sudo using the slave end of the pseudo-terminal.
        bettercap_process = subprocess.Popen(
            ["sudo", "bettercap"],   # Command to launch bettercap with sudo privileges.
            stdin=slave_fd,          # Set standard input to the slave file descriptor.
            stdout=slave_fd,         # Set standard output to the slave file descriptor.
            stderr=slave_fd,         # Set standard error to the slave file descriptor.
            universal_newlines=False,  # Handle I/O as bytes, not text.
            bufsize=0,               # Use unbuffered I/O for immediate data processing.
            preexec_fn=os.setsid     # Start the process in a new session (detaching it from the terminal).
        )
    except Exception as e:
        # Log an error if bettercap fails to launch and exit the script.
        logging.error("Failed to launch bettercap: %s", e, exc_info=True)
        sys.exit(1)
    # Close the slave file descriptor in the parent process as it is no longer needed.
    os.close(slave_fd)

    # Wait briefly to allow bettercap to initialize.
    time.sleep(1)
    try:
        # Log that the BLE scanning command is being sent.
        logging.info("Sending command: ble.recon on")
        # Send the "ble.recon on" command to bettercap via the pseudo-terminal.
        os.write(pty_fd, b"ble.recon on\n")
    except Exception as e:
        # Log any error that occurs while sending the command.
        logging.error("Error sending 'ble.recon on' command: %s", e, exc_info=True)

    iteration_count = 0  # Initialize a counter to track the number of iterations in the main loop.
    logging.info("Entering main loop; press Ctrl+C to exit.")  # Inform the user that the main loop has started.
    try:
        # Start an infinite loop to continuously send commands and process output.
        while True:
            iteration_count += 1  # Increment the iteration counter.
            time.sleep(1)         # Wait for 1 second between iterations.
            # Flush any stale data from the pseudo-terminal to ensure fresh output.
            flush_input(pty_fd, timeout=0.2)
            try:
                # Send the "ble.show" command to retrieve the current BLE scan results.
                os.write(pty_fd, b"ble.show\n")
            except Exception as e:
                # Log an error if sending the "ble.show" command fails.
                logging.error("Error sending 'ble.show' command: %s", e, exc_info=True)
                continue  # Skip the rest of the current iteration and try again.

            captured_output = b""  # Initialize a variable to accumulate bytes from the output.
            # Set an end time for reading data from bettercap.
            end_read_time = time.time() + 0.2
            # Loop until the designated read timeout is reached.
            while time.time() < end_read_time:
                # Check if data is available to be read from the pseudo-terminal.
                rlist, _, _ = select.select([pty_fd], [], [], 0.05)
                if pty_fd in rlist:
                    try:
                        # Read up to 1024 bytes of data from the pseudo-terminal.
                        chunk = os.read(pty_fd, 1024)
                        if chunk:
                            captured_output += chunk  # Append the chunk to the captured output.
                            end_read_time = time.time() + 0.2  # Extend the read period if data is still coming.
                    except Exception as e:
                        # Log an error if reading from the pseudo-terminal fails.
                        logging.error("Error reading output: %s", e, exc_info=True)
                        break  # Break out of the reading loop if an error occurs.

            captured_bytes = len(captured_output)  # Determine the number of bytes captured.
            # Decode the captured bytes into a string using UTF-8 encoding.
            captured_text = captured_output.decode("utf-8", errors="replace")
            # Remove ANSI escape codes (used for terminal colors) from the captured text.
            cleaned_text = ansi_escape.sub("", captured_text)
            # Extract the ASCII table (box) from the cleaned text.
            box_text = extract_box(cleaned_text)
            # Parse the ASCII table to extract BLE scan data.
            new_rows = parse_box_data(box_text)
            parsed_count = len(new_rows)  # Count the number of rows parsed.
            changed = False  # Flag to indicate whether the best_entries dictionary was updated.
            if new_rows:
                # Update the best_entries dictionary with the new data and check if any update occurred.
                changed = update_best_entries(new_rows)
                if changed:
                    # If an update occurred, write the updated entries to the CSV file.
                    write_output_csv(csv_path)
            # Every log_interval iterations, log a summary of the iteration.
            if iteration_count % args.log_interval == 0:
                log_summary = (
                    f"Iteration #{iteration_count}: ble.show sent, captured {captured_bytes} bytes, "
                    f"parsed {parsed_count} rows, best_entries: {len(best_entries)} "
                    f"{'(updated)' if changed else '(no change)'}."
                )
                logging.info(log_summary)  # Log the summary.
    except KeyboardInterrupt:
        # Log that a KeyboardInterrupt (Ctrl+C) has been received and exit the loop.
        logging.info("KeyboardInterrupt received; exiting main loop.")

    try:
        # Log that the quit command is being sent to bettercap.
        logging.info("Sending quit command to bettercap...")
        # Send the "quit" command via the pseudo-terminal.
        os.write(pty_fd, b"quit\n")
    except Exception as e:
        # Log any error encountered when sending the quit command.
        logging.error("Error sending quit command: %s", e, exc_info=True)
    time.sleep(1)  # Wait briefly to allow bettercap to process the quit command.
    # Terminate the bettercap process explicitly.
    bettercap_process.terminate()
    logging.info("bettercap process terminated.")  # Log that bettercap has been terminated.
    os.close(pty_fd)  # Close the parent's side of the pseudo-terminal.

# Ensure that the main() function is called only when the script is run directly.
if __name__ == "__main__":
    main()  # Call the main function to start the script.
