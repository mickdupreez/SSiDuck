#!/usr/bin/env python3
import socket
import json
import os
import sys
import time
import atexit
import traceback
import requests
from math import radians, sin, cos, sqrt, atan2
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut

def load_settings(file_path="settings.json"):
    default_settings = {
        "GPS_SETTINGS": {
            "udp_ip": "172.20.10.3",
            "udp_port": 11123,
            "buffer_size": 53248,
            "socket_timeout_sec": 1
        },
        "LOCATION_SETTINGS": {
            "geocoding_cache_seconds": 15,    # 15 seconds
            "weather_cache_seconds": 30,      # 30 seconds
            "openweathermap_api_key": "",     # Add your API key here
            "user_agent": "SSiDuck_GPS_Scanner"  # User agent for Nominatim
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
        except Exception:
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
LOG_DIR = "logs/scan_logs/gps"
LOG_FILE = os.path.join(LOG_DIR, "gps_scan.json")

# Store latest GPS values with timestamp
latest_gps_data = {
    "longitude": None,
    "latitude": None,
    "altitude": None,
    "speed": None,
    "satellites": None,
    "timestamp": None,
    "distance_traveled": 0.0,  # Total distance in kilometers
    "distance_since_last_api_call": 0.0,  # Distance since last API call
    "location_info": {
        "address": None,
        "country": None,
        "city": None,
        "last_update": None
    },
    "weather_info": {
        "temperature": None,
        "humidity": None,
        "conditions": None,
        "last_update": None
    }
}

# Store previous position for distance calculation
previous_position = {
    "latitude": None,
    "longitude": None,
    "last_api_position": {  # Position at last API call
        "latitude": None,
        "longitude": None
    }
}

def cleanup():
    """Delete the GPS log file during cleanup."""
    try:
        if os.path.exists(LOG_FILE):
            os.remove(LOG_FILE)
    except Exception:
        pass

def get_location_info(lat, lon):
    """Get location information using Nominatim."""
    if lat is None or lon is None:
        return None
    
    current_time = time.time()
    location_settings = settings["LOCATION_SETTINGS"]
    cache_duration = location_settings["geocoding_cache_seconds"]
    
    # Check if cached data is still valid
    if (latest_gps_data["location_info"]["last_update"] and 
        current_time - latest_gps_data["location_info"]["last_update"] < cache_duration):
        return latest_gps_data["location_info"]
    
    try:
        geolocator = Nominatim(user_agent=location_settings["user_agent"])
        location = geolocator.reverse(f"{lat}, {lon}", language="en")
        
        if location and location.raw.get("address"):
            address_data = location.raw["address"]
            return {
                "address": location.address,
                "country": address_data.get("country"),
                "city": address_data.get("city") or address_data.get("town") or address_data.get("village"),
                "last_update": current_time
            }
    except (GeocoderTimedOut, Exception) as e:
        print(f"Geocoding error: {str(e)}")
    
    return None

def get_weather_info(lat, lon):
    """Get weather information using OpenWeatherMap."""
    if lat is None or lon is None:
        return None
    
    current_time = time.time()
    location_settings = settings["LOCATION_SETTINGS"]
    cache_duration = location_settings["weather_cache_seconds"]
    api_key = location_settings["openweathermap_api_key"]
    
    if not api_key:
        return None
    
    # Check if cached data is still valid
    if (latest_gps_data["weather_info"]["last_update"] and 
        current_time - latest_gps_data["weather_info"]["last_update"] < cache_duration):
        return latest_gps_data["weather_info"]
    
    try:
        url = f"http://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={api_key}&units=metric"
        response = requests.get(url, timeout=5)
        response.raise_for_status()
        
        weather_data = response.json()
        return {
            "temperature": weather_data["main"]["temp"],
            "humidity": weather_data["main"]["humidity"],
            "conditions": weather_data["weather"][0]["description"],
            "last_update": current_time
        }
    except Exception as e:
        print(f"Weather API error: {str(e)}")
    
    return None

def calculate_distance(lat1, lon1, lat2, lon2):
    """Calculate distance between two points using Haversine formula."""
    if None in (lat1, lon1, lat2, lon2):
        return 0.0
        
    R = 6371  # Earth's radius in kilometers

    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    c = 2 * atan2(sqrt(a), sqrt(1-a))
    distance = R * c
    
    return distance

def update_gps_log():
    """Update the GPS log file with current data."""
    temp_file = LOG_FILE + ".tmp"
    current_time = time.time()
    DISTANCE_THRESHOLD = 0.01  # 10 meters in kilometers
    
    # Check if data is stale (older than 30 seconds)
    if latest_gps_data["timestamp"] is None or (current_time - latest_gps_data["timestamp"]) > 30:
        # Reset all values to None if data is stale
        for key in latest_gps_data:
            if key not in ["timestamp", "distance_traveled", "distance_since_last_api_call"]:  # Don't reset distances
                if isinstance(latest_gps_data[key], dict):
                    for subkey in latest_gps_data[key]:
                        latest_gps_data[key][subkey] = None
                else:
                    latest_gps_data[key] = None
    
    # Update location and weather info if coordinates are valid
    if latest_gps_data["latitude"] is not None and latest_gps_data["longitude"] is not None:
        # Calculate distance if we have previous position
        if previous_position["latitude"] is not None and previous_position["longitude"] is not None:
            distance = calculate_distance(
                previous_position["latitude"], previous_position["longitude"],
                latest_gps_data["latitude"], latest_gps_data["longitude"]
            )
            latest_gps_data["distance_traveled"] += distance
            
            # Calculate distance since last API call
            if previous_position["last_api_position"]["latitude"] is not None:
                latest_gps_data["distance_since_last_api_call"] = calculate_distance(
                    previous_position["last_api_position"]["latitude"],
                    previous_position["last_api_position"]["longitude"],
                    latest_gps_data["latitude"],
                    latest_gps_data["longitude"]
                )
        
        # Update previous position
        previous_position["latitude"] = latest_gps_data["latitude"]
        previous_position["longitude"] = latest_gps_data["longitude"]
        
        location_settings = settings["LOCATION_SETTINGS"]
        should_update_location = (
            latest_gps_data["distance_since_last_api_call"] >= DISTANCE_THRESHOLD or
            (latest_gps_data["location_info"]["last_update"] is None) or
            (current_time - latest_gps_data["location_info"]["last_update"] >= location_settings["geocoding_cache_seconds"])
        )
        
        should_update_weather = (
            latest_gps_data["distance_since_last_api_call"] >= DISTANCE_THRESHOLD or
            (latest_gps_data["weather_info"]["last_update"] is None) or
            (current_time - latest_gps_data["weather_info"]["last_update"] >= location_settings["weather_cache_seconds"])
        )
        
        if should_update_location:
            location_info = get_location_info(latest_gps_data["latitude"], latest_gps_data["longitude"])
            if location_info:
                latest_gps_data["location_info"].update(location_info)
                # Update last API position after successful call
                previous_position["last_api_position"]["latitude"] = latest_gps_data["latitude"]
                previous_position["last_api_position"]["longitude"] = latest_gps_data["longitude"]
                latest_gps_data["distance_since_last_api_call"] = 0.0
        
        if should_update_weather:
            weather_info = get_weather_info(latest_gps_data["latitude"], latest_gps_data["longitude"])
            if weather_info:
                latest_gps_data["weather_info"].update(weather_info)
                # Update last API position after successful call
                previous_position["last_api_position"]["latitude"] = latest_gps_data["latitude"]
                previous_position["last_api_position"]["longitude"] = latest_gps_data["longitude"]
                latest_gps_data["distance_since_last_api_call"] = 0.0
    
    try:
        with open(temp_file, 'w') as f:
            json.dump({
                "longitude": latest_gps_data["longitude"],
                "latitude": latest_gps_data["latitude"],
                "altitude": latest_gps_data["altitude"],
                "speed": latest_gps_data["speed"],
                "satellites": latest_gps_data["satellites"],
                "distance_traveled": round(latest_gps_data["distance_traveled"], 3),  # Round to 3 decimal places
                "location_info": latest_gps_data["location_info"],
                "weather_info": latest_gps_data["weather_info"]
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
    last_stale_check = time.time()  # Track the last time we checked for stale data
    UPDATE_INTERVAL = 0.1
    STALE_CHECK_INTERVAL = 1.0  # Check for stale data every second
    
    while True:
        try:
            current_time = time.time()
            
            # Check for stale data periodically, even without new GPS data
            if current_time - last_stale_check >= STALE_CHECK_INTERVAL:
                update_gps_log()  # This function already includes the 30-second stale check
                last_stale_check = current_time
            
            try:
                data, addr = udp_socket.recvfrom(BUFFER_SIZE)
            except socket.timeout:
                # On timeout, just continue to allow stale data checks
                continue
                
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
                            latest_gps_data["timestamp"] = current_time
                    elif sentence.startswith('$GPRMC'):
                        rmc_data = parse_rmc(sentence)
                        if rmc_data:
                            latest_gps_data.update(rmc_data)
                            latest_gps_data["timestamp"] = current_time
                    
                    # Only update the file if enough time has passed
                    if current_time - last_file_update >= UPDATE_INTERVAL:
                        update_gps_log()
                        last_file_update = current_time
                
            # Check if we need to reconnect
            if current_time - last_valid_time >= 10:
                print("No valid data received for 10 seconds, attempting to reconnect...")
                try:
                    udp_socket.close()
                except Exception:
                    pass
                udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                while True:
                    try:
                        udp_socket.bind((UDP_IP, UDP_PORT))
                        udp_socket.settimeout(SOCKET_TIMEOUT)
                        print("Successfully reconnected to UDP socket")
                        break
                    except OSError as e:
                        if e.errno == 99 and UDP_IP != "0.0.0.0":
                            print(f"Reconnection failed, waiting {attempt_interval} seconds...")
                            time.sleep(attempt_interval)
                        else:
                            raise
                last_valid_time = current_time  # Reset the timer after reconnection
                continue  # Continue the main loop after reconnection
                
        except KeyboardInterrupt:
            cleanup()
            sys.exit(0)
        except Exception as e:
            print(f"Error in main loop: {e}")
            print(traceback.format_exc())
            cleanup()
            sys.exit(1)

if __name__ == "__main__":
    try:
        print("Starting GPS scan... Press Ctrl+C to stop.")
        
        # Ensure log directory exists
        os.makedirs(LOG_DIR, exist_ok=True)
        
        # Delete existing log file if it exists
        cleanup()
        
        # Register cleanup handler
        atexit.register(cleanup)
        
        print("Attempting to bind to UDP socket...")
        while True:
            try:
                udp_socket.bind((UDP_IP, UDP_PORT))
                udp_socket.settimeout(SOCKET_TIMEOUT)
                print(f"Successfully bound to {UDP_IP}:{UDP_PORT}")
                break
            except OSError as e:
                print(f"Socket binding error: {e}")
                if e.errno == 99 and UDP_IP != "0.0.0.0":
                    print(f"Waiting {attempt_interval} seconds before retry...")
                    time.sleep(attempt_interval)
                else:
                    raise
        
        print("Entering main loop...")
        while True:
            try:
                main_loop()
            except Exception as e:
                print(f"Error in main loop: {e}")
                print(traceback.format_exc())
                sys.exit(1)
    except KeyboardInterrupt:
        print("\nGPS scan stopped.")
        sys.exit(0)
    except Exception as e:
        print(f"Fatal error: {e}")
        print(traceback.format_exc())
        sys.exit(1)

