#!/usr/bin/env python3
import os
import subprocess
import json
import sys
import time
import signal
import psutil
import glob
from loguru import logger

def load_settings(file_path="kismet_settings.json"):
    with open(file_path, 'r') as file:
        return json.load(file)

settings = load_settings()
kismet_settings = settings["KISMET_SETTINGS"]
logging_settings = settings["LOGGING_SETTINGS"]

log_dir = os.path.expanduser("~/.local/bin/wardriver/logs/wardrive_logs/")
kismet_log_path = os.path.expanduser("~/.local/bin/wardriver/logs/kismet_logs/kismet_data.log")
log_file_path = os.path.expanduser("~/.local/bin/wardriver/logs/kismet_logs/kismet_logger.log")
kismet_launch_args = "-c wlan1 --no-remote"
run_as_sudo = False

MAX_RESTART_ATTEMPTS = 3
RESTART_BACKOFF_SECONDS = 15
KILL_RETRY_ATTEMPTS = 3
KILL_RETRY_WAIT_SECONDS = 2
SKIP_KILL_SELF = True
IGNORE_PATTERNS = [
    "point your browser to",
    "edit kismet.service",
    "readme for setting up kismet",
    "specify baud=",
    "check your gps documentation"
]
CLEANUP_EXTENSIONS = [".kismet", ".kismet-journal", ".wiglecsv"]

gps_device_name = kismet_settings["gps_device_name"]
request_file_path = os.path.expanduser(f"~/.local/bin/wardriver/logs/gps_logs/gps_devices/{gps_device_name}")
ignore_patterns = [pattern.lower() for pattern in IGNORE_PATTERNS]

logger.remove()
logger_format = "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <magenta>KISMET</magenta> | <level>{level: <7}</level> | <cyan>{message}</cyan>"
logger.add(sys.stderr, level=logging_settings["log_level"].upper(), colorize=True, format=logger_format)
logger.add(log_file_path, level=logging_settings["log_level"].upper(), format="{time} | KISMET | {level} | {message}")

def cleanup_old_logs():
    cleared_count = 0
    for ext in CLEANUP_EXTENSIONS:
        for file in glob.glob(os.path.join(log_dir, f"*{ext}")):
            try:
                open(file, 'w').close()
                cleared_count += 1
            except Exception:
                pass
    if cleared_count > 0:
        logger.info(f"Cleared {cleared_count} old Kismet log files")
    else:
        logger.info("No old Kismet log files found to clear")

def kill_existing_kismet():
    current_pid = os.getpid()
    retry_attempts = KILL_RETRY_ATTEMPTS
    wait_seconds = KILL_RETRY_WAIT_SECONDS
    for _ in range(retry_attempts):
        found_processes = False
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                if 'kismet' in (proc.info['name'] or '').lower() or any('kismet' in (arg.lower()) for arg in proc.info['cmdline'] or []):
                    if SKIP_KILL_SELF and proc.pid == current_pid:
                        continue
                    found_processes = True
                    subprocess.run(f"sudo kill -9 {proc.pid}", shell=True, check=False)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        if found_processes:
            time.sleep(wait_seconds)
        else:
            break

def update_kismet_conf(gps_device_name):
    kismet_conf_path = "/etc/kismet/kismet.conf"
    backup_path = "/etc/kismet/kismet.conf.bak"
    subprocess.run(["sudo", "cp", kismet_conf_path, backup_path], check=False)
    result = subprocess.run(["sudo", "cat", kismet_conf_path], check=True, stdout=subprocess.PIPE, text=True)
    conf_lines = result.stdout.splitlines()
    updated_lines, replaced = [], False
    for line in conf_lines:
        if line.strip().startswith("gps=serial:device="):
            updated_line = f"gps=serial:device=/dev/tty{gps_device_name},name=gps_logger"
            updated_lines.append(updated_line)
            replaced = True
        else:
            updated_lines.append(line)
    if not replaced:
        updated_lines.append(f"gps=serial:device=/dev/tty{gps_device_name},name=gps_logger")
    temp_conf_path = "/tmp/kismet.conf.updated"
    with open(temp_conf_path, 'w') as temp_conf:
        temp_conf.write('\n'.join(updated_lines) + '\n')
    subprocess.run(["sudo", "mv", temp_conf_path, kismet_conf_path], check=True)

def start_kismet():
    kill_existing_kismet()
    cleanup_old_logs()
    os.makedirs(log_dir, exist_ok=True)
    open(request_file_path, 'a').close()
    update_kismet_conf(gps_device_name)
    kismet_args = kismet_launch_args.split()
    cmd = (["sudo"] if run_as_sudo else []) + ["kismet"] + kismet_args + ["--log-prefix", log_dir]
    with open(kismet_log_path, "w") as kismet_log_file:
        process = subprocess.Popen(cmd, stdout=kismet_log_file, stderr=subprocess.STDOUT, preexec_fn=os.setsid)
    logger.success(f"Kismet started, output to {kismet_log_path}")
    return process

def follow_kismet_log(process):
    restart_attempts = 0
    with open(kismet_log_path, 'r') as logfile:
        logfile.seek(0, os.SEEK_END)
        while True:
            line = logfile.readline()
            if line:
                line = line.strip()
                if any(pattern in line.lower() for pattern in ignore_patterns):
                    continue
            else:
                time.sleep(0.05)
            if process.poll() is not None:
                logger.critical("Kismet process stopped")
                if restart_attempts < MAX_RESTART_ATTEMPTS:
                    restart_attempts += 1
                    logger.warning(f"Restarting attempt #{restart_attempts}")
                    time.sleep(RESTART_BACKOFF_SECONDS)
                    process = start_kismet()
                else:
                    logger.critical("Max restart attempts reached. Stopping.")
                    break

def cleanup():
    if os.path.exists(request_file_path):
        os.remove(request_file_path)
        logger.info("Cleaned up request file.")

def handle_exit(sig, frame):
    cleanup()
    logger.info("Watchdog exiting.")
    sys.exit(0)

if __name__ == '__main__':
    signal.signal(signal.SIGINT, handle_exit)
    signal.signal(signal.SIGTERM, handle_exit)
    process = start_kismet()
    follow_kismet_log(process)
    cleanup()
