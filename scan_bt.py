#!/usr/bin/env python3

import asyncio
import json
import os
import signal
import sys
import bluetooth
from datetime import datetime
from typing import Dict, List, Optional

class OUILookup:
    def __init__(self):
        self.oui_dict = {}
        self.script_dir = os.path.dirname(os.path.abspath(__file__))
        self.oui_file = os.path.join(self.script_dir, 'data', 'oui.txt')
        self.load_oui_data()

    def load_oui_data(self):
        """Load OUI data from oui.txt file."""
        try:
            if not os.path.exists(self.oui_file):
                return

            current_oui = None
            current_info = {}
            
            with open(self.oui_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        if current_oui and current_info:
                            self.oui_dict[current_oui] = current_info
                        current_oui = None
                        current_info = {}
                        continue

                    if "(hex)" in line:
                        parts = line.split("(hex)")
                        if len(parts) >= 2:
                            oui = parts[0].strip().replace("-", "").upper()[:6]
                            company = parts[1].strip()
                            current_oui = oui
                            current_info = {
                                "company": company.strip(),
                                "address": [],
                                "country": ""
                            }
                    elif current_info:
                        line = line.strip()
                        if line and not line.startswith("(base 16)"):
                            if len(line.split()) >= 2:
                                current_info["address"].append(line)
                                if len(line.split()) >= 1 and len(line.split()[-1]) == 2:
                                    current_info["country"] = line.split()[-1]

            if current_oui and current_info:
                self.oui_dict[current_oui] = current_info

        except Exception as e:
            print(f"Error loading OUI data: {e}")

    def lookup_mac(self, mac_addr: str) -> Dict[str, str]:
        """Look up manufacturer information from MAC address."""
        try:
            oui = mac_addr.replace(":", "").replace("-", "").upper()[:6]
            
            if oui in self.oui_dict:
                info = self.oui_dict[oui]
                return {
                    "company": info["company"],
                    "address": "\n".join(info["address"]) if info["address"] else "",
                    "country": info["country"]
                }
        except Exception as e:
            print(f"Error looking up MAC address {mac_addr}: {e}")
        
        return {
            "company": "Unknown",
            "address": "",
            "country": ""
        }

class BluetoothMonitor:
    def __init__(self):
        self.devices: Dict[str, dict] = {}
        self.running = True
        self._lock = asyncio.Lock()
        self.oui_lookup = OUILookup()
        self.script_dir = os.path.dirname(os.path.abspath(__file__))
        self.scan_logs_dir = os.path.join(self.script_dir, 'logs', 'scan_logs')
        self.bt_logs_dir = os.path.join(self.scan_logs_dir, 'bluetooth')
        self.gps_file = os.path.join(self.script_dir, 'logs', 'gps_logs', 'gps_data.json')
        os.makedirs(self.bt_logs_dir, exist_ok=True)
        self.device_classes_file = os.path.join(self.script_dir, 'data', 'device_classes.json')
        self.json_path = os.path.join(self.bt_logs_dir, 'bt_scan.json')
        self.device_classes = self.load_json_data(self.device_classes_file)
        
        # Load existing devices
        self.load_existing_devices()
        
        # Clean up any existing files at startup
        self.cleanup_files()

    def load_existing_devices(self):
        """Load existing devices from the JSON file."""
        try:
            if os.path.exists(self.json_path):
                with open(self.json_path, 'r') as f:
                    data = json.load(f)
                    for device in data.get('devices', []):
                        if 'BD_ADDR' in device:
                            self.devices[device['BD_ADDR']] = device
        except Exception as e:
            print(f"Error loading existing devices: {e}")

    def should_update_device(self, addr: str, new_rssi: int) -> bool:
        """Determine if device should be updated based on RSSI."""
        if addr not in self.devices:
            return True
            
        try:
            current_rssi = int(self.devices[addr].get('RSSI', -100))
            return new_rssi > current_rssi  # Higher RSSI means stronger signal
        except (ValueError, TypeError):
            return True

    def load_json_data(self, file_path: str) -> dict:
        try:
            if os.path.exists(file_path):
                with open(file_path, 'r') as f:
                    return json.load(f)
        except Exception as e:
            print(f"Error loading JSON file {file_path}: {e}")
        return {}

    def get_company_from_mac(self, mac_addr: str) -> str:
        return self.oui_lookup.lookup_mac(mac_addr)["company"]

    def get_current_gps(self) -> Dict[str, Optional[float]]:
        """Get current GPS data from gps_data.json file."""
        try:
            if os.path.exists(self.gps_file):
                with open(self.gps_file, 'r') as f:
                    data = json.load(f)
                    return {
                        "longitude": data.get("longitude"),
                        "latitude": data.get("latitude"),
                        "altitude": data.get("altitude"),
                        "speed": data.get("speed"),
                        "satellites": data.get("satellites")
                    }
        except Exception as e:
            print(f"Error reading GPS data: {e}")
            
        # Return null values if file doesn't exist or there's an error
        return {
            "longitude": None,
            "latitude": None,
            "altitude": None,
            "speed": None,
            "satellites": None
        }

    async def scan_devices(self):
        try:
            nearby_devices = await asyncio.to_thread(
                bluetooth.discover_devices,
                lookup_names=True,
                flush_cache=True,
                lookup_class=True
            )
            
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            async with self._lock:
                for addr, name, device_class in nearby_devices:
                    # Only update if RSSI is stronger or device is new
                    if self.should_update_device(addr, -38):  # Default RSSI value for classic BT
                        major_class = (device_class >> 8) & 0x1F
                        minor_class = (device_class >> 2) & 0x3F
                        device_type = self.get_device_class_name(major_class, minor_class)
                        company_name = self.get_company_from_mac(addr)
                        
                        services = await self.get_device_services(addr)
                        
                        # Get current GPS data
                        gps_data = self.get_current_gps()
                        
                        device_info = {
                            "BD_ADDR": addr,
                            "Device_Name": name if name else addr.replace(":", "-"),
                            "Device_Type": device_type.split(" - ")[1] if " - " in device_type else device_type,
                            "First_Seen": self.devices.get(addr, {}).get("First_Seen", current_time),
                            "Last_Seen": current_time,
                            "Channel": 0,
                            "Frequency": None,
                            "RSSI": -38,
                            "MfgrId": None,
                            "Type": "BT",
                            "raw_data": "",
                            "company_name": company_name,
                            "protocol": "Bluetooth",
                            "services": services,
                            "device_class": {
                                "raw": device_class,
                                "major_class": major_class,
                                "minor_class": minor_class,
                                "major_name": device_type.split(" - ")[0] if " - " in device_type else "Unknown",
                                "minor_name": device_type.split(" - ")[1] if " - " in device_type else "Unknown"
                            },
                            "gps_data": gps_data
                        }
                        
                        self.devices[addr] = device_info
                    
        except bluetooth.BluetoothError as e:
            print(f"Classic Bluetooth scanning error: {e}")
            await asyncio.sleep(0.1)

    async def get_device_services(self, addr: str) -> List[str]:
        services = []
        try:
            service_info = await asyncio.to_thread(
                bluetooth.find_service,
                address=addr
            )
            for service in service_info:
                if service["name"]:
                    services.append(service["name"])
        except bluetooth.BluetoothError:
            pass
        return services

    def get_device_class_name(self, major_class: int, minor_class: int) -> str:
        if not self.device_classes:
            return f"Unknown ({major_class}) - Unknown ({minor_class})"

        major_name = self.device_classes.get("major_classes", {}).get(str(major_class), f"Unknown ({major_class})")
        minor_name = "Uncategorized"

        major_to_minor_map = {
            1: "computer",
            2: "phone",
            3: "lan",
            4: "av",
            5: "peripheral",
            6: "imaging",
            7: "wearable",
            8: "toy",
            9: "health"
        }

        minor_category = major_to_minor_map.get(major_class)
        if minor_category:
            if minor_category == "imaging":
                minor_names = []
                for bit, name in self.device_classes["minor_classes"]["imaging"].items():
                    if minor_class & int(bit):
                        minor_names.append(name)
                minor_name = "/".join(minor_names) if minor_names else "Unknown"
            elif minor_category == "peripheral" and minor_class > 0:
                lower_minor = minor_class & 0x0F
                upper_minor = (minor_class >> 4) & 0x0F
                lower_name = self.device_classes["minor_classes"]["peripheral"].get(str(lower_minor), "Unknown")
                if upper_minor > 0:
                    upper_name = self.device_classes["minor_classes"]["peripheral"].get(str(upper_minor), "")
                    if upper_name:
                        minor_name = f"{upper_name}/{lower_name}"
                else:
                    minor_name = lower_name
            else:
                minor_name = self.device_classes["minor_classes"].get(minor_category, {}).get(str(minor_class), f"Unknown ({minor_class})")

        return f"{major_name} - {minor_name}"

    def cleanup_files(self):
        """Clean up JSON files created by the script."""
        files_to_remove = [
            self.json_path,
            f"{self.json_path}.tmp"
        ]
        
        for file_path in files_to_remove:
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
            except Exception as e:
                print(f"Error removing file {file_path}: {e}")

    async def write_json_data(self):
        """Write current scan data to JSON file."""
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        json_data = {
            "scan_time": current_time,
            "total_devices": len(self.devices),
            "devices": list(self.devices.values())
        }
        
        temp_path = f"{self.json_path}.tmp"
        try:
            with open(temp_path, 'w') as f:
                json.dump(json_data, f, indent=2)
            os.replace(temp_path, self.json_path)
        except Exception as e:
            print(f"Error writing JSON file: {e}")
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except:
                    pass

    async def cleanup(self):
        print("\nCleaning up Bluetooth scanner...")
        self.running = False
        self.cleanup_files()

async def main():
    """Main entry point for Classic Bluetooth scanning."""
    bt_monitor = BluetoothMonitor()
    
    def signal_handler(signum, frame):
        print("\nReceived signal to terminate...")
        asyncio.create_task(bt_monitor.cleanup())
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        print("Starting Classic Bluetooth scan... Press Ctrl+C to stop.")
        
        while bt_monitor.running:
            await bt_monitor.scan_devices()
            
            # Display current devices
            async with bt_monitor._lock:
                print(f"\nDevices found: {len(bt_monitor.devices)}")
                for addr, device in bt_monitor.devices.items():
                    print(f"\nDevice: {device['Device_Name']} ({addr})")
                    print(f"Type: {device['Device_Type']}")
                    print(f"Company: {device['company_name']}")
                    print(f"Last seen: {device['Last_Seen']}")
                    if device['services']:
                        print(f"Services: {', '.join(device['services'])}")
                
                # Write data to JSON file
                await bt_monitor.write_json_data()
            
            await asyncio.sleep(5)
            
    except KeyboardInterrupt:
        print("\nStopping Bluetooth scan...")
    except Exception as e:
        print(f"Error in main: {e}")
    finally:
        await bt_monitor.cleanup()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nKeyboard interrupt received, exiting...")
    except Exception as e:
        print(f"Fatal error: {e}") 