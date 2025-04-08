#!/usr/bin/env python3
import socket
import json
import os
import sys
import time
import atexit

def load_settings(file_path="settings.json"):
    default_settings = {
        "GPS_SETTINGS": {
            "udp_ip": "172.20.10.3",
            "udp_port": 11123,
            "buffer_size": 53248,
            "socket_timeout_sec": 1
        }
    }
    try:
        with open(file_path, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        try:
            with open(file_path, "w") as f:
                json.dump(default_settings, f, indent=2)
            return default_settings
        except Exception as e:
            sys.exit(1)
    except Exception:
        sys.exit(1)

settings = load_settings()
gps_settings = settings["GPS_SETTINGS"]

UDP_IP = gps_settings["udp_ip"]
UDP_PORT = gps_settings["udp_port"]
BUFFER_SIZE = gps_settings["buffer_size"]
SOCKET_TIMEOUT = gps_settings["socket_timeout_sec"]
udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
attempt_interval = 1

# Define log file path globally
LOG_DIR = "logs/gps_logs"
LOG_FILE = os.path.join(LOG_DIR, "gps_data.json")

# Store latest GPS values
latest_gps_data = {
    "longitude": None,
    "latitude": None,
    "altitude": None,
    "speed": None,
    "satellites": None
}

def cleanup():
    """Delete the GPS log file during cleanup."""
    try:
        if os.path.exists(LOG_FILE):
            os.remove(LOG_FILE)
    except Exception:
        pass

def update_gps_log():
    """Update the GPS log file with current data."""
    temp_file = LOG_FILE + ".tmp"
    try:
        with open(temp_file, 'w') as f:
            json.dump({
                "longitude": latest_gps_data["longitude"],
                "latitude": latest_gps_data["latitude"],
                "altitude": latest_gps_data["altitude"],
                "speed": latest_gps_data["speed"],
                "satellites": latest_gps_data["satellites"]
            }, f, indent=2)
        # Atomic rename
        os.replace(temp_file, LOG_FILE)
    except Exception as e:
        if os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except:
                pass

def validate_checksum(sentence):
    try:
        sentence_body, checksum_str = sentence.strip().split('*')
        sentence_body = sentence_body.lstrip('$')
        calculated_checksum = 0
        for char in sentence_body:
            calculated_checksum ^= ord(char)
        return int(checksum_str, 16) == calculated_checksum
    except Exception:
        return False

def convert_to_decimal(degree_min_str, direction):
    try:
        if direction in ["N", "S"]:
            degrees = int(degree_min_str[:2])
            minutes = float(degree_min_str[2:])
        else:
            degrees = int(degree_min_str[:3])
            minutes = float(degree_min_str[3:])
        decimal_value = degrees + (minutes/60.0)
        return -decimal_value if direction in ["S", "W"] else decimal_value
    except Exception:
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
    except Exception:
        return None

def parse_rmc(sentence):
    try:
        fields = sentence.split(',')
        if len(fields) < 8:
            return None
        speed_knots = float(fields[7]) if fields[7] else None
        return {"speed": speed_knots * 1.852 if speed_knots is not None else None}
    except Exception:
        return None

def main_loop():
    global udp_socket
    last_valid_time = time.time()
    last_file_update = time.time()  # Track the last time we updated the file
    UPDATE_INTERVAL = 0.1
    
    while True:
        try:
            current_time = time.time()
            data, addr = udp_socket.recvfrom(BUFFER_SIZE)
            raw_data = data.decode(errors="ignore").strip()
            
            for sentence in [line.strip() for line in raw_data.splitlines() if line.strip()]:
                if not sentence.startswith('$'):
                    continue
                if validate_checksum(sentence):
                    last_valid_time = current_time
                    if sentence.startswith('$GPGGA'):
                        gga_data = parse_gga(sentence)
                        if gga_data:
                            latest_gps_data.update(gga_data)
                    elif sentence.startswith('$GPRMC'):
                        rmc_data = parse_rmc(sentence)
                        if rmc_data:
                            latest_gps_data.update(rmc_data)
                    
                    # Only update the file if enough time has passed
                    if current_time - last_file_update >= UPDATE_INTERVAL:
                        update_gps_log()
                        last_file_update = current_time
                
        except socket.timeout:
            current_time = time.time()
            if current_time - last_valid_time >= 10:
                try:
                    udp_socket.close()
                except Exception:
                    pass
                udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                while True:
                    try:
                        udp_socket.bind((UDP_IP, UDP_PORT))
                        udp_socket.settimeout(SOCKET_TIMEOUT)
                        break
                    except OSError as e:
                        if e.errno == 99 and UDP_IP != "0.0.0.0":
                            time.sleep(attempt_interval)
                return
            continue
        except KeyboardInterrupt:
            cleanup()
            sys.exit(0)
        except Exception:
            cleanup()
            sys.exit(1)

if __name__ == "__main__":
    try:
        # Ensure log directory exists
        os.makedirs(LOG_DIR, exist_ok=True)
        
        # Delete existing log file if it exists
        cleanup()
        
        # Register cleanup handler
        atexit.register(cleanup)
        
        while True:
            try:
                udp_socket.bind((UDP_IP, UDP_PORT))
                udp_socket.settimeout(SOCKET_TIMEOUT)
                break
            except OSError as e:
                if e.errno == 99 and UDP_IP != "0.0.0.0":
                    time.sleep(attempt_interval)
        while True:
            main_loop()
    except KeyboardInterrupt:
        print("\nKeyboard interrupt received, exiting...")
        sys.exit(0)
    except Exception:
        sys.exit(1)
