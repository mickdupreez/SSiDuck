#!/usr/bin/env python3

import asyncio
import json
import os
import signal
import sys
import time
import csv
import hashlib
from datetime import datetime
from typing import Dict, List, Optional, Any
import subprocess
import atexit
import shutil
import requests
from pathlib import Path

class DeviceStats:
    def __init__(self):
        self.stats_file = Path("logs/stats/stats.json")
        self.stats_file.parent.mkdir(parents=True, exist_ok=True)
        self.stats = self._load_stats()
        self.wigle_stats = None
        self._get_wigle_stats()
        
    def _get_wigle_stats(self):
        """Get Wigle stats for the user and update stats file."""
        try:
            settings_file = "settings.json"
            if not os.path.exists(settings_file):
                self.wigle_stats = None
                self.stats['wigle_stats'] = None
                self._save_stats()
                return
                
            with open(settings_file, 'r') as f:
                settings = json.load(f)
                wigle_settings = settings.get('WIGLE_SETTINGS', {})
                credentials = wigle_settings.get('credentials', {})
                api_username = credentials.get('api_username')
                api_password = credentials.get('api_password')
                username = credentials.get('username')
                if not api_username or not api_password:
                    self.wigle_stats = None
                    self.stats['wigle_stats'] = None
                    self._save_stats()
                    return
                    
            url = f"https://api.wigle.net/api/v2/stats/user?user={username}"
            response = requests.get(url, auth=(api_username, api_password))
            
            # Always update stats with latest API response
            if response.status_code == 200:
                stats = response.json()
                if stats.get('success'):
                    wigle_stats = stats.get('statistics', {})
                    # Filter out unwanted fields
                    fields_to_remove = [
                        'totalWiFiLocations', 'first', 'last', 'self',
                        'discoveredBt', 'eventMonthCount', 'eventPrevMonthCount', 'prevRank',
                        'discoveredWiFiGPSPercent', 'discoveredWiFi', 'discoveredCellGPS', 'discoveredCell'
                    ]
                    for field in fields_to_remove:
                        wigle_stats.pop(field, None)
                    
                    # Create new ordered dictionary with desired field sequence
                    ordered_wigle_stats = {
                        'userName': wigle_stats.get('userName'),
                        'rank': wigle_stats.get('rank'),
                        'monthRank': wigle_stats.get('monthRank'),
                        'prevMonthRank': wigle_stats.get('prevMonthRank'),
                        'discoveredWiFiGPS': wigle_stats.get('discoveredWiFiGPS'),
                        'discoveredBtGPS': wigle_stats.get('discoveredBtGPS')
                    }
                    self.wigle_stats = ordered_wigle_stats
                else:
                    self.wigle_stats = None
            else:
                self.wigle_stats = None
                
            # Always update the stats file with latest data
            self.stats['wigle_stats'] = self.wigle_stats
            self._save_stats()
            
        except Exception:
            self.wigle_stats = None
            self.stats['wigle_stats'] = None
            self._save_stats()

    def _load_stats(self) -> Dict[str, Any]:
        """Load existing stats from file or create new if doesn't exist"""
        if self.stats_file.exists():
            with open(self.stats_file, 'r') as f:
                data = json.load(f)
                # Remove old device_counts if it exists
                if "device_counts" in data:
                    del data["device_counts"]
                return data
        return {
            "wigle_stats": None,
            "devices": {
                "ble_devices": {},
                "bt_devices": {},
                "wifi_devices": {
                    "access_points": {},
                    "stations": {}
                }
            },
            "last_scan": None,
            "summary": {
                "BLE_DEVICES": 0,
                "BT_DEVICES": 0,
                "ACCESS_POINTS": 0,
                "STATIONS": 0,
                "TOTAL_DEVICES": 0
            }
        }

    def _save_stats(self):
        """Save current stats to file"""
        # Create a new dictionary with the desired order
        ordered_stats = {
            "wigle_stats": self.stats["wigle_stats"],
            "summary": self.stats["summary"],
            "last_scan": self.stats["last_scan"],
            "devices": self.stats["devices"]
        }
        
        with open(self.stats_file, 'w') as f:
            json.dump(ordered_stats, f, indent=2)

    def _calculate_hash(self, device: Dict[str, Any]) -> str:
        """Calculate a hash of the device data to detect changes"""
        # Create a copy of the device data without the update counter and first_seen
        device_data = device.copy()
        if "update_count" in device_data:
            del device_data["update_count"]
        if "first_seen" in device_data:
            del device_data["first_seen"]
        return hashlib.md5(json.dumps(device_data, sort_keys=True).encode()).hexdigest()

    def _update_device(self, device_type: str, device: Dict[str, Any], device_id: str, current_time: str):
        """Update a device's stats if it has changed"""
        # Get the appropriate container based on device type
        if device_type == "ble":
            container = self.stats["devices"]["ble_devices"]
        elif device_type == "bt":
            container = self.stats["devices"]["bt_devices"]
        elif device_type == "wifi_ap":
            container = self.stats["devices"]["wifi_devices"]["access_points"]
        elif device_type == "wifi_station":
            container = self.stats["devices"]["wifi_devices"]["stations"]
        else:
            return

        # Calculate hash of current device data
        current_hash = self._calculate_hash(device)

        # Check if device exists and has changed
        if device_id in container:
            existing_hash = self._calculate_hash(container[device_id])
            if current_hash == existing_hash:
                return  # No changes, no update needed
        else:
            # This is a new device, set first_seen time
            device["first_seen"] = current_time

        # Update device data and increment counter
        device["update_count"] = container.get(device_id, {}).get("update_count", 0) + 1
        container[device_id] = device

    def _update_device_counts(self):
        """Update the device counts in stats"""
        wifi_aps = len(self.stats["devices"]["wifi_devices"]["access_points"])
        wifi_stations = len(self.stats["devices"]["wifi_devices"]["stations"])
        ble_count = len(self.stats["devices"]["ble_devices"])
        bt_count = len(self.stats["devices"]["bt_devices"])
        
        self.stats["summary"] = {
            "BLE_DEVICES": ble_count,
            "BT_DEVICES": bt_count,
            "ACCESS_POINTS": wifi_aps,
            "STATIONS": wifi_stations,
            "TOTAL_DEVICES": ble_count + bt_count + wifi_aps + wifi_stations
        }

    def process_wardrive(self, wardrive_file: Path):
        """Process a wardrive.json file and update stats"""
        try:
            with open(wardrive_file, 'r') as f:
                wardrive_data = json.load(f)

            # Get current timestamp
            current_time = wardrive_data.get("timestamp")
            if not current_time:
                current_time = datetime.now().isoformat()

            # Update last scan timestamp
            self.stats["last_scan"] = current_time

            # Process BLE devices
            for device in wardrive_data.get("devices", {}).get("ble_devices", []):
                device_id = device.get("BD_ADDR")
                if device_id:
                    self._update_device("ble", device, device_id, current_time)

            # Process BT devices
            for device in wardrive_data.get("devices", {}).get("bt_devices", []):
                device_id = device.get("BD_ADDR")
                if device_id:
                    self._update_device("bt", device, device_id, current_time)

            # Process WiFi access points
            for device in wardrive_data.get("devices", {}).get("wifi_devices", {}).get("access_points", []):
                device_id = device.get("MAC")
                if device_id:
                    self._update_device("wifi_ap", device, device_id, current_time)

            # Process WiFi stations
            for device in wardrive_data.get("devices", {}).get("wifi_devices", {}).get("stations", []):
                device_id = device.get("MAC")
                if device_id:
                    self._update_device("wifi_station", device, device_id, current_time)

            # Update device counts
            self._update_device_counts()

            # Save updated stats
            self._save_stats()

        except Exception as e:
            pass

class FileMonitor:
    def __init__(self, scan_logs_dir: str):
        self.scan_logs_dir = scan_logs_dir
        self.ble_file = os.path.join(scan_logs_dir, 'ble', 'ble_scan.json')
        self.bt_file = os.path.join(scan_logs_dir, 'bluetooth', 'bt_scan.json')
        self.wifi_file = os.path.join(scan_logs_dir, 'wifi', 'wifi_scan.json')
        
        # Hardcoded output paths
        self.output_file = "logs/scan_logs/wardrive/wardrive.json"
        self.csv_file = "logs/scan_logs/wardrive/wardrive.csv"
        self.wardrive_logs_dir = "logs/scan_logs/wardrive/upload"
        os.makedirs(self.wardrive_logs_dir, exist_ok=True)
        
        # Load settings for Wigle upload
        self.settings_file = "settings.json"
        self.settings = self.load_settings()
        wigle_settings = self.settings.get('WIGLE_SETTINGS', {})
        self.api_key = wigle_settings.get('api_key')
        
        # Get upload settings
        upload_settings = wigle_settings.get('upload', {})
        self.upload_enabled = upload_settings.get('enabled', False)
        self.wigle_upload_url = upload_settings.get('url', 'https://api.wigle.net/api/v2/file/upload')
        
        # Initialize device stats
        self.device_stats = DeviceStats()
        
        # Backup existing CSV file if it exists
        self.backup_csv_file()
        
        self.last_modified_times: Dict[str, float] = {
            self.ble_file: 0,
            self.bt_file: 0,
            self.wifi_file: 0
        }
        self.running = True
        self.existing_devices = self.read_existing_csv()

    def load_settings(self) -> dict:
        """Load settings from settings.json file."""
        try:
            if os.path.exists(self.settings_file):
                with open(self.settings_file, 'r') as f:
                    return json.load(f)
        except Exception as e:
            print(f"Error loading settings: {e}")
        return {}

    def upload_file_to_wigle(self, file_path: str) -> bool:
        """Upload a file to Wigle."""
        if not self.upload_enabled:
            print("Wigle upload is disabled in settings")
            return False
            
        if not self.api_key:
            print("No Wigle API key found in settings.json")
            return False
            
        try:
            with open(file_path, 'rb') as f:
                files = {'file': f}
                headers = {
                    'Authorization': f'Basic {self.api_key}',
                    'Accept': 'application/json'
                }
                response = requests.post(self.wigle_upload_url, files=files, headers=headers)
                if response.status_code == 200:
                    print(f"Successfully uploaded {file_path} to Wigle")
                    return True
                else:
                    print(f"Failed to upload {file_path} to Wigle. Status code: {response.status_code}")
                    return False
        except Exception as e:
            print(f"Error uploading {file_path} to Wigle: {e}")
            return False

    def process_upload_directory(self):
        """Process all files in the upload directory."""
        if not os.path.exists(self.wardrive_logs_dir):
            return

        for filename in os.listdir(self.wardrive_logs_dir):
            file_path = os.path.join(self.wardrive_logs_dir, filename)
            if not os.path.isfile(file_path):
                continue

            try:
                # Count lines in file
                with open(file_path, 'r') as f:
                    line_count = sum(1 for _ in f)

                if line_count < 50:
                    # Delete files with less than 50 lines
                    try:
                        os.remove(file_path)
                        print(f"Deleted small file {filename} ({line_count} lines)")
                    except Exception as e:
                        print(f"Error deleting small file {filename}: {e}")
                    continue

                # Upload file if it has enough lines
                if self.upload_file_to_wigle(file_path):
                    try:
                        os.remove(file_path)
                        print(f"Successfully uploaded and deleted {filename}")
                    except Exception as e:
                        print(f"Error deleting uploaded file {filename}: {e}")
                else:
                    print(f"Failed to upload {filename}")

            except Exception as e:
                print(f"Error processing file {filename}: {e}")

    def backup_csv_file(self):
        """Backup the CSV file with current date and time if it exists."""
        if os.path.exists(self.csv_file):
            current_time = datetime.now().strftime("%d-%m-%y_%H:%M")
            backup_filename = f"SSIDuck_{current_time}.csv"
            backup_path = os.path.join(self.wardrive_logs_dir, backup_filename)
            try:
                shutil.move(self.csv_file, backup_path)
                print(f"Backed up existing CSV file to {backup_path}")
                
                # Process all files in upload directory
                self.process_upload_directory()
                
            except Exception as e:
                print(f"Error backing up CSV file: {e}")

    def get_file_modified_time(self, file_path: str) -> float:
        """Get the last modified time of a file"""
        try:
            if os.path.exists(file_path):
                return os.path.getmtime(file_path)
        except Exception:
            pass
        return 0

    def has_files_changed(self) -> bool:
        """Check if any of the input files have changed"""
        changed = False
        for file_path in self.last_modified_times:
            current_mtime = self.get_file_modified_time(file_path)
            if current_mtime > self.last_modified_times[file_path]:
                changed = True
                self.last_modified_times[file_path] = current_mtime
        return changed

    def ensure_output_directory(self):
        """Ensure the output directory exists"""
        output_dir = os.path.dirname(self.output_file)
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)


    def cleanup_output_file(self):
        """Delete the output files if they exist"""
        try:
            if os.path.exists(self.output_file):
                os.remove(self.output_file)
        except Exception as e:
            print(f"Error during cleanup: {e}")

    def read_json_file(self, file_path: str) -> Optional[dict]:
        """Read and parse a JSON file if it exists"""
        try:
            if os.path.exists(file_path):
                with open(file_path, 'r') as f:
                    return json.load(f)
        except Exception as e:
            print(f"Error reading {file_path}: {e}")
        return None

    def read_existing_csv(self) -> Dict:
        """Read existing CSV file and return devices as dictionary."""
        if not os.path.exists(self.csv_file):
            return {}
        
        devices = {}
        try:
            with open(self.csv_file, 'r') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    devices[row['MAC']] = row
        except Exception as e:
            print(f"Error reading CSV file: {e}")
            return {}
        return devices

    def write_csv(self, devices: Dict):
        """Write devices to CSV file."""
        headers = ['MAC', 'SSID', 'AuthMode', 'FirstSeen', 'Channel', 'RSSI', 
                  'CurrentLatitude', 'CurrentLongitude', 'AltitudeMeters', 
                  'AccuracyMeters', 'Type']
        
        try:
            # Sort devices by FirstSeen time
            sorted_devices = sorted(devices.values(), key=lambda x: x['FirstSeen'])
            
            with open(self.csv_file, 'w', newline='') as f:
                # Write Wigle preheader
                f.write("WigleWifi-1.4,appRelease=Kismet202307R1,model=Kismet,release=2023.07.R1,device=kismet,display=kismet,board=kismet,brand=kismet\n")
                
                writer = csv.DictWriter(f, fieldnames=headers)
                writer.writeheader()
                # Strip quotes and remove commas/periods from SSID before writing
                for device in sorted_devices:
                    cleaned_device = {k: str(v).strip('"\'') for k, v in device.items()}
                    # Remove commas and periods from SSID
                    if 'SSID' in cleaned_device:
                        cleaned_device['SSID'] = cleaned_device['SSID'].replace(',', '').replace('.', '')
                    writer.writerow(cleaned_device)
        except Exception as e:
            print(f"Error writing to CSV file: {e}")

    def has_valid_gps(self, gps_data: Dict) -> bool:
        """Check if GPS data is valid (not None)."""
        return (gps_data.get('latitude') is not None and 
                gps_data.get('longitude') is not None and 
                gps_data.get('altitude') is not None)

    def get_device_name(self, device: Dict) -> str:
        """Get the best available device name."""
        # If device name is just MAC with hyphens, try to get better name
        if device.get('Device_Name', '').replace('-', ':').upper() == device.get('BD_ADDR', '').upper():
            # Try Device_Type first if not "Unknown"
            if device.get('Device_Type') and device['Device_Type'] != "Unknown":
                return device['Device_Type']
            # Then try company_name
            if device.get('company_name'):
                return device['company_name']
            # Then try manufacturer_info.company
            if device.get('manufacturer_info', {}).get('company') and device['manufacturer_info']['company'] != "Unknown":
                return device['manufacturer_info']['company']
        return device.get('Device_Name', '').strip('"')  # Remove quotes from device name

    def process_devices_to_csv(self, combined_data: Dict):
        """Process devices from combined data and update CSV file."""
        new_devices = {}
        
        # Process BLE devices
        if 'devices' in combined_data and 'ble_devices' in combined_data['devices']:
            for device in combined_data['devices']['ble_devices']:
                if not self.has_valid_gps(device['gps_data']):
                    continue
                    
                device_data = {
                    'MAC': device['BD_ADDR'],
                    'SSID': self.get_device_name(device),
                    'AuthMode': '',  # BLE devices don't have AuthMode
                    'FirstSeen': device['First_Seen'],
                    'Channel': str(device['Channel']),
                    'RSSI': str(device['RSSI']),
                    'CurrentLatitude': str(device['gps_data']['latitude']),
                    'CurrentLongitude': str(device['gps_data']['longitude']),
                    'AltitudeMeters': str(device['gps_data']['altitude']),
                    'AccuracyMeters': '10',  # Set accuracy to 10 meters
                    'Type': device['Type']
                }
                new_devices[device['BD_ADDR']] = device_data
        
        # Process BT devices
        if 'devices' in combined_data and 'bt_devices' in combined_data['devices']:
            for device in combined_data['devices']['bt_devices']:
                if not self.has_valid_gps(device['gps_data']):
                    continue
                    
                device_data = {
                    'MAC': device['BD_ADDR'],
                    'SSID': self.get_device_name(device),
                    'AuthMode': '',  # BT devices don't have AuthMode
                    'FirstSeen': device['First_Seen'],
                    'Channel': str(device['Channel']),
                    'RSSI': str(device['RSSI']),
                    'CurrentLatitude': str(device['gps_data']['latitude']),
                    'CurrentLongitude': str(device['gps_data']['longitude']),
                    'AltitudeMeters': str(device['gps_data']['altitude']),
                    'AccuracyMeters': '10',  # Set accuracy to 10 meters
                    'Type': device['Type']
                }
                new_devices[device['BD_ADDR']] = device_data
        
        # Process WiFi access points
        if 'devices' in combined_data and 'wifi_devices' in combined_data['devices']:
            if 'access_points' in combined_data['devices']['wifi_devices']:
                for device in combined_data['devices']['wifi_devices']['access_points']:
                    if not self.has_valid_gps(device['gps_data']):
                        continue
                        
                    device_data = {
                        'MAC': device['MAC'],
                        'SSID': device['SSID'].strip('"'),  # Remove quotes from SSID
                        'AuthMode': f"{device['AuthMode']}[ESS]",
                        'FirstSeen': device['FirstSeen'],
                        'Channel': device['Channel'],
                        'RSSI': device['RSSI'],
                        'CurrentLatitude': str(device['gps_data']['latitude']),
                        'CurrentLongitude': str(device['gps_data']['longitude']),
                        'AltitudeMeters': str(device['gps_data']['altitude']),
                        'AccuracyMeters': '10',  # Set accuracy to 10 meters
                        'Type': 'WIFI'  # Set Type to WIFI for access points
                    }
                    new_devices[device['MAC']] = device_data
            
            # Process WiFi stations
            if 'stations' in combined_data['devices']['wifi_devices']:
                for device in combined_data['devices']['wifi_devices']['stations']:
                    if not self.has_valid_gps(device['gps_data']):
                        continue
                        
                    # Get SSID, default to <hidden> if empty
                    ssid = device['Probed'].strip('"') if device['Probed'] else '<hidden>'
                    
                    device_data = {
                        'MAC': device['MAC'],
                        'SSID': ssid,
                        'AuthMode': '[WPA2 PSK CCMP][ESS]',  # Default auth mode for stations with [ESS]
                        'FirstSeen': device['FirstSeen'],
                        'Channel': device['Channel'],
                        'RSSI': device['RSSI'],
                        'CurrentLatitude': str(device['gps_data']['latitude']),
                        'CurrentLongitude': str(device['gps_data']['longitude']),
                        'AltitudeMeters': str(device['gps_data']['altitude']),
                        'AccuracyMeters': '10',  # Set accuracy to 10 meters
                        'Type': 'WIFI'  # Set Type to WIFI for stations
                    }
                    new_devices[device['MAC']] = device_data
        
        # Update existing devices with new data
        for mac, device in new_devices.items():
            if mac in self.existing_devices:
                # Update existing device with new data
                self.existing_devices[mac].update(device)
            else:
                # Add new device
                self.existing_devices[mac] = device
        
        # Write updated data to CSV
        self.write_csv(self.existing_devices)

    def combine_scan_data(self) -> Optional[dict]:
        """Combine data from all scan files into one structure with summary counts"""
        # Read each scan file if it exists
        ble_data = self.read_json_file(self.ble_file)
        bt_data = self.read_json_file(self.bt_file)
        wifi_data = self.read_json_file(self.wifi_file)

        if not any([ble_data, bt_data, wifi_data]):
            return None

        # Initialize counts
        summary = {
            "BLE_DEVICES": 0,
            "BT_DEVICES": 0,
            "ACCESS_POINTS": 0,
            "STATIONS": 0,
            "TOTAL_DEVICES": 0
        }

        # Initialize combined data structure
        combined_data = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "summary": summary,
            "devices": {
                "ble_devices": [],
                "bt_devices": [],
                "wifi_devices": []
            }
        }

        # Process BLE devices
        if ble_data and "devices" in ble_data:
            combined_data["devices"]["ble_devices"] = ble_data["devices"]
            summary["BLE_DEVICES"] = len(ble_data["devices"])

        # Process BT devices
        if bt_data and "devices" in bt_data:
            combined_data["devices"]["bt_devices"] = bt_data["devices"]
            summary["BT_DEVICES"] = len(bt_data["devices"])

        # Process WiFi devices
        if wifi_data:
            access_points = []
            stations = []
            
            if "networks" in wifi_data:
                access_points = wifi_data["networks"]
                summary["ACCESS_POINTS"] = len(access_points)
            
            if "stations" in wifi_data:
                stations = wifi_data["stations"]
                summary["STATIONS"] = len(stations)
            
            combined_data["devices"]["wifi_devices"] = {
                "access_points": access_points,
                "stations": stations
            }

        # Calculate total devices
        summary["TOTAL_DEVICES"] = (
            summary["BLE_DEVICES"] +
            summary["BT_DEVICES"] +
            summary["ACCESS_POINTS"] +
            summary["STATIONS"]
        )

        return combined_data

    def update_combined_file(self):
        """Update the combined output files with current data"""
        combined_data = self.combine_scan_data()
        if not combined_data:
            return False

        success = True
        try:
            # Write JSON file
            with open(self.output_file, 'w') as f:
                json.dump(combined_data, f, indent=2)
            
            # Process and update CSV file
            self.process_devices_to_csv(combined_data)
            
            # Update stats
            self.device_stats.process_wardrive(Path(self.output_file))
                
        except Exception as e:
            print(f"Error writing output files: {e}")
            success = False
            
        return success

class ScannerManager:
    def __init__(self):
        self.script_dir = os.path.dirname(os.path.abspath(__file__))
        self.scan_logs_dir = os.path.join(self.script_dir, 'logs', 'scan_logs')
        
        # Define paths for all JSON files
        self.gps_file = os.path.join(self.scan_logs_dir, 'gps', 'gps_scan.json')
        self.wifi_file = os.path.join(self.scan_logs_dir, 'wifi', 'wifi_scan.json')
        self.bt_file = os.path.join(self.scan_logs_dir, 'bluetooth', 'bt_scan.json')
        self.ble_file = os.path.join(self.scan_logs_dir, 'ble', 'ble_scan.json')
        
        # Store process handles
        self.processes: Dict[str, subprocess.Popen] = {}
        self.running = True
        
        # Create file monitor
        self.file_monitor = FileMonitor(self.scan_logs_dir)
        
        # Register cleanup handler
        atexit.register(self.cleanup)

    def cleanup(self):
        """Clean up processes and backup CSV file on exit."""
        self.cleanup_processes()
        self.cleanup_files()
        self.file_monitor.backup_csv_file()

    def cleanup_files(self):
        """Delete all scanner JSON files and combined output."""
        files_to_delete = [self.gps_file, self.wifi_file, self.bt_file, self.ble_file]
        for file_path in files_to_delete:
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
            except Exception:
                pass
        self.file_monitor.cleanup_output_file()

    async def wait_for_gps_file(self) -> bool:
        """Wait for GPS file to be created and contain valid data."""
        retry_count = 0
        max_retries = 180  # Wait up to 180 seconds
        
        while retry_count < max_retries and self.running:
            if os.path.exists(self.gps_file):
                try:
                    with open(self.gps_file, 'r') as f:
                        data = json.load(f)
                        if data:  # Check if file contains valid JSON
                            return True
                except (json.JSONDecodeError, FileNotFoundError):
                    pass
            await asyncio.sleep(1)
            retry_count += 1
            
        return False

    def start_script(self, script_name: str) -> Optional[subprocess.Popen]:
        """Start a scanner script."""
        script_path = os.path.join(self.script_dir, script_name)
        try:
            process = subprocess.Popen(
                [sys.executable, script_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            return process
        except Exception:
            return None

    def cleanup_processes(self):
        """Clean up processes on exit."""
        self.running = False
        self.file_monitor.running = False
        for name, process in self.processes.items():
            try:
                process.terminate()
                process.wait(timeout=5)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass

    async def monitor_files(self):
        """Monitor files and update combined output."""
        self.file_monitor.ensure_output_directory()
        while self.running:
            if self.file_monitor.has_files_changed():
                self.file_monitor.update_combined_file()
            await asyncio.sleep(1)

    async def run(self):
        """Main run method."""
        try:
            # Clean up any existing files
            self.cleanup_files()
            
            # Start GPS scanner first
            self.processes['gps'] = self.start_script('scan_gps.py')
            
            # Wait for GPS file
            if not await self.wait_for_gps_file():
                self.cleanup_processes()
                return
            
            # Start other scanners
            self.processes['wifi'] = self.start_script('scan_wifi.py')
            self.processes['bt'] = self.start_script('scan_bt.py')
            self.processes['ble'] = self.start_script('scan_ble.py')
            
            # Start file monitoring
            await self.monitor_files()
            
        except asyncio.CancelledError:
            pass
        finally:
            self.cleanup_processes()
            self.cleanup_files()

def main():
    """Main entry point."""
    manager = ScannerManager()
    
    def signal_handler(signum, frame):
        manager.running = False
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        asyncio.run(manager.run())
    except KeyboardInterrupt:
        pass
    finally:
        manager.cleanup_processes()
        manager.cleanup_files()

if __name__ == "__main__":
    main()