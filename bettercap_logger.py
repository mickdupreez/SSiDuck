#!/usr/bin/env python3
"""
----------------------------------------------------------------------------------------------------
Script Name: bettercap_logger.py
Version: 1.9.2
Author: Michael Du Preez
Date: 2025-03-17

Overview:
    This script launches bettercap in a pseudo-terminal using sudo, sends interactive commands to
    enable Bluetooth scanning (BLE), and continuously logs the BLE data to a CSV file with logic
    similar to wifi_logger. Specifically:

      1. Creates a new CSV file named with the current date and time (format: YYYY-MM-DD_HH-MM-SS.bettercap)
         in the specified log directory.
      2. Launches bettercap in a detached session so its output does not echo to the parent terminal.
      3. Sends "ble.recon on" once to enable BLE scanning.
      4. Every 1 second:
         a. Flushes stale output.
         b. Sends "ble.show" and immediately reads the ASCII table output.
         c. Strips ANSI color codes.
         d. Parses the columns: RSSI, MAC, Vendor, Flags, and Seen.
         e. Updates an in-memory dictionary so that each MAC is unique (only updating when a stronger
            RSSI or a newer "Seen" time is found).
         f. Rewrites the CSV sorted by the "Seen" column from oldest to newest.
         g. Logs a one-line summary every N iterations (default every 10 iterations).
      5. Continues indefinitely until interrupted (Ctrl+C), at which point it sends "quit" to bettercap
         and terminates gracefully.

Usage:
    sudo python3 bettercap_logger.py --log-level INFO --log-dir ~/wardrivelog --log-interval 10

Prerequisites:
    - Python 3.x must be installed.
    - bettercap must be installed and available in the system's PATH.
    - Sudo privileges are required to launch bettercap.
----------------------------------------------------------------------------------------------------
"""

import os
import sys
import re
import subprocess
import time
import logging
import argparse
import select
import signal
import fcntl
from datetime import datetime

# =============================================================================
# GLOBAL VARIABLES
# =============================================================================
bettercap_process = None    # Holds the bettercap process object
pty_fd = None               # File descriptor for the parent's side of the pseudo-terminal

# Dictionary to store the "best" entries for each MAC. Key=MAC, Value=dict of columns.
best_entries = {}

# =============================================================================
# SIGNAL HANDLER
# =============================================================================
def signal_handler(sig, frame):
    """
    Gracefully terminate bettercap when a signal (SIGINT/SIGTERM) is received.
    """
    logging.info("Shutdown signal received. Terminating bettercap process...")
    try:
        if pty_fd is not None:
            os.write(pty_fd, b"quit\n")  # Send 'quit' to bettercap
            time.sleep(1)
    except Exception as e:
        logging.error("Error sending quit command: %s", e, exc_info=True)
    if bettercap_process:
        bettercap_process.terminate()
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# =============================================================================
# FUNCTION: flush_input
# =============================================================================
def flush_input(fd, timeout=0.2):
    """
    Flush any data currently available from the file descriptor.
    """
    end_time = time.time() + timeout
    while time.time() < end_time:
        rlist, _, _ = select.select([fd], [], [], 0.05)
        if fd in rlist:
            try:
                os.read(fd, 1024)
            except Exception:
                break

# =============================================================================
# FUNCTION: extract_box
# =============================================================================
def extract_box(text):
    """
    Extract the first block of text that appears to be a box (ASCII table) from the given text.
    """
    lines = text.splitlines()
    start_index = None
    end_index = None
    for i, line in enumerate(lines):
        if line.startswith("┌") or line.startswith("╔") or (line.startswith("+") and "-" in line):
            start_index = i
            break
    if start_index is not None:
        for j in range(start_index, len(lines)):
            if lines[j].startswith("└") or lines[j].startswith("╝") or (lines[j].startswith("+") and "-" in lines[j]):
                end_index = j
                break
        if end_index is None:
            end_index = len(lines) - 1
        return "\n".join(lines[start_index:end_index+1])
    else:
        return text

# =============================================================================
# FUNCTION: parse_box_data
# =============================================================================
def parse_box_data(box_text):
    """
    Parse an ASCII table (box) from bettercap's BLE output to extract columns:
    RSSI, MAC, Vendor, Flags, and Seen.
    """
    rows = []
    lines = box_text.splitlines()
    top_border_index = None
    for i, line in enumerate(lines):
        if line.startswith("├") or line.startswith("╟"):
            top_border_index = i
            break
    if top_border_index is None:
        for i, line in enumerate(lines):
            if (line.startswith("+") and "-" in line and "┬" not in line) or "┼" in line:
                top_border_index = i
                break
    if top_border_index is None:
        return rows
    data_lines = []
    for line in lines[top_border_index+1:]:
        if line.startswith("└") or line.startswith("╚") or (line.startswith("+") and "-" in line):
            break
        if "│" in line:
            data_lines.append(line)
    for dl in data_lines:
        parts = dl.split("│")
        if len(parts) < 7:
            continue
        rssi   = parts[1].strip()
        mac    = parts[2].strip()
        vendor = parts[3].strip()
        flags  = parts[4].strip()
        seen   = parts[6].strip()
        rows.append({
            "RSSI": rssi,
            "MAC": mac,
            "Vendor": vendor,
            "Flags": flags,
            "Seen": seen
        })
    return rows

# =============================================================================
# FUNCTION: parse_seen_time
# =============================================================================
def parse_seen_time(seen_str):
    """
    Parse the 'Seen' time (HH:MM:SS) into a datetime object for sorting.
    """
    try:
        now = datetime.now()
        hh, mm, ss = seen_str.split(":")
        return now.replace(hour=int(hh), minute=int(mm), second=int(ss), microsecond=0)
    except:
        return datetime.max

# =============================================================================
# FUNCTION: update_best_entries
# =============================================================================
def update_best_entries(new_rows):
    """
    Update the global best_entries dictionary with new rows, 
    replacing only if the new RSSI is stronger.
    """
    global best_entries
    updated = False
    for row in new_rows:
        mac = row["MAC"]
        try:
            # Parse RSSI to float for numeric comparison
            rssi_val = float(row["RSSI"].lower().replace("dbm", "").strip())
        except:
            rssi_val = -9999.0  # Default if parsing fails

        if mac not in best_entries:
            # Add new MAC if not seen before
            best_entries[mac] = row
            updated = True
        else:
            try:
                # Compare current best RSSI with new one
                old_rssi_val = float(best_entries[mac]["RSSI"].lower().replace("dbm", "").strip())
            except:
                old_rssi_val = -9999.0

            # Only update if the new RSSI is stronger (higher number)
            if rssi_val > old_rssi_val:
                best_entries[mac] = row
                updated = True
    return updated


# =============================================================================
# FUNCTION: write_output_csv
# =============================================================================
def write_output_csv(csv_path):
    """
    Write the global best_entries dictionary to the CSV file, sorted by 'Seen' (oldest to newest).
    """
    global best_entries
    def sort_key(item):
        return parse_seen_time(item[1]["Seen"])
    sorted_entries = sorted(best_entries.items(), key=sort_key)
    try:
        with open(csv_path, "w", encoding="utf-8") as f:
            f.write("RSSI,MAC,Vendor,Flags,Seen\n")
            for mac, row in sorted_entries:
                f.write("{},{},{},{},{}\n".format(
                    row["RSSI"], row["MAC"], row["Vendor"], row["Flags"], row["Seen"]
                ))
    except Exception as e:
        logging.error("Error writing CSV file '%s': %s", csv_path, e, exc_info=True)

# =============================================================================
# MAIN FUNCTION
# =============================================================================
def main():
    global bettercap_process, pty_fd, best_entries

    parser = argparse.ArgumentParser(
        description="Launch bettercap, send BLE commands, and continuously log BLE data (no duplicates)."
    )
    parser.add_argument("--log-level", type=str, default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                        help="Logging level (default: INFO)")
    parser.add_argument("--log-dir", type=str, default="~/wardrivelog",
                        help="Directory for wardriving logs (default: ~/wardrivelog)")
    parser.add_argument("--log-interval", type=int, default=10,
                        help="Log summary every N iterations (default: 10)")
    args = parser.parse_args()

    # Color code for blue
    BLUE = "\x1b[34m"
    RESET = "\x1b[0m"

    # Configure logging with a blue [BETTERCAP] prefix
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format=f"{BLUE}[BETTERCAP]{RESET} %(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    logging.debug("Parsed arguments: %s", args)

    log_dir = os.path.expanduser(args.log_dir)
    if not os.path.exists(log_dir):
        try:
            os.makedirs(log_dir)
            logging.info("Created log directory: %s", log_dir)
        except Exception as e:
            logging.error("Error creating log directory: %s", e, exc_info=True)
            sys.exit(1)

    # Create a new CSV file named with the current date and time.
    timestamp_str = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    csv_path = os.path.join(log_dir, f"{timestamp_str}.bettercap")
    with open(csv_path, "w", encoding="utf-8") as f:
        f.write("RSSI,MAC,Vendor,Flags,Seen\n")
    logging.info("Created new log file: %s", csv_path)

    # Regex to strip ANSI color codes
    ansi_escape = re.compile(r'\x1B\[[0-?]*[ -/]*[@-~]')

    pty_fd, slave_fd = os.openpty()
    flags = fcntl.fcntl(pty_fd, fcntl.F_GETFL)
    fcntl.fcntl(pty_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

    try:
        logging.info("Launching bettercap...")
        bettercap_process = subprocess.Popen(
            ["sudo", "bettercap"],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            universal_newlines=False,
            bufsize=0,
            preexec_fn=os.setsid
        )
    except Exception as e:
        logging.error("Failed to launch bettercap: %s", e, exc_info=True)
        sys.exit(1)
    os.close(slave_fd)

    time.sleep(1)
    try:
        logging.info("Sending command: ble.recon on")
        os.write(pty_fd, b"ble.recon on\n")
    except Exception as e:
        logging.error("Error sending 'ble.recon on' command: %s", e, exc_info=True)

    iteration_count = 0
    logging.info("Entering main loop; press Ctrl+C to exit.")
    try:
        while True:
            iteration_count += 1
            time.sleep(1)
            flush_input(pty_fd, timeout=0.2)
            try:
                os.write(pty_fd, b"ble.show\n")
            except Exception as e:
                logging.error("Error sending 'ble.show' command: %s", e, exc_info=True)
                continue

            captured_output = b""
            end_read_time = time.time() + 0.2
            while time.time() < end_read_time:
                rlist, _, _ = select.select([pty_fd], [], [], 0.05)
                if pty_fd in rlist:
                    try:
                        chunk = os.read(pty_fd, 1024)
                        if chunk:
                            captured_output += chunk
                            end_read_time = time.time() + 0.2
                    except Exception as e:
                        logging.error("Error reading output: %s", e, exc_info=True)
                        break

            captured_bytes = len(captured_output)
            captured_text = captured_output.decode("utf-8", errors="replace")
            cleaned_text = ansi_escape.sub("", captured_text)
            box_text = extract_box(cleaned_text)
            new_rows = parse_box_data(box_text)
            parsed_count = len(new_rows)
            changed = False
            if new_rows:
                changed = update_best_entries(new_rows)
                if changed:
                    write_output_csv(csv_path)
            # Log summary only every log_interval iterations
            if iteration_count % args.log_interval == 0:
                log_summary = (
                    f"Iteration #{iteration_count}: ble.show sent, captured {captured_bytes} bytes, "
                    f"parsed {parsed_count} rows, best_entries: {len(best_entries)} "
                    f"{'(updated)' if changed else '(no change)'}."
                )
                logging.info(log_summary)
    except KeyboardInterrupt:
        logging.info("KeyboardInterrupt received; exiting main loop.")

    try:
        logging.info("Sending quit command to bettercap...")
        os.write(pty_fd, b"quit\n")
    except Exception as e:
        logging.error("Error sending quit command: %s", e, exc_info=True)
    time.sleep(1)
    bettercap_process.terminate()
    logging.info("bettercap process terminated.")
    os.close(pty_fd)

if __name__ == "__main__":
    main()
