#!/usr/bin/env python3
import socket
import json
import os
import sys
import pty
import subprocess
import fcntl
import time
from loguru import logger

def load_settings(file_path="gps_settings.json"):
    default_settings = {
        "GPS_SETTINGS": {
            "udp_ip": "172.20.10.3",
            "udp_port": 11123,
            "buffer_size": 4096,
            "socket_timeout_sec": 1,
            "log_gps_data": True,
            "gps_log_path": "~/.local/bin/wardriver/logs/gps_data.log",
            "requests_dir": "~/.local/bin/wardriver/logs/"
        },
        "LOGGING_SETTINGS": {
            "log_to_file": True,
            "log_to_terminal": True,
            "log_file_path": "~/.local/bin/wardriver/logs/gps_monitor.log",
            "log_level": "TRACE"
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

settings = load_settings()
gps_settings = settings["GPS_SETTINGS"]
logging_settings = settings["LOGGING_SETTINGS"]

gps_log_path = os.path.expanduser(gps_settings["gps_log_path"])
log_file_path = os.path.expanduser(logging_settings["log_file_path"])
requests_directory = os.path.expanduser(gps_settings["requests_dir"])

os.makedirs(requests_directory, exist_ok=True)

logger.remove()
if logging_settings.get("log_to_file", True):
    logger.add(log_file_path, level=logging_settings["log_level"].upper())
if logging_settings.get("log_to_terminal", True):
    logger.add(sys.stderr, level=logging_settings["log_level"].upper(), colorize=True, format="<yellow>{time:DD/MM @ HH:mm:ss.SSS}</yellow><red>| GPS |</red><level>{level:^7}</level><red>|</red> <cyan>{message}</cyan>")
if os.path.exists(log_file_path):
    open(log_file_path, "w").close()
    logger.info(f"CLEARED {os.path.basename(log_file_path)}")
if os.path.exists(gps_log_path):
    open(gps_log_path, "w").close()
    logger.info(f"CLEARED {os.path.basename(gps_log_path)}")
logger.info("STARTING GPS LOG.")

UDP_IP = gps_settings["udp_ip"]
UDP_PORT = gps_settings["udp_port"]
BUFFER_SIZE = gps_settings["buffer_size"]
SOCKET_TIMEOUT = gps_settings["socket_timeout_sec"]

udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
attempt_interval = 1
attempt_count = 0
warning_logged = False
while True:
    try:
        udp_socket.bind((UDP_IP, UDP_PORT))
        udp_socket.settimeout(SOCKET_TIMEOUT)
        logger.info("GPS CONNECTED SUCCESSFULLY.")
        break
    except Exception:
        attempt_count += 1
        if not warning_logged:
            logger.info("SCANNING FOR GPS DEVICES.")
            warning_logged = True
        if attempt_count == 31:
            logger.error("NO GPS DEVICE CONNECTED.")
        time.sleep(attempt_interval)

active_devices = {}
buffer_full_counts = {}
connection_error_logged = False

def create_virtual_device(device_name):
    try:
        master_fd, slave_fd = pty.openpty()
        device_path = os.ttyname(slave_fd)
        symlink_path = f"/dev/tty{device_name}"
        subprocess.run(["sudo", "ln", "-sf", device_path, symlink_path], check=True)
        logger.info(f"CREATED VIRTUAL DEVICE ON {device_path}")
        flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
        fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        buffer_full_counts[device_name] = 0
        return master_fd
    except subprocess.CalledProcessError as e:
        logger.error(f"VIRTUAL DEVICE {device_name} CREATION FAILED: {e}")
        return None

def cleanup_virtual_device(device_name, fd):
    symlink_path = f"/dev/tty{device_name}"
    request_file_path = os.path.join(requests_directory, device_name)
    try:
        subprocess.run(["sudo", "rm", "-f", symlink_path], check=True)
        logger.info(f"REMOVED VIRTUAL DEVICE {symlink_path}")
    except subprocess.CalledProcessError as e:
        logger.error(f"VIRTUAL DEVICE {symlink_path} REMOVAL FAILED: {e}")
    if os.path.exists(request_file_path):
        try:
            os.remove(request_file_path)
            logger.success(f"{request_file_path} REMOVED")
        except Exception as e:
            logger.error(f"CANT REMOVE {request_file_path}: {e}")
    try:
        os.close(fd)
    except Exception:
        pass
    buffer_full_counts.pop(device_name, None)

def validate_checksum(sentence):
    try:
        sentence_body, checksum_str = sentence.strip().split('*')
        sentence_body = sentence_body.lstrip('$')
        calculated_checksum = 0
        for char in sentence_body:
            calculated_checksum ^= ord(char)
        return int(checksum_str, 16) == calculated_checksum
    except Exception as e:
        logger.error(f"FILE ERROR {e}")
        return False

def convert_to_decimal(degree_min_str, direction):
    try:
        if direction in ['N', 'S']:
            degrees = int(degree_min_str[:2])
            minutes = float(degree_min_str[2:])
        else:
            degrees = int(degree_min_str[:3])
            minutes = float(degree_min_str[3:])
        decimal_value = degrees + (minutes / 60.0)
        return -decimal_value if direction in ['S', 'W'] else decimal_value
    except Exception as e:
        logger.error(f"DATA MANIPULATION ERROR: {degree_min_str} {direction}: {e}")
        return None

def parse_gga(sentence):
    try:
        fields = sentence.split(',')
        if len(fields) < 10:
            return None
        latitude = convert_to_decimal(fields[2], fields[3]) if fields[2] and fields[3] else None
        longitude = convert_to_decimal(fields[4], fields[5]) if fields[4] and fields[5] else None
        satellites = int(fields[7]) if fields[7] else None
        altitude = float(fields[9]) if fields[9] else None
        return {"latitude": latitude, "longitude": longitude, "altitude": altitude, "satellites": satellites}
    except Exception as e:
        logger.error(f"GGA PARSE ERROR: {e}")
        return None

def parse_rmc(sentence):
    try:
        fields = sentence.split(',')
        if len(fields) < 8:
            return None
        speed_knots = float(fields[7]) if fields[7] else None
        return {"speed": speed_knots * 1.852 if speed_knots is not None else None}
    except Exception as e:
        logger.error(f"RMC PARSE ERROR: {e}")
        return None

def monitor_requests():
    current_requests = set(f for f in os.listdir(requests_directory) if f.startswith("GPS_") and not f.endswith((".log", ".zip")))
    for request_file in current_requests:
        if request_file not in active_devices:
            device_fd = create_virtual_device(request_file)
            if device_fd is not None:
                active_devices[request_file] = device_fd
    for device_name in list(active_devices.keys()):
        if device_name not in current_requests:
            logger.info(f"DISCONNECTING VIRTUAL DEVICE {device_name}")
            cleanup_virtual_device(device_name, active_devices[device_name])
            active_devices.pop(device_name, None)

def main_loop():
    global udp_socket, connection_error_logged
    last_check_time = time.time()
    last_valid_time = time.time()
    stable_start_time = None
    connection_lost = False
    summary_logged = False
    stable_logged = False
    gps_fix = {"latitude": None, "longitude": None, "altitude": None, "satellites": None, "speed": None}
    while True:
        try:
            current_time = time.time()
            if current_time - last_check_time > 2:
                monitor_requests()
                last_check_time = current_time
            data, addr = udp_socket.recvfrom(BUFFER_SIZE)
            raw_data = data.decode(errors="ignore").strip()
            for sentence in [line.strip() for line in raw_data.splitlines() if line.strip()]:
                if not sentence.startswith('$'):
                    logger.warning("MALFORMED SENTENCE IGNORED.")
                    continue
                if validate_checksum(sentence):
                    last_valid_time = current_time
                    if connection_lost:
                        logger.info("GPS DATA IS FLOWING.")
                        connection_lost = False
                        stable_start_time = current_time
                        summary_logged = False
                    elif stable_start_time is None:
                        stable_start_time = current_time
                    if sentence.startswith('$GPGGA'):
                        gga_data = parse_gga(sentence)
                        if gga_data:
                            gps_fix.update(gga_data)
                    elif sentence.startswith('$GPRMC'):
                        rmc_data = parse_rmc(sentence)
                        if rmc_data:
                            gps_fix["speed"] = rmc_data.get("speed")
                    for device_name, device_fd in list(active_devices.items()):
                        try:
                            if buffer_full_counts.get(device_name, 0) > 0:
                                logger.warning(f"{device_name} BUFFER FULL: ({buffer_full_counts[device_name]}/10).")
                            os.write(device_fd, (sentence + "\n").encode())
                            buffer_full_counts[device_name] = 0
                        except BlockingIOError:
                            logger.warning(f"{device_name} BUFFER FULL: ({buffer_full_counts[device_name]}/10).")
                            buffer_full_counts[device_name] += 1
                            if buffer_full_counts[device_name] > 10:
                                logger.error(f"{device_name} IS NOT RESPONDING.")
                                cleanup_virtual_device(device_name, device_fd)
                                active_devices.pop(device_name, None)
                        except OSError as e:
                            logger.error(f"{device_name}: ERROR: {e}")
                    if gps_settings["log_gps_data"]:
                        with open(gps_log_path, "a") as gps_log_file:
                            gps_log_file.write(sentence + "\n")
                else:
                    logger.warning(f"SOMETHING DOESN'T LOOK RIGHT {sentence}")
        except socket.timeout:
            current_time = time.time()
            if current_time - last_valid_time >= 30:
                if not connection_error_logged:
                    logger.error("GPS DATA FLOW STOPPED, RESTARTING GPS.")
                    connection_error_logged = True
                try:
                    udp_socket.close()
                except Exception:
                    pass
                udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                attempt_count = 0
                warning_logged = False
                while True:
                    try:
                        udp_socket.bind((UDP_IP, UDP_PORT))
                        udp_socket.settimeout(SOCKET_TIMEOUT)
                        logger.info("GPS RESTARTED.")
                        break
                    except Exception as e:
                        attempt_count += 1
                        if not warning_logged:
                            logger.info("SCANNING FOR GPS DEVICE.")
                            warning_logged = True
                        if attempt_count == 31:
                            logger.error("NO GPS DEVICE FOUND.")
                        time.sleep(attempt_interval)
                return
            else:
                if not connection_lost:
                    logger.info("GPS DATA IS NOT FLOWING.")
                    connection_lost = True
                continue
        except KeyboardInterrupt:
            logger.critical("CLOSING GPS CONNECTION")
            sys.exit(0)
        except Exception as e:
            logger.critical(f"SOMETHING HAPPENED: {e}")
            sys.exit(1)
        if (not connection_lost) and (stable_start_time is not None) and (time.time() - stable_start_time >= 5) and (not summary_logged):
            if not stable_logged:
                logger.success("CONNECTION STABLE.")
                stable_logged = True
                summary_logged = True
                connection_error_logged = False

if __name__ == "__main__":
    logger.info("GPS LOGGING HAS BEGUN.")
    while True:
        main_loop()
