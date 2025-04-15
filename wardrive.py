#!/usr/bin/env python3

import asyncio
import json
import os
import signal
import sys
import time
import csv
from datetime import datetime
from typing import Dict, List, Optional
import subprocess
import atexit
import shutil
import requests
from pathlib import Path

class FileMonitor:
    def __init__(self, scan_logs_dir: str):
        self.scan_logs_dir = scan_logs_dir
        self.ble_file = os.path.join(scan_logs_dir, 'ble', 'ble_scan.json')
        self.bt_file = os.path.join(scan_logs_dir, 'bluetooth', 'bt_scan.json')
        self.wifi_file = os.path.join(scan_logs_dir, 'wifi', 'wifi_scan.json')
        
        # Load settings for Wigle upload
        self.settings_file = "settings.json"
        self.settings = self.load_settings()
        wigle_settings = self.settings.get('WIGLE_SETTINGS', {})
        self.api_key = wigle_settings.get('api_key')
        
        # Get upload settings
        upload_settings = wigle_settings.get('upload', {})
        self.upload_enabled = upload_settings.get('enabled', False)
        self.wigle_upload_url = upload_settings.get('url', 'https://api.wigle.net/api/v2/file/upload')
        
        # Set output paths from settings
        self.output_file = upload_settings.get('output_file', os.path.join(scan_logs_dir, 'wardrive', 'wardrive.json'))
        self.csv_file = upload_settings.get('csv_file', os.path.join(scan_logs_dir, 'wardrive', 'wardrive.csv'))
        self.wardrive_logs_dir = upload_settings.get('output_dir', os.path.join(scan_logs_dir, 'wardrive', 'upload'))
        os.makedirs(self.wardrive_logs_dir, exist_ok=True)
        
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

    def backup_csv_file(self):
        """Backup the CSV file with current date and time if it exists and upload to Wigle."""
        if os.path.exists(self.csv_file):
            current_time = datetime.now().strftime("%d-%m-%y_%H:%M")
            backup_filename = f"SSIDuck_{current_time}.csv"
            backup_path = os.path.join(self.wardrive_logs_dir, backup_filename)
            try:
                shutil.move(self.csv_file, backup_path)
                print(f"Backed up existing CSV file to {backup_path}")
                
                # Upload the backed up file to Wigle
                if self.upload_file_to_wigle(backup_path):
                    # Only delete the file if upload was successful
                    try:
                        os.remove(backup_path)
                        print(f"Deleted uploaded file: {backup_path}")
                    except Exception as e:
                        print(f"Error deleting uploaded file: {e}")
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
        
        # Store Wigle stats
        self.wigle_stats = None
        
        # Register cleanup handler
        atexit.register(self.cleanup)

    def check_wigle_stats(self) -> bool:
        """Check Wigle stats for the user."""
        try:
            settings_file = "settings.json"
            if not os.path.exists(settings_file):
                print("No settings.json file found")
                return False
                
            with open(settings_file, 'r') as f:
                settings = json.load(f)
                wigle_settings = settings.get('WIGLE_SETTINGS', {})
                credentials = wigle_settings.get('credentials', {})
                username = credentials.get('username')
                password = credentials.get('password')
                
                if not username or not password:
                    print("Missing Wigle credentials in settings.json")
                    return False
                    
            url = "https://api.wigle.net/api/v2/stats/user?user=INFILTRATEHQ"
            response = requests.get(url, auth=(username, password))
            
            if response.status_code == 200:
                stats = response.json()
                if stats.get('success'):
                    statistics = stats.get('statistics', {})
                    self.wigle_stats = statistics
                    # Clear screen and move to top
                    print("\033[2J\033[H", end="")
                    print("Starting all scanners... Press Ctrl+C to stop.\n")
                    print("INFILTRATE HQ")
                    print(f"Global Rank: {statistics.get('rank', 'N/A')} Monthly Rank: {statistics.get('monthRank', 'N/A')}")
                    print(f"WiFi Networks with GPS: {statistics.get('discoveredWiFiGPS', 0)} Total: {statistics.get('discoveredWiFi', 0)}")
                    print(f"Bluetooth Devices with GPS: {statistics.get('discoveredBtGPS', 0)} Total: {statistics.get('discoveredBt', 0)}\n")
                    print("LIVE STATS:")
                    print("WIFI Devices: 0")
                    print("BLE Devices: 0")
                    print("BT Devices: 0")
                    print("TOTAL Devices: 0")
                    print()  # Add blank line
                    # Move cursor to the start of the last line
                    print("\033[1A", end="")
                    return True
                else:
                    print("Failed to get Wigle stats: API returned unsuccessful response")
                    return False
            else:
                print(f"Failed to get Wigle stats. Status code: {response.status_code}")
                return False
                
        except Exception as e:
            print(f"Error checking Wigle stats: {e}")
            return False

    def update_live_stats(self, wifi_count: int, ble_count: int, bt_count: int):
        """Update the live stats display."""
        # Save cursor position
        print("\033[s", end="")
        # Move cursor up 5 lines (4 stats lines + 1 blank line)
        print("\033[5A", end="")
        # Clear from cursor to end of line
        print("\033[K", end="")
        print(f"WIFI Devices: {wifi_count}")
        print("\033[K", end="")
        print(f"BLE Devices: {ble_count}")
        print("\033[K", end="")
        print(f"BT Devices: {bt_count}")
        print("\033[K", end="")
        print(f"TOTAL Devices: {wifi_count + ble_count + bt_count}")
        print("\033[K", end="")  # Clear the blank line
        print()  # Add new blank line
        # Restore cursor position
        print("\033[u", end="")
        # Flush the output
        sys.stdout.flush()

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
                if self.file_monitor.update_combined_file():
                    # Get current device counts
                    wifi_count = len(self.file_monitor.existing_devices)
                    ble_count = sum(1 for device in self.file_monitor.existing_devices.values() if device['Type'] == 'BLE')
                    bt_count = sum(1 for device in self.file_monitor.existing_devices.values() if device['Type'] == 'BT')
                    # Update live stats display
                    self.update_live_stats(wifi_count, ble_count, bt_count)
            await asyncio.sleep(1)

    async def run(self):
        """Main run method."""
        try:
            # Check Wigle stats first
            self.check_wigle_stats()
            
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
        print("Starting all scanners... Press Ctrl+C to stop.")
        asyncio.run(manager.run())
    except KeyboardInterrupt:
        pass
    finally:
        manager.cleanup_processes()
        manager.cleanup_files()

if __name__ == "__main__":
    main() 