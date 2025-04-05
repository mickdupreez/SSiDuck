#!/usr/bin/env python3

import asyncio
import json
import os
import signal
import sys
import bluetooth
from datetime import datetime
from typing import Dict, Optional, List

from bleak import BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

class OUILookup:
    def __init__(self):
        self.oui_dict = {}
        self.script_dir = os.path.dirname(os.path.abspath(__file__))
        self.oui_file = os.path.join(self.script_dir, 'oui.txt')
        self.load_oui_data()

    def load_oui_data(self):
        """Load OUI data from oui.txt file."""
        try:
            if not os.path.exists(self.oui_file):
                print(f"Warning: OUI file not found at {self.oui_file}")
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
                            if len(line.split()) >= 2:  # Likely an address line
                                current_info["address"].append(line)
                                # Try to extract country code (usually last line, 2 characters)
                                if len(line.split()) >= 1 and len(line.split()[-1]) == 2:
                                    current_info["country"] = line.split()[-1]

            # Add the last entry if exists
            if current_oui and current_info:
                self.oui_dict[current_oui] = current_info

            print(f"Loaded {len(self.oui_dict)} OUI entries")
        except Exception as e:
            print(f"Error loading OUI data: {e}")

    def lookup_mac(self, mac_addr: str) -> Dict[str, str]:
        """Look up manufacturer information from MAC address."""
        try:
            # Clean up MAC address format
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

class ClassicBluetoothScanner:
    def __init__(self):
        self.devices: Dict[str, dict] = {}
        self.running = True
        self._lock = asyncio.Lock()
        self.oui_lookup = OUILookup()
        self.script_dir = os.path.dirname(os.path.abspath(__file__))
        self.device_classes_file = os.path.join(self.script_dir, 'device_classes.json')
        self.device_classes = self.load_json_data(self.device_classes_file)
        print("Classic Bluetooth scanner initialized")

    def load_json_data(self, file_path: str) -> dict:
        """Load data from a JSON file."""
        try:
            if os.path.exists(file_path):
                with open(file_path, 'r') as f:
                    return json.load(f)
        except Exception as e:
            print(f"Error loading JSON file {file_path}: {e}")
        return {}

    def get_company_from_mac(self, mac_addr: str) -> str:
        """Get company name from MAC address OUI."""
        return self.oui_lookup.lookup_mac(mac_addr)["company"]

    async def scan_devices(self):
        """Scan for classic Bluetooth devices asynchronously."""
        try:
            # Run the blocking discover_devices call in a separate thread without duration
            nearby_devices = await asyncio.to_thread(
                bluetooth.discover_devices,
                lookup_names=True,
                flush_cache=True,
                lookup_class=True
            )
            
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            async with self._lock:
                for addr, name, device_class in nearby_devices:
                    major_class = (device_class >> 8) & 0x1F
                    minor_class = (device_class >> 2) & 0x3F
                    device_type = self.get_device_class_name(major_class, minor_class)
                    company_name = self.get_company_from_mac(addr)
                    
                    # Get services asynchronously
                    services = await self.get_device_services(addr)
                    
                    if addr in self.devices:
                        # Update only dynamic information for existing device
                        self.devices[addr].update({
                            "Last_Seen": current_time,
                            "Device_Name": name if name else addr.replace(":", "-"),
                            "services": services
                        })
                    else:
                        # Add new device with complete information
                        self.devices[addr] = {
                            "BD_ADDR": addr,
                            "Device_Name": name if name else addr.replace(":", "-"),
                            "Device_Type": device_type.split(" - ")[1] if " - " in device_type else device_type,
                            "First_Seen": current_time,
                            "Last_Seen": current_time,
                            "Channel": None,
                            "Frequency": None,
                            "RSSI": None,
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
                            }
                        }
                    
        except bluetooth.BluetoothError as e:
            print(f"Classic Bluetooth scanning error: {e}")
            await asyncio.sleep(0.1)  # Brief pause on error before retrying

    async def get_device_services(self, addr: str) -> List[str]:
        """Get available services for a classic Bluetooth device asynchronously."""
        services = []
        try:
            # Run the blocking find_service call in a separate thread
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
        """Get human-readable device class name."""
        if not self.device_classes:
            return f"Unknown ({major_class}) - Unknown ({minor_class})"

        major_name = self.device_classes.get("major_classes", {}).get(str(major_class), f"Unknown ({major_class})")
        minor_name = "Uncategorized"

        # Map major class numbers to their corresponding minor class categories
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
                # Imaging is a bitmap
                minor_names = []
                for bit, name in self.device_classes["minor_classes"]["imaging"].items():
                    if minor_class & int(bit):
                        minor_names.append(name)
                minor_name = "/".join(minor_names) if minor_names else "Unknown"
            elif minor_category == "peripheral" and minor_class > 0:
                # Handle keyboard/pointing device combo
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

class EnhancedBLEScanner:
    def __init__(self):
        self.devices: Dict[str, dict] = {}
        self.running = True
        self.script_dir = os.path.dirname(os.path.abspath(__file__))
        self.json_path = os.path.join(self.script_dir, 'bluetooth_scan.json')
        self.company_codes_file = os.path.join(self.script_dir, 'company_codes.json')
        self.service_uuids_file = os.path.join(self.script_dir, 'service_uuids.json')
        self.oui_lookup = OUILookup()
        
        # Load lookup data
        self.company_codes = self.load_json_data(self.company_codes_file)
        self.service_uuids = self.load_json_data(self.service_uuids_file)
        
        self.ble_frequencies = {
            37: 2402,
            38: 2426,
            39: 2480,
            **{i: 2402 + (2 * (i-1)) for i in range(1, 37)}
        }

    def load_json_data(self, file_path: str) -> dict:
        """Load data from a JSON file."""
        try:
            if os.path.exists(file_path):
                with open(file_path, 'r') as f:
                    return json.load(f)
        except Exception as e:
            print(f"Error loading JSON file {file_path}: {e}")
        return {}

    def get_company_name(self, company_code: int) -> str:
        """Get company name from company code."""
        return self.company_codes.get(str(company_code), f"Unknown ({company_code:04x})")

    def get_service_name(self, uuid: str) -> str:
        """Get standard service name from UUID."""
        uuid_short = uuid.split('-')[0].upper()
        
        if len(uuid) == 36:  # Full UUID
            if "FE00" in uuid.upper():
                return "Vendor Specific Service"
            elif "0000" in uuid.upper() and uuid.upper().endswith("-0000-1000-8000-00805F9B34FB"):
                base_uuid = uuid.upper().split("-")[0]
                return self.service_uuids.get(base_uuid, f"Custom Service ({base_uuid})")
                
        return self.service_uuids.get(uuid_short, f"Unknown Service ({uuid})")



    def get_device_name_from_services(self, adv: AdvertisementData) -> Optional[str]:
        """Get device name based on advertised services and manufacturer data."""
        if not adv.service_uuids and not adv.manufacturer_data:
            return None
            
        device_type = None
        
        for uuid in adv.service_uuids:
            uuid_upper = uuid.upper()
            if "180A" in uuid_upper:
                device_type = "Generic BLE Device"
            elif "180D" in uuid_upper:
                device_type = "Heart Rate Monitor"
            elif "180F" in uuid_upper:
                device_type = "Battery Powered Device"
            elif "1812" in uuid_upper:
                device_type = "Input Device"
            elif "1826" in uuid_upper:
                device_type = "Fitness Equipment"
            elif "FE07" in uuid_upper or "FE08" in uuid_upper:
                device_type = "Apple Accessory"
            elif "FEEE" in uuid_upper:
                device_type = "Eddystone Beacon"
            elif "FE59" in uuid_upper:
                device_type = "Fast Pair Device"
            elif "FE2C" in uuid_upper:
                device_type = "Windows Device"
            elif "1819" in uuid_upper:
                device_type = "Location Tracker"
            elif "181A" in uuid_upper:
                device_type = "Environmental Sensor"
            elif "183E" in uuid_upper:
                device_type = "Medical Device"
            elif "1844" in uuid_upper or "1846" in uuid_upper:
                device_type = "Media Controller"
                
        if adv.manufacturer_data:
            mfg_id = next(iter(adv.manufacturer_data.keys()))
            data = adv.manufacturer_data[mfg_id]
            
            if mfg_id == 76:
                if len(data) >= 2:
                    type_code = data[0]
                    if type_code == 0x01:
                        device_type = "iBeacon"
                    elif type_code == 0x06:
                        device_type = "AirPods"
                    elif type_code == 0x07:
                        device_type = "AirPrint Device"
                    elif type_code == 0x09:
                        device_type = "AirDrop Device"
                    elif type_code == 0x10:
                        device_type = "AirPlay Device"
            elif mfg_id == 6:
                device_type = "Windows Device"
            elif mfg_id == 117:
                device_type = "Samsung Device"
            elif mfg_id == 224:
                device_type = "Google Device"
                
        return device_type

    def update_device_info(self, device: BLEDevice, adv: AdvertisementData):
        """Update device information with the latest scan data."""
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        channel = 37
        frequency = self.ble_frequencies[channel]
        
        device_type_name = self.get_device_name_from_services(adv)
        
        raw_data = ""
        company_name = ""
        protocol = ""
        manufacturer_info = self.oui_lookup.lookup_mac(device.address)
        
        if adv.manufacturer_data:
            mfgr_id = next(iter(adv.manufacturer_data.keys()))
            data = adv.manufacturer_data[mfgr_id]
            raw_data = data.hex()
            company_name = manufacturer_info["company"]  # Use OUI lookup first
            if company_name == "Unknown":  # Fall back to manufacturer ID lookup
                company_name = self.get_company_name(mfgr_id)
            
            if mfgr_id == 76 and len(data) >= 2:
                type_code = data[0]
                if type_code == 0x01:
                    protocol = "iBeacon"
                elif type_code == 0x06:
                    protocol = "AirPods"
                elif type_code == 0x07:
                    protocol = "AirPrint"
                elif type_code == 0x09:
                    protocol = "AirDrop"
                elif type_code == 0x10:
                    protocol = "AirPlay"
        else:
            company_name = manufacturer_info["company"]  # Use OUI lookup for devices without manufacturer data
        
        device_info = {
            "BD_ADDR": device.address,
            "Device_Name": device.name or adv.local_name or device_type_name or device.address.replace(":", "-"),
            "Device_Type": device_type_name or "Unknown",
            "First_Seen": current_time,
            "Last_Seen": current_time,
            "Channel": channel,
            "Frequency": frequency,
            "RSSI": adv.rssi,
            "MfgrId": next(iter(adv.manufacturer_data.keys())) if adv.manufacturer_data else None,
            "Type": "BLE",
            "raw_data": raw_data,
            "company_name": company_name,
            "manufacturer_info": manufacturer_info,  # Add full manufacturer info
            "protocol": protocol,
            "services": [self.get_service_name(uuid) for uuid in adv.service_uuids] if adv.service_uuids else []
        }
        
        if device.address not in self.devices:
            self.devices[device.address] = device_info
        else:
            # Preserve First_Seen timestamp for existing devices
            device_info["First_Seen"] = self.devices[device.address]["First_Seen"]
            self.devices[device.address] = device_info

    async def detection_callback(self, device: BLEDevice, advertisement_data: AdvertisementData):
        """Callback for each device detection."""
        self.update_device_info(device, advertisement_data)

    async def run_scanner(self):
        """Run the BLE scanner."""
        scanner = BleakScanner(detection_callback=self.detection_callback)
        
        while self.running:
            try:
                await scanner.start()
                print("BLE Scanner started. Press Ctrl+C to stop...")
                while self.running:
                    await asyncio.sleep(1)
                await scanner.stop()
            except Exception as e:
                print(f"Error during scanning: {e}")
                await asyncio.sleep(1)

    def cleanup(self, signum, frame):
        """Cleanup handler for graceful shutdown."""
        print("\nStopping BLE scanner...")
        self.running = False

class EnhancedScanner:
    def __init__(self):
        self.ble_scanner = EnhancedBLEScanner()
        self.classic_scanner = ClassicBluetoothScanner()
        self.running = True
        self.script_dir = os.path.dirname(os.path.abspath(__file__))
        self.json_path = os.path.join(self.script_dir, 'bluetooth_scan.json')
        self.all_devices = {}  # Keep track of all devices across scans
        self._write_task = None
        
        # Delete existing JSON file if it exists
        if os.path.exists(self.json_path):
            try:
                os.remove(self.json_path)
                print(f"Deleted existing JSON file {self.json_path}")
            except Exception as e:
                print(f"Error deleting existing JSON file: {e}")

    async def load_existing_devices(self):
        """Load existing devices from JSON file if it exists."""
        try:
            if os.path.exists(self.json_path):
                with open(self.json_path, 'r') as f:
                    data = json.load(f)
                    if "DEVICES" in data:
                        for device in data["DEVICES"]:
                            if "BD_ADDR" in device:
                                self.all_devices[device["BD_ADDR"]] = device
                    print(f"Loaded {len(self.all_devices)} existing devices from {self.json_path}")
        except Exception as e:
            print(f"Error loading existing devices: {e}")

    async def write_json_data(self):
        """Write current device data to JSON file with thread safety."""
        current_time = datetime.now()
        
        # Update BLE devices
        for addr, device in self.ble_scanner.devices.items():
            if addr in self.all_devices:
                # Update only dynamic fields for existing devices
                self.all_devices[addr].update({
                    "Last_Seen": device["Last_Seen"],
                    "RSSI": device["RSSI"]
                })
            else:
                # Create a new device entry without unwanted fields
                filtered_device = {k: v for k, v in device.items() 
                                if k not in ['manufacturer_info', 'raw_data', 'country', 'address', 'Channel']}
                self.all_devices[addr] = filtered_device

        # Update Classic BT devices
        async with self.classic_scanner._lock:
            for addr, device in self.classic_scanner.devices.items():
                if "device_class" in device:
                    device["device_class"] = {
                        "raw": device["device_class"]["raw"],
                        "major_class": device["device_class"]["major_class"],
                        "minor_class": device["device_class"]["minor_class"],
                        "major_name": device["device_class"]["major_name"],
                        "minor_name": device["device_class"]["minor_name"]
                    }
                if addr in self.all_devices:
                    # Update only dynamic fields for existing devices
                    self.all_devices[addr].update({
                        "Last_Seen": device["Last_Seen"],
                        "services": device["services"]
                    })
                    # Ensure Type remains "BT" for classic devices
                    if self.all_devices[addr].get("Type") != "BT":
                        self.all_devices[addr]["Type"] = "BT"
                else:
                    # Create a new device entry without unwanted fields
                    filtered_device = {k: v for k, v in device.items() 
                                    if k not in ['manufacturer_info', 'raw_data', 'country', 'address', 'Channel']}
                    filtered_device["Type"] = "BT"
                    self.all_devices[addr] = filtered_device

        # Prepare flattened data structure
        devices_list = []
        for device in self.all_devices.values():
            # Create a flattened device entry
            flattened_device = {
                "First_Seen": device.get("First_Seen", ""),
                "BD_ADDR": device.get("BD_ADDR", ""),
                "Device_Name": device.get("Device_Name", ""),
                "Device_Type": device.get("Device_Type", "Unknown"),
                "Type": device.get("Type", "Unknown"),
                "company_name": device.get("company_name", "Unknown"),
                "RSSI": device.get("RSSI"),
                "Frequency": device.get("Frequency"),
                "MfgrId": device.get("MfgrId"),
                "protocol": device.get("protocol", ""),
                "services": device.get("services", []),
                "device_class_major": device.get("device_class", {}).get("major_name", "") if "device_class" in device else "",
                "device_class_minor": device.get("device_class", {}).get("minor_name", "") if "device_class" in device else "",
                "Last_Seen": device.get("Last_Seen", "")
            }
            
            devices_list.append(flattened_device)

        # Create the final data structure
        data = {
            "scan_time": current_time.strftime("%Y-%m-%d %H:%M:%S"),
            "total_devices": len(devices_list),
            "ble_devices": len(self.ble_scanner.devices),
            "bt_devices": len(self.classic_scanner.devices),
            "devices": devices_list
        }
        
        # Write to temporary file first
        temp_path = f"{self.json_path}.tmp"
        try:
            with open(temp_path, 'w') as f:
                json.dump(data, f, indent=2)
            os.replace(temp_path, self.json_path)  # Atomic replace
        except Exception as e:
            print(f"Error writing JSON file: {e}")
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except:
                    pass

    async def periodic_write(self):
        """Periodically write data to JSON file."""
        while self.running:
            try:
                await self.write_json_data()
                await asyncio.sleep(1)  # Write every second
            except Exception as e:
                print(f"Error in periodic write: {e}")
                await asyncio.sleep(1)

    async def run_classic_scanner(self):
        """Run the classic Bluetooth scanner continuously."""
        while self.running:
            try:
                await self.classic_scanner.scan_devices()
                await asyncio.sleep(0.1)  # Small delay to prevent CPU overuse
            except Exception as e:
                print(f"Error in classic scanner: {e}")
                await asyncio.sleep(0.1)  # Brief pause on error before retrying

    async def run_scanners(self):
        """Run both BLE and classic Bluetooth scanners concurrently."""
        try:
            # Load existing devices first
            await self.load_existing_devices()
            
            # Create tasks for both scanners and periodic write
            ble_task = asyncio.create_task(self.ble_scanner.run_scanner())
            classic_task = asyncio.create_task(self.run_classic_scanner())
            write_task = asyncio.create_task(self.periodic_write())
            
            # Run all tasks concurrently
            await asyncio.gather(ble_task, classic_task, write_task)
        except Exception as e:
            print(f"Error in scanner tasks: {e}")
            self.cleanup(None, None)

    def cleanup(self, signum, frame):
        """Cleanup handler for graceful shutdown."""
        print("\nStopping scanners...")
        self.running = False
        self.ble_scanner.running = False
        self.classic_scanner.running = False
        
        # Delete the JSON file
        if os.path.exists(self.json_path):
            try:
                os.remove(self.json_path)
                print(f"Deleted JSON file {self.json_path}")
            except Exception as e:
                print(f"Error deleting JSON file: {e}")
        
        # Delete temporary file if it exists
        if os.path.exists(f"{self.json_path}.tmp"):
            try:
                os.remove(f"{self.json_path}.tmp")
                print(f"Deleted temporary file {self.json_path}.tmp")
            except Exception as e:
                print(f"Error deleting temporary file: {e}")

def main():
    scanner = EnhancedScanner()
    signal.signal(signal.SIGINT, scanner.cleanup)
    signal.signal(signal.SIGTERM, scanner.cleanup)
    
    try:
        asyncio.run(scanner.run_scanners())
    except KeyboardInterrupt:
        scanner.cleanup(None, None)  # Ensure cleanup runs on keyboard interrupt
    except Exception as e:
        print(f"Error in main: {e}")
        scanner.cleanup(None, None)  # Ensure cleanup runs on any error

if __name__ == "__main__":
    main() 