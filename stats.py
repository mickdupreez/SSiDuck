#!/usr/bin/env python3

import json
import os
import time
import requests
from pathlib import Path
from typing import Dict, Any, List
import hashlib
from datetime import datetime

class DeviceStats:
    def __init__(self):
        self.stats_file = Path("logs/stats/stats.json")
        self.stats_file.parent.mkdir(parents=True, exist_ok=True)
        self.stats = self._load_stats()
        self.wigle_stats = None
        self._get_wigle_stats()
        
    def _get_wigle_stats(self):
        """Get Wigle stats for the user."""
        try:
            settings_file = "settings.json"
            if not os.path.exists(settings_file):
                return
                
            with open(settings_file, 'r') as f:
                settings = json.load(f)
                wigle_settings = settings.get('WIGLE_SETTINGS', {})
                credentials = wigle_settings.get('credentials', {})
                username = credentials.get('username')
                password = credentials.get('password')
                
                if not username or not password:
                    return
                    
            url = "https://api.wigle.net/api/v2/stats/user?user=INFILTRATEHQ"
            response = requests.get(url, auth=(username, password))
            
            if response.status_code == 200:
                stats = response.json()
                if stats.get('success'):
                    self.wigle_stats = stats.get('statistics', {})
                    # Update stats file with Wigle data
                    self.stats['wigle_stats'] = self.wigle_stats
                    self._save_stats()
        except Exception:
            pass

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
            print(f"Error processing {wardrive_file}: {str(e)}")

def main():
    stats = DeviceStats()
    wardrive_dir = Path("logs/scan_logs/wardrive")
    
    print("Starting device stats monitoring...")
    
    try:
        while True:
            # Check for new wardrive.json files
            wardrive_file = wardrive_dir / "wardrive.json"
            
            # Wait for file to exist
            if not wardrive_file.exists():
                print("\rWaiting for wardrive.json file...", end="", flush=True)
                time.sleep(2)
                continue
                
            # Read the current wardrive.json file
            try:
                with open(wardrive_file, 'r') as f:
                    wardrive_data = json.load(f)
                
                # Calculate total devices from current file
                total_devices = (
                    len(wardrive_data.get("devices", {}).get("ble_devices", [])) +
                    len(wardrive_data.get("devices", {}).get("bt_devices", [])) +
                    len(wardrive_data.get("devices", {}).get("wifi_devices", {}).get("access_points", [])) +
                    len(wardrive_data.get("devices", {}).get("wifi_devices", {}).get("stations", []))
                )
                
                # Print the current total
                print(f"\rDevices Logged: {total_devices}", end="", flush=True)
                
                # Process the file to update stats
                stats.process_wardrive(wardrive_file)
            except json.JSONDecodeError:
                print("\rError reading wardrive.json - file may be incomplete, waiting...", end="", flush=True)
                time.sleep(2)
                continue
            
            # Sleep for a while before checking again
            time.sleep(2)  # Check every 2 seconds
            
    except KeyboardInterrupt:
        print("\nStopping device stats monitoring...")
    except Exception as e:
        print(f"Error in main loop: {str(e)}")

if __name__ == "__main__":
    main() 