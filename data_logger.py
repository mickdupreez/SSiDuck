#!/usr/bin/env python3
import os
import glob
import csv
import argparse
import signal
import sys
import shutil
import time
from datetime import datetime
from loguru import logger

error_flag = False
warning_flag = False
success_flag = False
critical_flag = False

def set_log_level(level, msg):
    global error_flag, warning_flag, success_flag, critical_flag
    if level == "trace":
        logger.trace(msg)
    elif level == "debug":
        logger.debug(msg)
    elif level == "info":
        logger.info(msg)
    elif level == "error" and not error_flag:
        logger.error(msg)
        error_flag = True
        warning_flag = False
        success_flag = False
        critical_flag = False
    elif level == "warning" and not warning_flag:
        logger.warning(msg)
        warning_flag = True
        error_flag = False
        success_flag = False
        critical_flag = False
    elif level == "success" and not success_flag:
        logger.success(msg)
        success_flag = True
        error_flag = False
        warning_flag = False
        critical_flag = False
    elif level == "critical" and not critical_flag:
        logger.critical(msg)
        critical_flag = True
        error_flag = False
        warning_flag = False
        success_flag = False

logger.remove()
fmt = "<yellow>{time:DD/MM @ HH:mm:ss.SSS} </yellow><blue>|</blue><level>{level:^9}</level><blue>|</blue><magenta> MERGE </magenta><blue>|</blue> <cyan>{message}</cyan>"
logger.add(sys.stderr, level="INFO", colorize=True, format=fmt)

def signal_handler(sig, frame):
    set_log_level("info", "Shutdown signal received. Exiting...")
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

def process_wigle_file(filepath, output_filepath, processed_macs):
    """Process the wigle file and append only new devices to the output file"""
    try:
        # Read the source file
        with open(filepath, 'r') as f:
            lines = f.readlines()
            if len(lines) < 2:  # Need at least header lines
                return False
            
            header1 = lines[0].strip()
            header2 = lines[1].strip()
            
            # Create output file with headers if it doesn't exist
            if not os.path.exists(output_filepath):
                with open(output_filepath, 'w') as outf:
                    outf.write(header1 + '\n')
                    outf.write(header2 + '\n')
            
            # Process new entries
            new_devices = 0
            with open(output_filepath, 'a') as outf:
                for line in lines[2:]:  # Skip headers
                    parts = line.strip().split(',')
                    if len(parts) > 0:
                        mac = parts[0]
                        if mac not in processed_macs:
                            outf.write(line)
                            processed_macs.add(mac)
                            new_devices += 1
            
            if new_devices > 0:
                set_log_level("debug", f"Added {new_devices} new devices from: {filepath}")
            return True
            
    except Exception as e:
        set_log_level("error", f"Error processing file {filepath}: {e}")
        return False

def start_new_session(processing_dir, upload_dir, current_file=None):
    """Start a new session by moving current file to upload and creating new one"""
    # Move current file to upload if it exists
    if current_file and os.path.exists(current_file):
        try:
            shutil.move(current_file, upload_dir)
            set_log_level("info", "Moved previous session file to upload directory")
        except Exception as e:
            set_log_level("error", f"Failed to move previous session file: {e}")

    # Create new session file
    now_str = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    output_filename = f"wardrive-{now_str}.csv"
    return os.path.join(processing_dir, output_filename)

def main():
    parser = argparse.ArgumentParser(description="Process and move wardrive data files")
    parser.add_argument("--log-dir", type=str, default="logs/wardrive_logs/", help="Directory containing wardrive logs")
    parser.add_argument("--interval", type=int, default=10, help="Status update interval in iterations")
    args = parser.parse_args()

    log_dir = os.path.expanduser(args.log_dir)
    processing_dir = os.path.join(log_dir, "processing")
    upload_dir = os.path.join(log_dir, "uploading")

    if not os.path.isdir(processing_dir):
        set_log_level("error", f"Processing dir '{processing_dir}' does not exist.")
        sys.exit(1)

    os.makedirs(upload_dir, exist_ok=True)

    # Move any existing processing files to upload at startup
    existing_files = glob.glob(os.path.join(processing_dir, "wardrive-*.csv"))
    for ef in existing_files:
        try:
            shutil.move(ef, upload_dir)
        except Exception as e:
            set_log_level("error", f"Failed to move existing file {ef}: {e}")

    output_filepath = start_new_session(processing_dir, upload_dir)
    last_mtime = None
    iteration = 0
    processed_macs = set()
    had_files = False
    
    while True:
        iteration += 1
        
        # Look for wigle files
        wigle_files = glob.glob(os.path.join(log_dir, "*.wiglecsv"))
        
        # Handle session transitions
        if wigle_files:
            if not had_files:  # No files before, but now we have some - start new session
                output_filepath = start_new_session(processing_dir, upload_dir, output_filepath)
                processed_macs.clear()
                last_mtime = None
            had_files = True
            
            latest_file = max(wigle_files, key=os.path.getmtime)
            current_mtime = os.path.getmtime(latest_file)
            
            # Only process if the file has been modified
            if current_mtime != last_mtime:
                if process_wigle_file(latest_file, output_filepath, processed_macs):
                    last_mtime = current_mtime
                    
                    if iteration % args.interval == 0:
                        set_log_level("info", f"Iteration #{iteration}: {len(processed_macs)} total devices")
        else:
            if had_files:  # Had files before, but now none - end session
                output_filepath = start_new_session(processing_dir, upload_dir, output_filepath)
                processed_macs.clear()
                last_mtime = None
            had_files = False

        time.sleep(1)

if __name__ == "__main__":
    main()
