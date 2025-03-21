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
            "socket_timeout_sec": 10,
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
if os.path.exists(log_file_path):
    open(log_file_path, "w").close()

logger.remove()
if logging_settings.get("log_to_file", True):
    logger.add(log_file_path, level=logging_settings["log_level"].upper())
if logging_settings.get("log_to_terminal", True):
    logger.add(sys.stderr, level=logging_settings["log_level"].upper(), colorize=True,
               format="<yellow>{time:DD/MM @ HH:mm:ss}</yellow><red>| GPS |</red><level>{level:^7}</level> <red>|</red> <cyan>{message}</cyan>")
logger.success("Logger initialized.")

UDP_IP = gps_settings["udp_ip"]
UDP_PORT = gps_settings["udp_port"]
BUFFER_SIZE = gps_settings["buffer_size"]
SOCKET_TIMEOUT = gps_settings["socket_timeout_sec"]

udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
try:
    udp_socket.bind((UDP_IP, UDP_PORT))
    udp_socket.settimeout(SOCKET_TIMEOUT)
    logger.success(f"UDP socket bound to {UDP_IP}:{UDP_PORT}")
except Exception as e:
    logger.critical(f"Socket binding failed: {e}")
    sys.exit(1)

active_devices = {}
buffer_full_counts = {}

def create_virtual_device(device_name):
    master_fd, slave_fd = pty.openpty()
    device_path = os.ttyname(slave_fd)
    symlink_path = f"/dev/tty{device_name}"
    try:
        subprocess.run(["sudo", "ln", "-sf", device_path, symlink_path], check=True)
        logger.success(f"Created virtual device: {symlink_path} -> {device_path}")
    except subprocess.CalledProcessError as e:
        logger.critical(f"Failed to create symlink for {device_name}: {e}")
        return None
    flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
    fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
    buffer_full_counts[device_name] = 0
    return master_fd

def cleanup_virtual_device(device_name, fd):
    symlink_path = f"/dev/tty{device_name}"
    request_file_path = os.path.join(requests_directory, device_name)
    try:
        subprocess.run(["sudo", "rm", "-f", symlink_path], check=True)
        logger.info(f"Virtual device {symlink_path} removed.")
    except subprocess.CalledProcessError as e:
        logger.critical(f"Virtual device {symlink_path} removal failed: {e}")
    if os.path.exists(request_file_path):
        try:
            os.remove(request_file_path)
            logger.info(f"Removed stale request file {request_file_path}")
        except Exception as e:
            logger.error(f"Error removing {request_file_path}: {e}")
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
        logger.error(f"Checksum error: {e}")
        return False

def convert_to_decimal(degree_min_str, direction):
    try:
        if direction in ['N', 'S']:
            degrees = int(degree_min_str[:2])
            minutes = float(degree_min_str[2:])
        else:
            degrees = int(degree_min_str[:3])
            minutes = float(degree_min_str[3:])
        decimal_value = degrees + minutes / 60.0
        return -decimal_value if direction in ['S', 'W'] else decimal_value
    except Exception as e:
        logger.error(f"Conversion error for {degree_min_str} {direction}: {e}")
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
        logger.error(f"GGA parse error: {e}")
        return None

def parse_rmc(sentence):
    try:
        fields = sentence.split(',')
        if len(fields) < 8:
            return None
        speed_knots = float(fields[7]) if fields[7] else None
        return {"speed": speed_knots * 1.852 if speed_knots is not None else None}
    except Exception as e:
        logger.error(f"RMC parse error: {e}")
        return None

def monitor_requests():
    current_requests = set(f for f in os.listdir(requests_directory)
                           if f.startswith("GPS_") and not f.endswith((".log", ".zip")))
    for request_file in current_requests:
        if request_file not in active_devices:
            device_fd = create_virtual_device(request_file)
            if device_fd is not None:
                active_devices[request_file] = device_fd
    for device_name in list(active_devices.keys()):
        if device_name not in current_requests:
            logger.warning(f"Virtual device {device_name} disconnected, cleaning up.")
            cleanup_virtual_device(device_name, active_devices[device_name])
            active_devices.pop(device_name, None)

def main_loop():
    last_check_time = time.time()
    last_stats_time = time.time()
    last_valid_time = time.time()
    last_fix_log_time = time.time()
    last_summary_time = time.time()
    valid_update_count = 0
    stats_update_count = 0
    window_valid_updates = 0
    window_speed_sum = 0.0
    window_speed_count = 0
    error_logged = False
    connection_lost = False
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
                    logger.warning(f"Ignored malformed sentence: {sentence}")
                    continue
                if validate_checksum(sentence):
                    last_valid_time = time.time()
                    if connection_lost:
                        logger.success("GPS Reconnected.")
                        connection_lost = False
                    error_logged = False
                    if sentence.startswith('$GPGGA'):
                        gga_data = parse_gga(sentence)
                        if gga_data:
                            gps_fix.update(gga_data)
                    elif sentence.startswith('$GPRMC'):
                        rmc_data = parse_rmc(sentence)
                        if rmc_data:
                            gps_fix["speed"] = rmc_data.get("speed")
                            if rmc_data.get("speed") is not None:
                                window_speed_sum += rmc_data.get("speed")
                                window_speed_count += 1
                    for device_name, device_fd in list(active_devices.items()):
                        try:
                            if buffer_full_counts.get(device_name, 0) > 0:
                                logger.warning(f"Buffer full on /dev/tty{device_name}, skipping write ({buffer_full_counts[device_name]}/10).")
                            os.write(device_fd, (sentence + "\n").encode())
                            buffer_full_counts[device_name] = 0
                        except BlockingIOError:
                            logger.warning(f"Buffer full on /dev/tty{device_name}, skipping write ({buffer_full_counts[device_name]}/10).")
                            buffer_full_counts[device_name] += 1
                            if buffer_full_counts[device_name] > 10:
                                logger.critical(f"Device /dev/tty{device_name} unresponsive. Cleaning up.")
                                cleanup_virtual_device(device_name, device_fd)
                                active_devices.pop(device_name, None)
                        except OSError as e:
                            logger.error(f"Write error on /dev/tty{device_name}: {e}")
                    if gps_settings["log_gps_data"]:
                        with open(gps_log_path, "a") as gps_log_file:
                            gps_log_file.write(sentence + "\n")
                    valid_update_count += 1
                    stats_update_count += 1
                    window_valid_updates += 1
                    if time.time() - last_stats_time >= 5:
                        updates_per_sec = stats_update_count / (time.time() - last_stats_time)
                        logger.info(f"GPS updates per second: {updates_per_sec:.2f}")
                        stats_update_count = 0
                        last_stats_time = time.time()
                    if (current_time - last_valid_time < 30) and (current_time - last_fix_log_time >= 5):
                        latitude = f"{gps_fix['latitude']:.3f}" if gps_fix['latitude'] is not None else "N/A"
                        longitude = f"{gps_fix['longitude']:.3f}" if gps_fix['longitude'] is not None else "N/A"
                        altitude = f"{gps_fix['altitude']:.3f}" if gps_fix['altitude'] is not None else "N/A"
                        satellites = gps_fix['satellites'] if gps_fix['satellites'] is not None else "N/A"
                        speed = f"{gps_fix['speed']:.3f}" if gps_fix['speed'] is not None else "N/A"
                        logger.info(f"Latitude: {latitude}")
                        logger.info(f"Longitude: {longitude}")
                        logger.info(f"Altitude: {altitude} m")
                        logger.info(f"Satellites: {satellites}")
                        logger.info(f"Speed: {speed} km/h")
                        last_fix_log_time = current_time
                else:
                    logger.warning(f"Invalid checksum: {sentence}")
            logger.trace(" | ".join([
                f"Latitude: {gps_fix['latitude']:.3f}" if gps_fix['latitude'] is not None else "Latitude: N/A",
                f"Longitude: {gps_fix['longitude']:.3f}" if gps_fix['longitude'] is not None else "Longitude: N/A",
                f"Altitude: {gps_fix['altitude']:.3f} m" if gps_fix['altitude'] is not None else "Altitude: N/A",
                f"Satellites: {gps_fix['satellites']}" if gps_fix['satellites'] is not None else "Satellites: N/A",
                f"Speed: {gps_fix['speed']:.3f} km/h" if gps_fix['speed'] is not None else "Speed: N/A"
            ]))
        except socket.timeout:
            current_time = time.time()
            if current_time - last_valid_time >= 30:
                if not error_logged:
                    logger.error("No GPS connection for more than 30 seconds.")
                    error_logged = True
                    connection_lost = True
            else:
                logger.warning("GPS connection lost: Check your GPS Device.")
            continue
        except KeyboardInterrupt:
            logger.success("Terminated by user.")
            sys.exit(0)
        except Exception as e:
            logger.critical(f"Unexpected error: {e}")
            sys.exit(1)
        if time.time() - last_summary_time >= 30:
            average_speed = window_speed_sum / window_speed_count if window_speed_count > 0 else 0.0
            logger.success(f"30-second summary: {window_valid_updates} valid updates, average speed: {average_speed:.2f} km/h")
            window_valid_updates = 0
            window_speed_sum = 0.0
            window_speed_count = 0
            last_summary_time = time.time()

if __name__ == "__main__":
    logger.success("GPS Logger started.")
    main_loop()
