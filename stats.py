#!/usr/bin/env python3
import os
import sys
import time
import signal
import json
import math
from datetime import datetime
from loguru import logger
import csv
from typing import Dict, List, Optional, Tuple, Any
import geopy
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut
import numpy as np
import requests

# Global flags for logging state
error_flag = False
warning_flag = False
success_flag = False
critical_flag = False

# Cache for location data
location_cache = {
    'data': None,
    'timestamp': None,
    'coordinates': None,
    'last_distance': 0
}

# Cache for GPS data
gps_cache = {
    'data': None,
    'timestamp': None,
    'last_valid_data': None
}

# Cache for wardrive data
wardrive_cache = {
    'last_line': None,
    'timestamp': None,
    'data': None
}

# Cache for weather data
weather_cache = {
    'data': None,
    'timestamp': None
}

# Cache duration in seconds (1 minute)
CACHE_DURATION = 60

# Minimum distance (meters) required to trigger new API request
MIN_DISTANCE = 25

# Define paths
gps_log_path = os.path.join(os.getcwd(), "logs", "gps_logs", "gps_data.log")
stats_log_path = os.path.join(os.getcwd(), "logs", "stats_logs", "stats_data.log")
log_file_path = os.path.join(os.getcwd(), "logs", "stats_logs", "stats_monitor.log")

# Initialize geocoder
geolocator = Nominatim(user_agent="wardriver_stats")

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
        "STATS_SETTINGS": {
            "update_interval": 1,  # Update stats every second
            "max_messages": 100,
            "api_update_interval": 10  # API calls every 10 seconds
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

def parse_gps_data(lines: List[str]) -> Optional[Dict]:
    """Parse GPS data from the last complete message set."""
    if len(lines) < 4:
        return None
        
    try:
        # Identify message types
        gga_line = None
        rmc_line = None
        pashr_line = None
        
        for line in lines:
            if line.startswith('$GPGGA'):
                gga_line = line
            elif line.startswith('$GPRMC'):
                rmc_line = line
            elif line.startswith('$PASHR'):
                pashr_line = line
                
        if not all([gga_line, rmc_line, pashr_line]):
            return None
            
        # Parse GPGGA data
        gga_parts = gga_line.split(',')
        if len(gga_parts) < 10:
            return None
            
        # Parse GPRMC data
        rmc_parts = rmc_line.split(',')
        if len(rmc_parts) < 8:
            return None
            
        # Parse PASHR data
        pashr_parts = pashr_line.split(',')
        if len(pashr_parts) < 10:
            return None
            
        # Extract time from GGA
        time_str = gga_parts[1]
        current_time = datetime.now()
        hour = int(time_str[:2])
        minute = int(time_str[2:4])
        second = int(time_str[4:6])
        time = current_time.replace(hour=hour, minute=minute, second=second)
        
        # Extract coordinates from GGA
        lat_str = gga_parts[2]
        lat_dir = gga_parts[3]
        lon_str = gga_parts[4]
        lon_dir = gga_parts[5]
        
        # Convert latitude from DDMM.MMMM to decimal degrees
        lat_deg = float(lat_str[:2])
        lat_min = float(lat_str[2:])
        lat = lat_deg + lat_min/60
        if lat_dir == 'S':
            lat = -lat
            
        # Convert longitude from DDDMM.MMMM to decimal degrees
        lon_deg = float(lon_str[:3])
        lon_min = float(lon_str[3:])
        lon = lon_deg + lon_min/60
        if lon_dir == 'W':
            lon = -lon
            
        # Extract altitude (in meters)
        altitude = float(gga_parts[9])
        
        # Extract speed and heading from RMC
        speed_knots = float(rmc_parts[7]) if rmc_parts[7] and rmc_parts[7] != '' else 0
        speed_ms = speed_knots * 0.514444  # Convert knots to m/s
        heading = float(rmc_parts[8]) if rmc_parts[8] and rmc_parts[8] != '' else 0
        
        # Extract pitch and roll from PASHR
        pitch = float(pashr_parts[3]) if pashr_parts[3] and pashr_parts[3] != '' else 0
        roll = float(pashr_parts[4]) if pashr_parts[4] and pashr_parts[4] != '' else 0
        
        # Extract number of satellites
        satellites = int(gga_parts[7]) if gga_parts[7] and gga_parts[7] != '' else 0
        
        return {
            'time': time,
            'latitude': lat,
            'longitude': lon,
            'altitude': altitude,
            'speed': speed_ms,
            'heading': heading,
            'pitch': pitch,
            'roll': roll,
            'satellites': satellites
        }
    except Exception as e:
        return None

def get_location_info(lat: float, lon: float) -> Dict:
    """Get location information using reverse geocoding."""
    try:
        location = geolocator.reverse((lat, lon))
        if location and location.raw.get('address'):
            address = location.raw['address']
            # Only cache if we got valid data
            location_cache['data'] = address
            location_cache['timestamp'] = time.time()
            location_cache['last_distance'] = 0
            return address
        else:
            set_log_level("error", "No address data received from geocoder")
    except GeocoderTimedOut:
        set_log_level("warning", "Geocoding request timed out")
    except Exception as e:
        set_log_level("error", f"Failed to get location info: {e}")
    
    # Only use cache if we failed to get new data
    if location_cache.get('data'):
        set_log_level("warning", "Using cached location data")
        return location_cache['data']
    return {
        'road': 'Unknown',
        'house_number': '',
        'town': 'Unknown',
        'municipality': 'Unknown',
        'state': 'Unknown',
        'country': 'Unknown',
        'postcode': 'Unknown'
    }

def calculate_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance between two points in kilometers."""
    R = 6371  # Earth's radius in km
    
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    
    a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
    c = 2 * math.asin(math.sqrt(a))
    return R * c

def analyze_gps_data(gps_data: List[Dict]) -> Dict:
    """Analyze GPS data to generate statistics."""
    if not gps_data:
        return {}
        
    # Calculate basic statistics
    speeds = [d['speed'] for d in gps_data]
    altitudes = [d['altitude'] for d in gps_data]
    headings = [d['heading'] for d in gps_data]
    
    # Calculate total distance
    total_distance = 0
    for i in range(1, len(gps_data)):
        total_distance += calculate_distance(
            gps_data[i-1]['latitude'],
            gps_data[i-1]['longitude'],
            gps_data[i]['latitude'],
            gps_data[i]['longitude']
        )
    
    return {
        'avg_speed': np.mean(speeds),
        'max_speed': max(speeds),
        'avg_altitude': np.mean(altitudes),
        'max_altitude': max(altitudes),
        'min_altitude': min(altitudes),
        'avg_heading': np.mean(headings),
        'total_distance': total_distance,
        'satellites': gps_data[-1]['satellites']
    }

def get_script_status():
    """Get status of all scripts from their respective log files."""
    log_files = {
        'gps_lock': os.path.join(os.getcwd(), "logs", "gps_logs", "gps_monitor.log"),
        'wifi_recon': os.path.join(os.getcwd(), "logs", "kismet_logs", "kismet_monitor.log"),
        'ble_recon': os.path.join(os.getcwd(), "logs", "bettercap_logs", "bettercap_monitor.log"),
        'wardriving': os.path.join(os.getcwd(), "logs", "wardrive_logs", "wardrive_monitor.log"),
        'uploading': os.path.join(os.getcwd(), "logs", "wigle_logs", "wigle_monitor.log")
    }
    
    statuses = {}
    for script, log_file in log_files.items():
        try:
            if os.path.exists(log_file):
                with open(log_file, 'r') as f:
                    lines = f.readlines()
                    if lines:
                        last_line = lines[-1].strip()
                        if "SUCCESS" in last_line:
                            statuses[script] = "SUCCESS"
                        elif "WARNING" in last_line:
                            statuses[script] = "WARNING"
                        elif "ERROR" in last_line:
                            statuses[script] = "ERROR"
                        elif "CRITICAL" in last_line:
                            statuses[script] = "CRITICAL"
                        else:
                            statuses[script] = "UNKNOWN"
                    else:
                        statuses[script] = "UNKNOWN"
            else:
                statuses[script] = "UNKNOWN"
        except Exception as e:
            statuses[script] = "UNKNOWN"
            
    return statuses

def get_wardrive_data() -> Dict:
    """Get the latest wardrive data from the log file."""
    try:
        wardrive_log_path = os.path.join(os.getcwd(), "logs", "wardrive_logs", "wardrive_data.log")
        
        if not os.path.exists(wardrive_log_path):
            set_log_level("warning", "Wardrive log file not found")
            return {'time': '00:00:00', 'wifi_count': 0, 'ble_count': 0}
            
        with open(wardrive_log_path, 'r') as f:
            # Read all lines and filter out empty lines
            lines = [line.strip() for line in f.readlines() if line.strip()]
            
            if not lines:
                set_log_level("warning", "No data found in wardrive log file")
                return {'time': '00:00:00', 'wifi_count': 0, 'ble_count': 0}
                
            # Get the last line
            last_line = lines[-1]
            
            # Parse the line
            if 'SESSION CLOCK:' in last_line and 'DEVICES LOGGED:' in last_line:
                try:
                    # Split by 'SESSION CLOCK:' and 'DEVICES LOGGED:'
                    time_part = last_line.split('SESSION CLOCK:')[1].split('DEVICES LOGGED:')[0].strip()
                    devices_part = last_line.split('DEVICES LOGGED:')[1].strip()
                    
                    # Parse time
                    time_str = time_part.strip()
                    
                    # Parse device counts
                    wifi_part = devices_part.split('WIFI:')[1].split('BLE:')[0].strip()
                    ble_part = devices_part.split('BLE:')[1].strip()
                    
                    wifi_count = int(wifi_part)
                    ble_count = int(ble_part)
                    
                    return {
                        'time': time_str,
                        'wifi_count': wifi_count,
                        'ble_count': ble_count
                    }
                except (IndexError, ValueError) as e:
                    set_log_level("error", f"Failed to parse wardrive line: {last_line}")
            else:
                set_log_level("error", f"Line does not contain expected format: {last_line}")
                
    except Exception as e:
        set_log_level("error", f"Failed to read wardrive data: {e}")
    
    return {'time': '00:00:00', 'wifi_count': 0, 'ble_count': 0}

def get_weather_data(lat: float, lon: float) -> Dict[str, Any]:
    """Get weather data using Open-Meteo API."""
    try:
        # Open-Meteo API endpoint for current weather
        url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m,precipitation,cloud_cover"
        
        
        # Add timeout to prevent hanging
        response = requests.get(url, timeout=5)
        
        if response.status_code == 200:
            data = response.json()
            current = data.get('current', {})
            
            # Debug log the raw response
            
            # Format weather data with proper Unicode characters
            weather_data = {
                'temperature': f"{current.get('temperature_2m', 0):.1f}°C",
                'humidity': f"{current.get('relative_humidity_2m', 0)}%",
                'wind_speed': f"{current.get('wind_speed_10m', 0):.1f} km/h",
                'wind_direction': f"{current.get('wind_direction_10m', 0):.1f}°",
                'precipitation': f"{current.get('precipitation', 0):.1f} mm",
                'cloud_cover': f"{current.get('cloud_cover', 0)}%"
            }
            
            # Log successful weather data retrieval
            set_log_level("debug", f"Successfully retrieved weather data: {json.dumps(weather_data, indent=2, ensure_ascii=False)}")
            return weather_data
        else:
            set_log_level("warning", f"Failed to get weather data: HTTP {response.status_code}")
            set_log_level("debug", f"Response content: {response.text}")
    except requests.Timeout:
        set_log_level("warning", "Weather API request timed out")
    except requests.ConnectionError:
        set_log_level("warning", "Failed to connect to weather API - check internet connection")
    except json.JSONDecodeError as e:
        set_log_level("error", f"Failed to parse weather API response: {e}")
        set_log_level("debug", f"Response content: {response.text}")
    except Exception as e:
        set_log_level("error", f"Failed to get weather data: {str(e)}")
        set_log_level("debug", f"Error type: {type(e).__name__}")
    
    # Return default values if any error occurred
    return {
        'temperature': 'N/A',
        'humidity': 'N/A',
        'wind_speed': 'N/A',
        'wind_direction': 'N/A',
        'precipitation': 'N/A',
        'cloud_cover': 'N/A'
    }

def update_stats():
    """Update statistics based on GPS data."""
    try:
        # Read GPS log file
        with open(gps_log_path, 'r') as f:
            lines = f.readlines()
            
        # Find the last complete message set
        message_sets = []
        current_set = []
        
        for line in reversed(lines):
            if line.startswith('$GPTXT'):
                if len(current_set) == 4:
                    message_sets.append(current_set)
                current_set = [line.strip()]
            elif len(current_set) < 4:
                current_set.append(line.strip())
                
        if len(current_set) == 4:
            message_sets.append(current_set)
            
        # Parse GPS data from the last 100 message sets
        gps_data = []
        for message_set in message_sets[:100]:
            data = parse_gps_data(message_set)
            if data:
                gps_data.append(data)
                
        # Update GPS cache if we have valid data
        if gps_data:
            gps_cache['data'] = gps_data
            gps_cache['timestamp'] = datetime.now()
            gps_cache['last_valid_data'] = gps_data[-1]
        # Use cached data if available and not too old
        elif (gps_cache['last_valid_data'] and 
              gps_cache['timestamp'] and 
              (datetime.now() - gps_cache['timestamp']).total_seconds() < CACHE_DURATION):
            gps_data = [gps_cache['last_valid_data']]
            set_log_level("warning", "Using cached GPS data")
        else:
            set_log_level("warning", "No valid GPS data found and no valid cache available")
            return
            
        # Calculate total distance traveled
        total_distance = 0
        for i in range(1, len(gps_data)):
            total_distance += calculate_distance(
                gps_data[i-1]['latitude'],
                gps_data[i-1]['longitude'],
                gps_data[i]['latitude'],
                gps_data[i]['longitude']
            )
        
        # Get current location info and weather data
        current_location = get_location_info(gps_data[-1]['latitude'], gps_data[-1]['longitude'])
        weather_data = get_weather_data(gps_data[-1]['latitude'], gps_data[-1]['longitude'])
        
        # Calculate statistics
        stats = analyze_gps_data(gps_data)
        
        # Get script statuses
        script_statuses = get_script_status()
        
        # Get wardrive data
        wardrive_data = get_wardrive_data()
        
        # Prepare output with better formatting
        output = {
            'timestamp': datetime.now().isoformat(),
            'location': {
                'address': f"{current_location.get('road', 'Unknown')} {current_location.get('house_number', '')}".strip(),
                'suburb': current_location.get('town', 'Unknown'),
                'city': current_location.get('municipality', 'Unknown'),
                'state': current_location.get('state', 'Unknown'),
                'country': current_location.get('country', 'Unknown'),
                'postcode': current_location.get('postcode', 'Unknown')
            },
            'weather': weather_data,
            'gps': {
                'position': f"{gps_data[-1]['latitude']:.6f}, {gps_data[-1]['longitude']:.6f}",
                'altitude': f"{gps_data[-1]['altitude']:.1f}m",
                'speed': f"{gps_data[-1]['speed']:.1f}m/s",
                'heading': f"{gps_data[-1]['heading']:.1f}°",
                'satellites': gps_data[-1]['satellites']
            },
            'stats': {
                'distance': f"{stats['total_distance']:.2f}m",
                'avg_speed': f"{stats['avg_speed']:.1f}m/s",
                'max_speed': f"{stats['max_speed']:.1f}m/s",
                'avg_altitude': f"{stats['avg_altitude']:.1f}m",
                'satellites': stats['satellites']
            },
            'wardrive': {
                'time': wardrive_data['time'],
                'wifi_count': wardrive_data['wifi_count'],
                'ble_count': wardrive_data['ble_count']
            },
            'status': script_statuses
        }
        
        # Clear the stats log file before writing new data
        open(stats_log_path, "w").close()
        
        # Write to stats log with pretty formatting
        with open(stats_log_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(output, indent=2, ensure_ascii=False) + '\n')
            
        set_log_level("success", f"Updated stats: {stats['total_distance']:.2f}m, {stats['avg_speed']:.1f}m/s, WIFI:{wardrive_data['wifi_count']} BLE:{wardrive_data['ble_count']}, Temp:{weather_data['temperature']}")
            
    except Exception as e:
        set_log_level("error", f"Failed to update stats: {e}")

def handle_exit(sig, frame):
    set_log_level("critical", "STOPPED.")
    sys.exit(0)

def main():
    # Create log directories
    os.makedirs(os.path.dirname(stats_log_path), exist_ok=True)
    os.makedirs(os.path.dirname(log_file_path), exist_ok=True)
    
    # Clear log files at startup
    if os.path.exists(log_file_path):
        open(log_file_path, "w").close()
    if os.path.exists(stats_log_path):
        open(stats_log_path, "w").close()
    
    # Clear caches at startup
    location_cache['data'] = None
    location_cache['timestamp'] = None
    gps_cache['data'] = None
    gps_cache['timestamp'] = None
    wardrive_cache['last_line'] = None
    wardrive_cache['timestamp'] = None
    wardrive_cache['data'] = None
    weather_cache['data'] = None
    weather_cache['timestamp'] = None
    
    # Configure logger
    logger.remove()
    fmt = "<yellow>{time:DD/MM @ HH:mm:ss.SSS} </yellow><blue>|</blue><level>{level:^9}</level><blue>|</blue><magenta> STATS </magenta><cyan>{message}</cyan>"
    logger.add(sys.stderr, level="DEBUG", colorize=True, format=fmt)
    logger.add(log_file_path, level="DEBUG", format=fmt)
    
    # Set up signal handlers
    signal.signal(signal.SIGINT, handle_exit)
    signal.signal(signal.SIGTERM, handle_exit)
    
    # Load settings
    settings = load_settings()
    update_interval = settings['STATS_SETTINGS']['update_interval']
    api_update_interval = settings['STATS_SETTINGS']['api_update_interval']
    
    set_log_level("success", "STARTED.")
    
    try:
        last_api_update = 0
        last_wardrive_line = None
        last_gps_data = None
        last_script_status = None
        
        while True:
            current_time = time.time()
            
            # Get current data from all sources
            wardrive_data = get_wardrive_data()
            script_statuses = get_script_status()
            
            # Read GPS data
            try:
                with open(gps_log_path, 'r') as f:
                    lines = f.readlines()
                message_sets = []
                current_set = []
                for line in reversed(lines):
                    if line.startswith('$GPTXT'):
                        if len(current_set) == 4:
                            message_sets.append(current_set)
                        current_set = [line.strip()]
                    elif len(current_set) < 4:
                        current_set.append(line.strip())
                if len(current_set) == 4:
                    message_sets.append(current_set)
                
                gps_data = []
                for message_set in message_sets[:100]:
                    data = parse_gps_data(message_set)
                    if data:
                        gps_data.append(data)
                
                if gps_data:
                    current_gps_data = gps_data[-1]
                    stats = analyze_gps_data(gps_data)
                else:
                    current_gps_data = None
                    stats = {}
            except Exception as e:
                set_log_level("error", f"Failed to update GPS data: {e}")
                current_gps_data = None
                stats = {}
            
            # Check if any data has changed
            data_changed = False
            
            # Check wardrive data
            if wardrive_data['time'] != last_wardrive_line:
                data_changed = True
                last_wardrive_line = wardrive_data['time']
            
            # Check GPS data
            if current_gps_data and (not last_gps_data or 
                current_gps_data['time'] != last_gps_data['time'] or
                current_gps_data['latitude'] != last_gps_data['latitude'] or
                current_gps_data['longitude'] != last_gps_data['longitude']):
                data_changed = True
                last_gps_data = current_gps_data
            
            # Check script status
            if script_statuses != last_script_status:
                data_changed = True
                last_script_status = script_statuses
            
            # Update stats if data has changed or API interval has passed
            if data_changed or current_time - last_api_update >= api_update_interval:
                try:
                    # Get location info and weather data only if API interval has passed
                    if current_time - last_api_update >= api_update_interval:
                        current_location = get_location_info(current_gps_data['latitude'], current_gps_data['longitude'])
                        weather_data = get_weather_data(current_gps_data['latitude'], current_gps_data['longitude'])
                        # Cache the weather data
                        weather_cache['data'] = weather_data
                        weather_cache['timestamp'] = current_time
                        last_api_update = current_time
                    else:
                        # Use cached location and weather data
                        current_location = location_cache.get('data', {
                            'road': 'Unknown',
                            'house_number': '',
                            'town': 'Unknown',
                            'municipality': 'Unknown',
                            'state': 'Unknown',
                            'country': 'Unknown',
                            'postcode': 'Unknown'
                        })
                        weather_data = weather_cache.get('data', {
                            'temperature': 'N/A',
                            'humidity': 'N/A',
                            'wind_speed': 'N/A',
                            'wind_direction': 'N/A',
                            'precipitation': 'N/A',
                            'cloud_cover': 'N/A'
                        })
                    
                    # Prepare output
                    output = {
                        'timestamp': datetime.now().isoformat(),
                        'location': {
                            'address': f"{current_location.get('road', 'Unknown')} {current_location.get('house_number', '')}".strip(),
                            'suburb': current_location.get('town', 'Unknown'),
                            'city': current_location.get('municipality', 'Unknown'),
                            'state': current_location.get('state', 'Unknown'),
                            'country': current_location.get('country', 'Unknown'),
                            'postcode': current_location.get('postcode', 'Unknown')
                        },
                        'weather': weather_data,
                        'gps': {
                            'position': f"{current_gps_data['latitude']:.6f}, {current_gps_data['longitude']:.6f}",
                            'altitude': f"{current_gps_data['altitude']:.1f}m",
                            'speed': f"{current_gps_data['speed']:.1f}m/s",
                            'heading': f"{current_gps_data['heading']:.1f}°",
                            'satellites': current_gps_data['satellites']
                        },
                        'stats': {
                            'distance': f"{stats.get('total_distance', 0):.2f}m",
                            'avg_speed': f"{stats.get('avg_speed', 0):.1f}m/s",
                            'max_speed': f"{stats.get('max_speed', 0):.1f}m/s",
                            'avg_altitude': f"{stats.get('avg_altitude', 0):.1f}m",
                            'satellites': stats.get('satellites', 0)
                        },
                        'wardrive': {
                            'time': wardrive_data['time'],
                            'wifi_count': wardrive_data['wifi_count'],
                            'ble_count': wardrive_data['ble_count']
                        },
                        'status': script_statuses
                    }
                    
                    # Clear the stats log file before writing new data
                    open(stats_log_path, "w").close()
                    
                    # Write to stats log with pretty formatting
                    with open(stats_log_path, 'a', encoding='utf-8') as f:
                        f.write(json.dumps(output, indent=2, ensure_ascii=False) + '\n')
                    
                    set_log_level("success", f"Updated stats: {stats.get('total_distance', 0):.2f}m, {stats.get('avg_speed', 0):.1f}m/s, WIFI:{wardrive_data['wifi_count']} BLE:{wardrive_data['ble_count']}, Temp:{weather_data['temperature']}")
                except Exception as e:
                    set_log_level("error", f"Failed to update stats: {e}")
            
            time.sleep(update_interval)
    except KeyboardInterrupt:
        handle_exit(signal.SIGINT, None)
    except Exception as e:
        set_log_level("critical", f"UNEXPECTED ERROR: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main() 
