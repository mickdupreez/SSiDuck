#!/usr/bin/env python3

import asyncio
import json
import os
import signal
import sys
from datetime import datetime
from typing import Dict, Optional
from bleak import BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

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

class BLEMonitor:
    def __init__(self):
        self.devices: Dict[str, dict] = {}
        self.running = True
        self.script_dir = os.path.dirname(os.path.abspath(__file__))
        self.scan_logs_dir = os.path.join(self.script_dir, 'logs', 'scan_logs')
        self.ble_logs_dir = os.path.join(self.scan_logs_dir, 'ble')
        self.gps_file = os.path.join(self.script_dir, 'logs', 'scan_logs', 'gps', 'gps_scan.json')
        os.makedirs(self.ble_logs_dir, exist_ok=True)
        self.json_path = os.path.join(self.ble_logs_dir, 'ble_scan.json')
        self.company_codes_file = os.path.join(self.script_dir, 'data', 'company_codes.json')
        self.service_uuids_file = os.path.join(self.script_dir, 'data', 'service_uuids.json')
        self.oui_lookup = OUILookup()
        
        self.company_codes = self.load_json_data(self.company_codes_file)
        self.service_uuids = self.load_json_data(self.service_uuids_file)
        
        # Load existing devices
        self.load_existing_devices()
        
        # Clean up any existing files at startup
        self.cleanup_files()
        
        self.ble_frequencies = {
            37: 2402,
            38: 2426,
            39: 2480,
            **{i: 2402 + (2 * (i-1)) for i in range(1, 37)}
        }

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

    def get_company_name(self, company_code: int) -> str:
        return self.company_codes.get(str(company_code), f"Unknown ({company_code:04x})")

    def get_service_name(self, uuid: str) -> str:
        uuid_short = uuid.split('-')[0].upper()
        
        if len(uuid) == 36:
            if "FE00" in uuid.upper():
                return "Vendor Specific Service"
            elif "0000" in uuid.upper() and uuid.upper().endswith("-0000-1000-8000-00805F9B34FB"):
                base_uuid = uuid.upper().split("-")[0]
                return self.service_uuids.get(base_uuid, f"Custom Service ({base_uuid})")
                
        return self.service_uuids.get(uuid_short, f"Unknown Service ({uuid})")

    def get_device_name_from_services(self, adv: AdvertisementData) -> str:
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
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        device_type_name = self.get_device_name_from_services(adv)
        
        raw_data = ""
        company_name = ""
        protocol = ""
        manufacturer_info = self.oui_lookup.lookup_mac(device.address)
        
        if adv.manufacturer_data:
            mfgr_id = next(iter(adv.manufacturer_data.keys()))
            data = adv.manufacturer_data[mfgr_id]
            raw_data = data.hex()
            company_name = manufacturer_info["company"]
            if company_name == "Unknown":
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
            company_name = manufacturer_info["company"]
        
        # Only update if RSSI is stronger or device is new
        if self.should_update_device(device.address, adv.rssi):
            # Get current GPS data
            gps_data = self.get_current_gps()
            
            device_info = {
                "BD_ADDR": device.address,
                "Device_Name": device.name or adv.local_name or device_type_name or device.address.replace(":", "-"),
                "Device_Type": device_type_name or "Unknown",
                "First_Seen": self.devices.get(device.address, {}).get("First_Seen", current_time),
                "Last_Seen": current_time,
                "Channel": 0,
                "Frequency": mfgr_id if adv.manufacturer_data else 0,  # Using manufacturer ID as the type code
                "RSSI": adv.rssi,
                "MfgrId": next(iter(adv.manufacturer_data.keys())) if adv.manufacturer_data else None,
                "Type": "BLE",
                "raw_data": raw_data,
                "company_name": company_name,
                "manufacturer_info": manufacturer_info,
                "protocol": protocol,
                "services": [self.get_service_name(uuid) for uuid in adv.service_uuids] if adv.service_uuids else [],
                "gps_data": gps_data
            }
            
            self.devices[device.address] = device_info

    async def detection_callback(self, device: BLEDevice, advertisement_data: AdvertisementData):
        self.update_device_info(device, advertisement_data)

    async def run_scanner(self):
        scanner = BleakScanner(detection_callback=self.detection_callback)
        
        while self.running:
            try:
                await scanner.start()
                while self.running:
                    await asyncio.sleep(1)
                await scanner.stop()
            except Exception as e:
                print(f"Error during scanning: {e}")
                await asyncio.sleep(1)

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
        self.running = False
        self.cleanup_files()

    def get_current_gps(self) -> Dict[str, Optional[float]]:
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

async def main():
    """Main entry point for BLE scanning."""
    ble_monitor = BLEMonitor()
    
    def signal_handler(signum, frame):
        ble_monitor.running = False
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        print("Starting BLE scan... Press Ctrl+C to stop.")
        scan_task = asyncio.create_task(ble_monitor.run_scanner())
        
        while ble_monitor.running:
            # Write data to JSON file
            await ble_monitor.write_json_data()
            await asyncio.sleep(5)
            
    except KeyboardInterrupt:
        print("\nBLE scan stopped.")
    except Exception:
        sys.exit(1)
    finally:
        await ble_monitor.cleanup()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception:
        sys.exit(1) 