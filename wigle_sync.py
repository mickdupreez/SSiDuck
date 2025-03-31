#!/usr/bin/env python3
import os
import sys
import json
import glob
import argparse
import signal
from datetime import datetime
import requests
import time
import shutil
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

def load_settings(settings_path: str = "settings.json") -> dict:
    default_settings = {
        "WIGLE_SETTINGS": {
            "auth_token": "",
            "upload_url": "https://api.wigle.net/api/v2/file/upload"
        },
        "LOGGING_SETTINGS": {
            "log_level": "INFO",
            "log_to_file": True,
            "log_to_terminal": True
        }
    }
    try:
        with open(settings_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        print(f"Settings file '{settings_path}' missing/invalid. Creating default...")
        try:
            with open(settings_path, "w", encoding="utf-8") as f:
                json.dump(default_settings, f, indent=2)
            print(f"Default settings written to '{settings_path}'.")
            return default_settings
        except Exception as e:
            print(f"Failed to create settings file: {e}")
            sys.exit(1)
    except Exception as e:
        print(f"Unexpected error: {e}")
        sys.exit(1)

def check_internet_connection(test_url: str = "http://www.google.com", timeout: int = 5) -> bool:
    try:
        response = requests.get(test_url, timeout=timeout)
        return response.status_code == 200
    except Exception as e:
        set_log_level("error", f"Internet connection check failed: {e}")
        return False

def get_csv_files(upload_dir: str) -> list:
    return glob.glob(os.path.join(upload_dir, "*.csv"))

def count_files_in_dir(directory: str) -> int:
    return len(glob.glob(os.path.join(directory, "*.csv")))

def upload_file(file_path: str, url: str, headers: dict) -> bool:
    try:
        # Check line count before uploading
        with open(file_path, "r", encoding="utf-8") as f:
            line_count = sum(1 for _ in f)
        
        if line_count < 50:
            set_log_level("warning", f"Deleting small file {os.path.basename(file_path)} with {line_count} lines")
            os.remove(file_path)
            return False

        with open(file_path, "rb") as f:
            files = {"file": f}
            response = requests.post(url, files=files, headers=headers)
            return response.status_code == 200
    except Exception as e:
        set_log_level("error", f"Error uploading {os.path.basename(file_path)}: {e}")
        return False

def download_wiglestats_image(log_dir: str) -> None:
    image_url = "https://wigle.net/bi/BPvOYy811vlj2ck_bAFdNw.png"
    destination = os.path.join(log_dir, "wiglestats.png")
    try:
        response = requests.get(image_url, stream=True)
        if response.status_code == 200:
            with open(destination, "wb") as f:
                for chunk in response.iter_content(chunk_size=1024):
                    if chunk:
                        f.write(chunk)
    except Exception as e:
        set_log_level("error", f"Failed to download wiglestats image: {e}")

def main():
    try:
        parser = argparse.ArgumentParser(
            description="Continuously upload CSV files from the upload directory to wigle.net and update wiglestats image."
        )
        parser.add_argument("--log-dir", type=str, default="logs/wardrive_logs",
                            help="Directory containing wardriving log files")
        args = parser.parse_args()

        log_dir = args.log_dir
        if not os.path.isdir(log_dir):
            set_log_level("critical", f"Log directory '{log_dir}' does not exist")
            sys.exit(1)

        script_dir = os.path.dirname(os.path.abspath(__file__))
        settings_path = os.path.join(script_dir, "settings.json")

        settings = load_settings(settings_path)

        auth_token = settings.get("WIGLE_SETTINGS", {}).get("auth_token")
        if not auth_token:
            set_log_level("critical", "No auth_token found in settings")
            sys.exit(1)
        upload_url = settings.get("WIGLE_SETTINGS", {}).get("upload_url", "https://api.wigle.net/api/v2/file/upload")

        headers = {'Authorization': 'Basic ' + auth_token}

        upload_dir = os.path.join(log_dir, "uploading")
        uploaded_dir = os.path.join(log_dir, "uploaded")
        
        if not os.path.isdir(uploaded_dir):
            os.makedirs(uploaded_dir)

        stats_image_path = os.path.join(log_dir, "wiglestats.png")
        if not os.path.exists(stats_image_path):
            download_wiglestats_image(log_dir)

        # Count unuploaded files at startup
        unuploaded_count = count_files_in_dir(upload_dir)
        if unuploaded_count > 0:
            set_log_level("error", f"{unuploaded_count} NOT UPLOADED.")
        else:
            set_log_level("success", "SYNCED.")

        uploaded_flag = False
        waiting_counter = 0
        last_upload_count = 0

        while True:
            if not check_internet_connection():
                time.sleep(5)
                continue

            csv_files = get_csv_files(upload_dir)
            if not csv_files:
                if last_upload_count > 0:
                    set_log_level("warning", f"{last_upload_count} UPLOADED.")
                    last_upload_count = 0
                set_log_level("success", "SYNCED.")
                time.sleep(1)
                continue

            waiting_counter = 0
            current_upload_count = 0
            files_to_upload = len(csv_files)
            if files_to_upload > 0:
                set_log_level("warning", f"UPLOADING {files_to_upload} FILES.")

            for file_path in csv_files:
                if upload_file(file_path, upload_url, headers):
                    uploaded_flag = True
                    current_upload_count += 1
                    try:
                        shutil.move(file_path, uploaded_dir)
                    except Exception as e:
                        set_log_level("error", f"Failed to move file {os.path.basename(file_path)}: {e}")
                time.sleep(1)

            if current_upload_count > 0:
                last_upload_count = current_upload_count

            if uploaded_flag:
                download_wiglestats_image(log_dir)
                uploaded_flag = False
            time.sleep(1)

    except Exception as e:
        set_log_level("critical", f"Unexpected error: {e}")
        sys.exit(1)

def handle_exit(sig, frame):
    set_log_level("critical", "STOPPED.")
    sys.exit(0)

if __name__ == "__main__":
    signal.signal(signal.SIGINT, handle_exit)
    signal.signal(signal.SIGTERM, handle_exit)
    
    logger.remove()
    fmt = "<yellow>{time:DD/MM @ HH:mm:ss.SSS} </yellow><blue>|</blue><level>{level:^9}</level><blue>|</blue><magenta> WIGLE </magenta><cyan>{message}</cyan>"
    logger.add(sys.stderr, level="INFO", colorize=True, format=fmt)
    logger.add(os.path.join(os.getcwd(), "logs", "wigle_logs", "wigle_monitor.log"), level="INFO", format=fmt)
    
    main()
