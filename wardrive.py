#!/usr/bin/env python3
import os
import sys
import time
import signal
import subprocess
import json
from loguru import logger
from datetime import datetime
import csv
import glob

# Global flags for logging state
error_flag = False
warning_flag = False
success_flag = False
critical_flag = False

# Define paths
wardrive_log_dir = os.path.join(os.getcwd(), "logs", "wardrive_logs")
wardrive_data_path = os.path.join(wardrive_log_dir, "wardrive_data.log")
log_file_path = os.path.join(wardrive_log_dir, "wardrive_monitor.log")

# Add these global variables
session_start_time = None
last_csv_line_count = 0  # Track number of lines in CSV
last_logged_wifi_count = 0
last_logged_ble_count = 0

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

def load_settings(file_path="settings.json"):
    default_settings = {
        "WARDRIVE_SETTINGS": {
            "check_interval": 5,
            "max_restart_attempts": 3,
            "restart_delay": 5
        },
        "LOGGING_SETTINGS": {
            "log_level": "INFO",
            "log_to_file": True,
            "log_to_terminal": True
        }
    }
    try:
        with open(file_path, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        print(f"Settings file '{file_path}' missing/invalid. Creating default...")
        try:
            with open(file_path, "w") as f:
                json.dump(default_settings, f, indent=2)
            print(f"Default settings written to '{file_path}'.")
            return default_settings
        except Exception as e:
            print(f"Failed to create settings file: {e}")
            sys.exit(1)
    except Exception as e:
        print(f"Unexpected error: {e}")
        sys.exit(1)

def setup_logging():
    # Create log directories
    os.makedirs(wardrive_log_dir, exist_ok=True)
    
    # Clear log files at startup
    if os.path.exists(log_file_path):
        open(log_file_path, "w").close()
    if os.path.exists(wardrive_data_path):
        open(wardrive_data_path, "w").close()
    
    # Configure logger
    logger.remove()
    fmt = "<yellow>{time:DD/MM @ HH:mm:ss.SSS} </yellow><blue>|</blue><level>{level:^9}</level><blue>|</blue><magenta> WARDRIVE </magenta><cyan>{message}</cyan>"
    logger.add(sys.stderr, level="INFO", colorize=True, format=fmt)
    logger.add(log_file_path, level="INFO", format=fmt)

def start_script(script_name):
    try:
        process = subprocess.Popen(
            [sys.executable, script_name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        return process
    except Exception as e:
        set_log_level("error", f"Failed to start {script_name}: {e}")
        return None

def check_process(process, script_name):
    if process is None or process.poll() is not None:
        return False
    return True

def restart_script(process, script_name, settings):
    if process is not None:
        try:
            process.terminate()
            process.wait(timeout=5)
        except:
            try:
                process.kill()
            except:
                pass
    
    time.sleep(settings["WARDRIVE_SETTINGS"]["restart_delay"])
    return start_script(script_name)

def get_script_status():
    log_files = {
        'GPS': os.path.join(os.getcwd(), "logs", "gps_logs", "gps_monitor.log"),
        'WiFi': os.path.join(os.getcwd(), "logs", "kismet_logs", "kismet_monitor.log"),
        'BLE': os.path.join(os.getcwd(), "logs", "bettercap_logs", "bettercap_monitor.log"),
        'MERGE': os.path.join(os.getcwd(), "logs", "merger_logs", "merger_monitor.log")
    }
    
    statuses = {}
    for device, log_file in log_files.items():
        try:
            if os.path.exists(log_file):
                with open(log_file, 'r') as f:
                    lines = f.readlines()
                    if lines:
                        last_line = lines[-1].strip()
                        if "SUCCESS" in last_line:
                            statuses[device] = "SUCCESS"
                        elif "WARNING" in last_line:
                            statuses[device] = "WARNING"
                        elif "ERROR" in last_line:
                            statuses[device] = "ERROR"
                        elif "CRITICAL" in last_line:
                            statuses[device] = "CRITICAL"
                        else:
                            statuses[device] = "UNKNOWN"
                    else:
                        statuses[device] = "UNKNOWN"
            else:
                statuses[device] = "UNKNOWN"
        except Exception as e:
            statuses[device] = "UNKNOWN"
            
    return statuses

def update_wardrive_status():
    statuses = get_script_status()
    
    # Case 1: If WiFi and BLE are both CRITICAL but GPS is SUCCESS
    if (statuses.get('WiFi') == "CRITICAL" and 
        statuses.get('BLE') == "CRITICAL" and 
        statuses.get('GPS') == "SUCCESS"):
        set_log_level("error", "WiFi & BLE DOWN.")
        return
    
    # Case 2: If GPS, BLE, and WiFi are SUCCESS but merger has issues
    if (statuses.get('GPS') == "SUCCESS" and 
        statuses.get('BLE') == "SUCCESS" and 
        statuses.get('WiFi') == "SUCCESS" and 
        statuses.get('MERGE') in ["ERROR", "CRITICAL"]):
        set_log_level("error", "MERGING FAILED.")
        return
    
    # Case 3: If GPS is WARNING
    if statuses.get('GPS') == "WARNING":
        set_log_level("warning", "GPS DATA UNSTABLE.")
        return
    
    # If GPS is not SUCCESS or WARNING, this is highest priority
    if statuses.get('GPS') not in ["SUCCESS", "WARNING"]:
        set_log_level("error", "GPS DOWN.")
        return
    
    # If all devices are SUCCESS (or merger is WARNING which counts as SUCCESS)
    if all(status == "SUCCESS" or (device == "MERGE" and status == "WARNING") 
           for device, status in statuses.items()):
        set_log_level("success", "STARTED.")
        return
    
    # If we get here and at least one of WiFi/BLE is not SUCCESS, show warning
    for device in ['BLE', 'WiFi']:
        if statuses.get(device) != "SUCCESS":
            set_log_level("warning", f"{device} DOWN.")
            return

def clear_log_files():
    log_directories = [
        os.path.join(os.getcwd(), "logs", "gps_logs"),
        os.path.join(os.getcwd(), "logs", "kismet_logs"),
        os.path.join(os.getcwd(), "logs", "bettercap_logs"),
        os.path.join(os.getcwd(), "logs", "merger_logs"),
        os.path.join(os.getcwd(), "logs", "wardrive_logs")
    ]
    
    for directory in log_directories:
        if os.path.exists(directory):
            for file in os.listdir(directory):
                if file.endswith('.log'):
                    file_path = os.path.join(directory, file)
                    try:
                        open(file_path, 'w').close()  # Clear file contents
                    except Exception as e:
                        print(f"Failed to clear {file_path}: {e}")

def update_wardrive_data():
    global session_start_time, last_csv_line_count
    global last_logged_wifi_count, last_logged_ble_count
    
    if session_start_time is None:
        return
        
    # Look for the most recent CSV file in the processing directory
    processing_dir = os.path.join(os.getcwd(), "logs", "wardrive_logs", "processing")
    if not os.path.exists(processing_dir):
        return
        
    try:
        # Get the most recent CSV file
        csv_files = glob.glob(os.path.join(processing_dir, "wardrive-*.csv"))
        if not csv_files:
            return
            
        latest_csv = max(csv_files, key=os.path.getmtime)
        
        # Read CSV file
        with open(latest_csv, 'r') as f:
            lines = f.readlines()
            if len(lines) < 3:  # Need header lines plus at least one data line
                return
                
            # Only process if there are new lines
            current_line_count = len(lines)
            if current_line_count <= last_csv_line_count:
                return
                
            # Parse CSV data
            reader = csv.DictReader(lines[2:], fieldnames=lines[1].strip().split(','))
            data = list(reader)
            
            if not data:
                return
                
            # Calculate session duration
            session_duration = datetime.now() - session_start_time
            hours, remainder = divmod(session_duration.seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            
            # Count WiFi and BLE devices
            wifi_count = sum(1 for device in data if device.get('Type', '').upper() == 'WIFI')
            ble_count = sum(1 for device in data if device.get('Type', '').upper() == 'BLE')
            
            # Update log
            with open(wardrive_data_path, 'a') as log:
                log.write(f"SESSION CLOCK: {hours:02d}:{minutes:02d}:{seconds:02d} "
                         f"DEVICES LOGGED: WIFI:{wifi_count} BLE:{ble_count}\n")
            
            # Update tracking variables
            last_csv_line_count = current_line_count
            
    except Exception as e:
        set_log_level("error", f"Failed to update wardrive data: {e}")

def monitor_scripts():
    settings = load_settings()
    check_interval = settings["WARDRIVE_SETTINGS"]["check_interval"]
    max_restart_attempts = settings["WARDRIVE_SETTINGS"]["max_restart_attempts"]
    
    # Start all scripts
    scripts = {
        "gps_logger.py": None,
        "kismet_logger.py": None,
        "bettercap_logger.py": None,
        "merger.py": None,
        "wigle_sync.py": None,
        "stats.py": None
    }
    
    restart_counts = {script: 0 for script in scripts}
    
    # Start initial processes
    for script_name in scripts:
        scripts[script_name] = start_script(script_name)
    
    while True:
        for script_name, process in scripts.items():
            if not check_process(process, script_name):
                if restart_counts[script_name] < max_restart_attempts:
                    set_log_level("warning", f"Restarting {script_name}")
                    scripts[script_name] = restart_script(process, script_name, settings)
                    restart_counts[script_name] += 1
                else:
                    set_log_level("critical", f"Failed to keep {script_name} running after {max_restart_attempts} attempts")
                    sys.exit(1)
            else:
                restart_counts[script_name] = 0  # Reset counter on successful run
        
        # Update overall wardrive status
        update_wardrive_status()
        
        # Update wardrive data
        update_wardrive_data()
        
        time.sleep(check_interval)

def handle_exit(sig, frame):
    set_log_level("critical", "STOPPED.")
    sys.exit(0)

def main():
    global session_start_time
    
    signal.signal(signal.SIGINT, handle_exit)
    signal.signal(signal.SIGTERM, handle_exit)
    
    # Clear all log files at startup
    clear_log_files()
    setup_logging()
    
    set_log_level("success", "STARTED SESSION.")
    session_start_time = datetime.now()
    
    try:
        monitor_scripts()
    except KeyboardInterrupt:
        handle_exit(signal.SIGINT, None)
    except Exception as e:
        set_log_level("critical", f"UNEXPECTED ERROR: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main() 
