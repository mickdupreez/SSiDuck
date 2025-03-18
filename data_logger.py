#!/usr/bin/env python3
"""
----------------------------------------------------------------------------------------------------
Script Name: data_logger.py
Version: 1.4.3
Author: Michael Du Preez
Date: 2025-03-18

Overview:
    This script continuously scans a specified log directory (default: ~/wardrivelog) for the
    newest .bettercap and .kismet log files (whose filenames contain date and time in the format
    YYYY-MM-DD_HH-MM-SS). It then parses data from both files and merges them into a single CSV file.
    The output CSV file is created once at startup (named "wardrive-<todaysdateandtime>.csv") and
    is continuously updated with merged data.

    At startup, the script checks for an "upload" subdirectory in the log directory. If it doesn't
    exist, the script creates it. Then, any preexisting .csv files in the log directory are moved
    into the "upload" directory, with appropriate log messages.

    The output CSV has the following two header lines (identical to the kismet header):

      WigleWifi-1.4,appRelease=Kismet202307R1,model=Kismet202307R1,release=2023.07.R1,
      device=kismet,display=kismet,board=kismet,brand=kismet
      MAC,SSID,AuthMode,FirstSeen,Channel,RSSI,CurrentLatitude,CurrentLongitude,AltitudeMeters,AccuracyMeters,Type

    For rows from the .bettercap file (header: RSSI,MAC,Vendor,Flags,Seen), the values are mapped as:
      - RSSI → RSSI (with "dBm" removed)
      - MAC → MAC
      - Vendor → SSID
      - Flags → AuthMode
      - Seen → FirstSeen (the time is prefixed with the date from the filename)
    If a matching MAC exists in the .kismet file, its GPS-related fields are merged.
    Otherwise, for BLE records missing GPS data, the script uses the most recent kismet record
    with valid GPS data as fallback.
    In any case, records derived from bettercap will have Type set to "BLE".

    Every 10 iterations the script logs a summary showing cumulative changes (new records and updates)
    since the last summary log.
    
Usage:
    python3 data_logger.py --log-dir ~/wardrivelog

Prerequisites:
    - Python 3.x must be installed.
    - The log directory should contain at least one .bettercap file and one .kismet file
      (with filenames like YYYY-MM-DD_HH-MM-SS.bettercap and YYYY-MM-DD_HH-MM-SS.kismet).
----------------------------------------------------------------------------------------------------
"""

import os                  # For file system operations
import glob                # To search for files by pattern
import csv                 # To read and write CSV files
import argparse            # For command-line argument parsing
import logging             # For logging messages
import signal              # For handling termination signals
import sys                 # For system exit
import re                  # For regex matching
from datetime import datetime  # For timestamping
import copy                # For copying dictionaries
import shutil              # For moving files

# -----------------------------------------------------------------------------
# Global Constants & Logging Configuration
# -----------------------------------------------------------------------------

# Header lines for the merged CSV file (same as the kismet header)
HEADER_LINE_1 = ("WigleWifi-1.4,appRelease=Kismet202307R1,model=Kismet202307R1,"
                 "release=2023.07.R1,device=kismet,display=kismet,board=kismet,brand=kismet")
HEADER_LINE_2 = ("MAC,SSID,AuthMode,FirstSeen,Channel,RSSI,CurrentLatitude,"
                 "CurrentLongitude,AltitudeMeters,AccuracyMeters,Type")

# List of output columns in order
OUTPUT_COLUMNS = ["MAC", "SSID", "AuthMode", "FirstSeen", "Channel", "RSSI",
                  "CurrentLatitude", "CurrentLongitude", "AltitudeMeters", "AccuracyMeters", "Type"]

# Configure logging with a green [MERGE] prefix (following the style of the other scripts)
MERGE_GREEN = "\x1b[32m"
RESET = "\x1b[0m"
logging.basicConfig(
    level=logging.INFO,
    format=f"{MERGE_GREEN}[MERGE]{RESET} %(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

# -----------------------------------------------------------------------------
# Signal Handler for Graceful Shutdown
# -----------------------------------------------------------------------------
def signal_handler(sig, frame):
    """
    Handle termination signals to allow for a graceful shutdown.
    """
    logging.info("Shutdown signal received. Exiting merge loop...")
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# -----------------------------------------------------------------------------
# Pre-startup Function: check_and_move_csv_files
# -----------------------------------------------------------------------------
def check_and_move_csv_files(log_dir: str, upload_dir: str) -> None:
    """
    Check if the upload directory exists in log_dir; if not, create it.
    Then, move any preexisting .csv files from log_dir to upload_dir.
    Log actions accordingly.
    
    Args:
        log_dir (str): The main log directory.
        upload_dir (str): The subdirectory for uploads.
    """
    # Create upload directory if it doesn't exist.
    if not os.path.isdir(upload_dir):
        try:
            os.makedirs(upload_dir)
            logging.info("Upload directory created: %s", upload_dir)
        except Exception as e:
            logging.error("Error creating upload directory %s: %s", upload_dir, e, exc_info=True)
    
    # Find all .csv files in log_dir (only top-level, not in subdirectories)
    csv_files = glob.glob(os.path.join(log_dir, "*.csv"))
    for file in csv_files:
        # Skip if the file is already in the upload directory.
        if os.path.dirname(file) == os.path.abspath(upload_dir):
            continue
        try:
            shutil.move(file, upload_dir)
            logging.info("Moved file %s to upload directory.", file)
        except Exception as e:
            logging.error("Error moving file %s: %s", file, e, exc_info=True)

# -----------------------------------------------------------------------------
# Helper Function: is_valid_kismet_file
# -----------------------------------------------------------------------------
def is_valid_kismet_file(filepath: str) -> bool:
    """
    Check if a given kismet file's basename matches the expected date-time format.
    Expected format: YYYY-MM-DD_HH-MM-SS.kismet.
    Files like 'wardrive.kismet' are ignored.
    """
    basename = os.path.basename(filepath)
    pattern = r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.kismet$"
    return re.match(pattern, basename) is not None

# -----------------------------------------------------------------------------
# Function: get_newest_file
# -----------------------------------------------------------------------------
def get_newest_file(directory: str, extension: str) -> str:
    """
    Search the given directory for files with the specified extension and return the one with
    the newest date/time in its filename. For kismet files, only files matching the expected
    date-time pattern are considered.
    
    Args:
        directory (str): Directory to search.
        extension (str): File extension (e.g., "bettercap" or "kismet").
    
    Returns:
        str: Full path to the newest file found, or an empty string if none.
    """
    pattern = os.path.join(directory, f"*.{extension}")
    files = glob.glob(pattern)
    if extension == "kismet":
        files = [f for f in files if is_valid_kismet_file(f)]
    if not files:
        return ""
    files.sort()
    newest = files[-1]
    logging.debug("Found newest .%s file: %s", extension, newest)
    return newest

# -----------------------------------------------------------------------------
# Function: parse_bettercap_file
# -----------------------------------------------------------------------------
def parse_bettercap_file(filepath: str, base_date: str) -> dict:
    """
    Parse the .bettercap file and return a dictionary keyed by MAC.
    The file is expected to have a header:
      RSSI,MAC,Vendor,Flags,Seen
    Extra commas in the Vendor or Flags fields are handled.
    The "dBm" unit is stripped from the RSSI field.
    The "Seen" time (HH:MM:SS) is prefixed with the base_date to form
    a full timestamp in the format "YYYY-MM-DD HH:MM:SS".
    
    Args:
        filepath (str): Path to the .bettercap file.
        base_date (str): Date string (YYYY-MM-DD) extracted from the filename.
    
    Returns:
        dict: Dictionary with MAC as keys and a mapped record as values.
    """
    data = {}
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    except Exception as e:
        logging.error("Error reading %s: %s", filepath, e, exc_info=True)
        return data

    for line in lines[1:]:
        if not line.strip():
            continue
        parts = [p.strip() for p in line.split(",")]
        rssi = mac = vendor = flags = seen = ""
        if len(parts) == 5:
            rssi, mac, vendor, flags, seen = parts
        elif len(parts) == 7:
            rssi = parts[0]
            mac = parts[1]
            vendor = parts[2] + ", " + parts[3]
            flags = parts[4] + ", " + parts[5]
            seen = parts[6]
        elif len(parts) > 7:
            rssi = parts[0]
            mac = parts[1]
            seen = parts[-1]
            middle = parts[2:-1]
            half = len(middle) // 2
            vendor = ", ".join(middle[:half])
            flags = ", ".join(middle[half:])
        else:
            continue

        rssi = rssi.replace("dBm", "").strip()
        full_timestamp = f"{base_date} {seen}"
        record = {
            "MAC": mac,
            "SSID": vendor,       # Vendor maps to SSID.
            "AuthMode": flags,    # Flags maps to AuthMode.
            "FirstSeen": full_timestamp,  # Combine date from filename and time from file.
            "Channel": "",
            "RSSI": rssi,
            "CurrentLatitude": "",
            "CurrentLongitude": "",
            "AltitudeMeters": "",
            "AccuracyMeters": "",
            "Type": ""            # Will be set to "BLE" later.
        }
        data[mac] = record
    return data

# -----------------------------------------------------------------------------
# Function: parse_kismet_file
# -----------------------------------------------------------------------------
def parse_kismet_file(filepath: str) -> dict:
    """
    Parse the .kismet file and return a dictionary keyed by MAC.
    The file is expected to have two header lines; the second header defines the columns:
      MAC,SSID,AuthMode,FirstSeen,Channel,RSSI,CurrentLatitude,CurrentLongitude,AltitudeMeters,AccuracyMeters,Type
    
    Args:
        filepath (str): Path to the .kismet file.
    
    Returns:
        dict: Dictionary with MAC as keys and the corresponding record as values.
    """
    data = {}
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            reader = csv.reader(f)
            _ = next(reader, None)
            header2 = next(reader, None)
            if not header2:
                logging.error("No valid header found in %s.", filepath)
                return data
            for row in reader:
                if not row or len(row) < len(OUTPUT_COLUMNS):
                    continue
                record = dict(zip(OUTPUT_COLUMNS, row))
                mac = record.get("MAC", "")
                if mac:
                    data[mac] = record
    except Exception as e:
        logging.error("Error reading %s: %s", filepath, e, exc_info=True)
    return data

# -----------------------------------------------------------------------------
# Function: merge_logs
# -----------------------------------------------------------------------------
def merge_logs(bettercap_data: dict, kismet_data: dict) -> dict:
    """
    Merge dictionaries from bettercap and kismet files keyed by MAC.
    For each MAC:
      - If a kismet record exists, its GPS and channel fields are retained.
      - The bettercap record (if exists) supplies SSID, AuthMode, FirstSeen, and RSSI.
      - For BLE records, the "Type" field is set to "BLE".
      - For BLE records missing GPS data, the most recent kismet record with valid GPS data
        is used as fallback.
    
    Args:
        bettercap_data (dict): Parsed data from the .bettercap file.
        kismet_data (dict): Parsed data from the .kismet file.
    
    Returns:
        dict: Merged dictionary keyed by MAC.
    """
    merged = {}
    for mac, record in kismet_data.items():
        merged[mac] = record

    for mac, bc_record in bettercap_data.items():
        if mac in merged:
            merged[mac]["SSID"] = bc_record.get("SSID", merged[mac]["SSID"])
            merged[mac]["AuthMode"] = bc_record.get("AuthMode", merged[mac]["AuthMode"])
            merged[mac]["FirstSeen"] = bc_record.get("FirstSeen", merged[mac]["FirstSeen"])
            merged[mac]["RSSI"] = bc_record.get("RSSI", merged[mac]["RSSI"])
        else:
            merged[mac] = bc_record
        merged[mac]["Type"] = "BLE"

    fallback_gps = None
    fallback_gps_time = None
    for record in kismet_data.values():
        if record.get("CurrentLatitude", "").strip() and record.get("CurrentLongitude", "").strip():
            try:
                t = datetime.strptime(record["FirstSeen"], "%Y-%m-%d %H:%M:%S")
            except Exception:
                continue
            if fallback_gps is None or t > fallback_gps_time:
                fallback_gps = record
                fallback_gps_time = t

    if fallback_gps:
        for rec in merged.values():
            if rec.get("Type") == "BLE":
                if not rec.get("Channel", "").strip() or not rec.get("CurrentLatitude", "").strip():
                    rec["Channel"] = fallback_gps.get("Channel", "")
                    rec["CurrentLatitude"] = fallback_gps.get("CurrentLatitude", "")
                    rec["CurrentLongitude"] = fallback_gps.get("CurrentLongitude", "")
                    rec["AltitudeMeters"] = fallback_gps.get("AltitudeMeters", "")
                    rec["AccuracyMeters"] = fallback_gps.get("AccuracyMeters", "")
    return merged

# -----------------------------------------------------------------------------
# Function: write_merged_csv
# -----------------------------------------------------------------------------
def write_merged_csv(merged_data: dict, output_filepath: str) -> None:
    """
    Write the merged data to the output CSV file with two header lines.
    Records are sorted by the 'FirstSeen' field.
    
    Args:
        merged_data (dict): Merged records keyed by MAC.
        output_filepath (str): Full path to the output CSV file.
    """
    try:
        with open(output_filepath, "w", newline="", encoding="utf-8") as f:
            f.write(HEADER_LINE_1 + "\n")
            f.write(HEADER_LINE_2 + "\n")
            writer = csv.writer(f)
            sorted_records = sorted(merged_data.values(), key=lambda r: r.get("FirstSeen", ""))
            for record in sorted_records:
                row = [record.get(col, "") for col in OUTPUT_COLUMNS]
                writer.writerow(row)
        logging.debug("Merged CSV file updated: %s", output_filepath)
    except Exception as e:
        logging.error("Error writing merged CSV file %s: %s", output_filepath, e, exc_info=True)

# -----------------------------------------------------------------------------
# Main Loop: Continuously update merged CSV file and log summary every 10 iterations
# -----------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Continuously merge .bettercap and .kismet log files into one CSV."
    )
    parser.add_argument("--log-dir", type=str, default="~/wardrivelog",
                        help="Directory containing wardriving log files (default: ~/wardrivelog)")
    args = parser.parse_args()

    log_dir = os.path.expanduser(args.log_dir)
    if not os.path.isdir(log_dir):
        logging.error("Log directory '%s' does not exist.", log_dir)
        sys.exit(1)

    # Check and move preexisting CSV files to the upload directory.
    upload_dir = os.path.join(log_dir, "upload")
    check_and_move_csv_files(log_dir, upload_dir)

    logging.info("Merge process started. Monitoring log directory: %s", log_dir)

    now_str = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    output_filename = f"wardrive-{now_str}.csv"
    output_filepath = os.path.join(log_dir, output_filename)
    logging.info("Output CSV file: %s", output_filepath)

    # Snapshot of merged data from the last summary log.
    last_summary_data = {}
    summary_interval = 10

    iteration = 0
    waiting_counter = 0
    while True:
        iteration += 1
        try:
            bettercap_file = get_newest_file(log_dir, "bettercap")
            kismet_file = get_newest_file(log_dir, "kismet")
            if not (bettercap_file and kismet_file):
                waiting_counter += 1
                if waiting_counter % 5 == 0:
                    logging.info("Still waiting for .bettercap and valid .kismet files in %s.", log_dir)
                import time
                time.sleep(1)
                continue
            else:
                waiting_counter = 0

            # Extract base date from the bettercap filename.
            base_date_match = re.match(r"(\d{4}-\d{2}-\d{2})_", os.path.basename(bettercap_file))
            base_date = base_date_match.group(1) if base_date_match else ""
            bettercap_data = parse_bettercap_file(bettercap_file, base_date)
            kismet_data = parse_kismet_file(kismet_file)
            merged_data = merge_logs(bettercap_data, kismet_data)
            write_merged_csv(merged_data, output_filepath)

            # Every summary_interval iterations, compute cumulative changes since last summary.
            if iteration % summary_interval == 0:
                new_lines = 0
                updates = 0
                if not last_summary_data:
                    new_lines = len(merged_data)
                else:
                    for mac, record in merged_data.items():
                        if mac not in last_summary_data:
                            new_lines += 1
                        else:
                            if record != last_summary_data[mac]:
                                updates += 1
                logging.info("Iteration #%d: merged CSV file updated. New lines: %d, Updates: %d",
                             iteration, new_lines, updates)
                last_summary_data = {k: record.copy() for k, record in merged_data.items()}
        except Exception as e:
            logging.error("Unexpected error during merge iteration %d: %s", iteration, e, exc_info=True)

        try:
            import time
            time.sleep(1)
        except KeyboardInterrupt:
            logging.info("Keyboard interrupt received. Exiting merge loop.")
            break

if __name__ == "__main__":
    main()
