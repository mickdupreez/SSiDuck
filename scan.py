#!/usr/bin/env python3

import asyncio
import json
import os
import signal
import sys
import time
import bluetooth
from datetime import datetime
from typing import Dict, Optional, List
import subprocess
import re

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
            pass

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

class WiFiMonitor:
    def __init__(self):
        self.monitor_interface = None
        self.original_interface = 'wlan1'
        self.running = True
        script_dir = os.path.dirname(os.path.abspath(__file__))
        self.scan_logs_dir = os.path.join(script_dir, 'logs', 'scan_logs')
        os.makedirs(self.scan_logs_dir, exist_ok=True)
        self.base_path = os.path.join(self.scan_logs_dir, 'wifi_scan')
        self.oui_lookup = OUILookup()
        
        # Device type detection patterns
        self.ssid_patterns = {
            r'iPhone.*of': 'Apple iPhone',
            r'Galaxy.*': 'Samsung Device',
            r'Huawei.*': 'Huawei Device',
            r'AndroidAP': 'Android Device',
            r'Nest-.*': 'Nest Device',
            r'Ring-.*': 'Ring Device',
            r'DIRECT-.*': 'WiFi Direct Device',
            r'GoPro.*': 'GoPro Camera',
            r'Amazon.*': 'Amazon Device',
            r'Xbox-.*': 'Xbox Console',
            r'PS\d-.*': 'PlayStation Console',
            r'Nintendo.*': 'Nintendo Console',
            r'ChromeCast.*': 'Chromecast Device',
            r'HP-Print-.*': 'HP Printer',
            r'EPSON_.*': 'Epson Printer'
        }
        
        # Protocol detection from auth modes
        self.auth_protocols = {
            'WPA3': ['SAE'],
            'WPA2-Enterprise': ['EAP', '802.1X'],
            'WPA2-Personal': ['PSK', 'CCMP'],
            'Mixed Mode': ['TKIP', 'CCMP'],
            'Open': ['OPN'],
            'WEP': ['WEP']
        }

    def format_auth_mode(self, privacy, cipher, authentication):
        components = []
        if privacy:
            components.append(privacy)
        if authentication:
            components.append(authentication)
        if cipher:
            components.append(cipher)
        return f"[{' '.join(components)}]"

    async def write_json_data(self, networks, stations):
        data = {
            "Scan_Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "NETWORKS": networks,
            "STATIONS": stations,
            "summary": {
                "Networks": len(networks),
                "Stations": len(stations),
                "Last_Updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
        }
        
        temp_path = f"{self.json_path}.tmp"
        try:
            with open(temp_path, 'w') as f:
                json.dump(data, f, indent=2)
            os.replace(temp_path, self.json_path)
        except Exception as e:
            print(f"Error writing JSON file: {e}")
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except:
                    pass

    async def get_wireless_interfaces(self):
        try:
            proc = await asyncio.create_subprocess_exec(
                'sudo', 'iwconfig',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL
            )
            await proc.communicate()
            return ['wlan1']
        except Exception:
            sys.exit(1)

    async def manage_network_manager(self, interface, action='stop'):
        try:
            if action == 'stop':
                proc = await asyncio.create_subprocess_exec(
                    'sudo', 'nmcli', 'device', 'set', interface, 'managed', 'no',
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL
                )
            else:
                proc = await asyncio.create_subprocess_exec(
                    'sudo', 'nmcli', 'device', 'set', interface, 'managed', 'yes',
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL
                )
            await proc.communicate()
        except Exception:
            pass

    async def start_monitor_mode(self, interface):
        try:
            await self.manage_network_manager(interface, 'stop')
            
            # Start monitor mode on the specific interface
            proc = await asyncio.create_subprocess_exec(
                'sudo', 'airmon-ng', 'start', interface,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )
            await proc.communicate()
            self.monitor_interface = interface + 'mon'
            self.original_interface = interface
        except Exception:
            sys.exit(1)

    async def stop_monitor_mode(self):
        if self.monitor_interface:
            try:
                proc = await asyncio.create_subprocess_exec(
                    'sudo', 'airmon-ng', 'stop', self.monitor_interface,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL
                )
                await proc.communicate()
                await self.manage_network_manager(self.original_interface, 'start')
            except Exception:
                pass

    def detect_device_type(self, ssid: str, auth_mode: str, mac: str) -> str:
        # Check SSID patterns
        for pattern, device_type in self.ssid_patterns.items():
            if re.search(pattern, ssid, re.IGNORECASE):
                return device_type
                
        # Check auth mode patterns
        if any(mode in auth_mode for mode in ['EAP', '802.1X']):
            return 'Enterprise Device'
        elif 'SAE' in auth_mode:
            return 'WPA3 Capable Device'
            
        # Check MAC patterns
        company_info = self.oui_lookup.lookup_mac(mac)
        if company_info["company"] != "Unknown":
            if company_info["company"] in ["Cisco", "Aruba", "Ruckus", "Ubiquiti"]:
                return "Enterprise Access Point"
            elif company_info["company"] in ["Apple", "Samsung", "Google", "OnePlus"]:
                return "Mobile Device"
            elif company_info["company"] in ["Intel", "Realtek", "Broadcom"]:
                return "Computer"
                
        return "Unknown Device"

    def detect_protocol(self, auth_mode: str) -> str:
        protocols = []
        for protocol, indicators in self.auth_protocols.items():
            if any(indicator in auth_mode for indicator in indicators):
                protocols.append(protocol)
        return " + ".join(protocols) if protocols else "Unknown"

    def parse_csv_network_info(self, line):
        try:
            parts = line.strip().split(',')
            if len(parts) >= 14:
                bssid = parts[0].strip()
                first_seen = parts[1].strip()
                last_seen = parts[2].strip()
                channel = parts[3].strip()
                speed = parts[4].strip()
                privacy = parts[5].strip()
                cipher = parts[6].strip()
                authentication = parts[7].strip()
                power = parts[8].strip()
                beacons = parts[9].strip()
                essid = parts[13].strip().strip('"').replace('\u2019', "'")
                
                if bssid and power and channel:
                    auth_mode = self.format_auth_mode(privacy, cipher, authentication)
                    company_info = self.oui_lookup.lookup_mac(bssid)
                    device_type = self.detect_device_type(essid, auth_mode, bssid)
                    protocol = self.detect_protocol(auth_mode)
                    
                    return {
                        "FirstSeen": first_seen,
                        "LastSeen": last_seen,
                        "MAC": bssid,
                        "RSSI": power,
                        "Channel": channel,
                        "Speed": speed,
                        "SSID": essid if essid else "<hidden>",
                        "AuthMode": auth_mode,
                        "Beacons": beacons,
                        "company_name": company_info["company"],
                        "device_type": device_type,
                        "protocol": protocol,
                        "capabilities": {
                            "wps": "WPS" in auth_mode,
                            "hidden": not essid,
                            "enterprise": any(x in auth_mode for x in ["EAP", "802.1X"]),
                            "mesh": "MESH" in auth_mode or "SAE" in auth_mode
                        }
                    }
            return None
        except Exception as e:
            return None

    def parse_csv_station_info(self, line):
        try:
            parts = line.strip().split(',')
            if len(parts) >= 6:
                mac = parts[0].strip()
                first_seen = parts[1].strip()
                last_seen = parts[2].strip()
                power = parts[3].strip()
                bssid = parts[5].strip()
                probed = parts[6].strip().replace('\u2019', "'") if len(parts) > 6 else ""
                
                if mac:
                    company_info = self.oui_lookup.lookup_mac(mac)
                    device_type = self.detect_device_type(probed, "", mac)
                    
                    return {
                        "MAC": mac,
                        "FirstSeen": first_seen,
                        "LastSeen": last_seen,
                        "RSSI": power,
                        "BSSID": "not associated" if bssid == "(not associated)" else bssid,
                        "Probed": probed,
                        "company_name": company_info["company"],
                        "device_type": device_type,
                        "probe_history": [probed] if probed else []
                    }
            return None
        except Exception as e:
            return None

    async def get_wifi_data(self):
        """Get current WiFi scan data without writing to file."""
        networks_info = []
        stations_info = []
        
        csv_file_path = f"{self.base_path}-01.csv"
        
        try:
            if os.path.exists(csv_file_path):
                with open(csv_file_path, 'r') as f:
                    lines = f.readlines()
                    
                    in_stations_section = False
                    for line in lines:
                        line = line.strip()
                        if not line:
                            continue
                            
                        if line.startswith("Station MAC"):
                            in_stations_section = True
                            continue
                            
                        if not in_stations_section:
                            if line and not line.startswith("BSSID"):
                                network = self.parse_csv_network_info(line)
                                if network:
                                    networks_info.append(network)
                        else:
                            station = self.parse_csv_station_info(line)
                            if station:
                                stations_info.append(station)
                                
        except Exception as e:
            print(f"Error reading WiFi data: {e}")
            
        return {
            "networks": networks_info,
            "stations": stations_info,
            "summary": {
                "networks": len(networks_info),
                "stations": len(stations_info),
                "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
        }

    async def scan_networks(self):
        try:
            cmd = f"sudo airodump-ng {self.monitor_interface} --channel 1,2,3,4,5,6,7,8,9,10,11,12,13,36,40,44,48,149,153,157,161 --output-format csv -w {self.base_path} --write-interval 1"
            
            process = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )
            
            while self.running:
                await asyncio.sleep(1)
                
        except asyncio.CancelledError:
            pass
        finally:
            try:
                process.terminate()
                await process.wait()
                for f in os.listdir(os.path.dirname(self.base_path)):
                    if f.startswith(os.path.basename(self.base_path)):
                        try:
                            os.remove(os.path.join(os.path.dirname(self.base_path), f))
                        except:
                            pass
            except:
                pass

    async def cleanup(self):
        print("\nCleaning up WiFi scanner...")
        self.running = False
        await self.stop_monitor_mode() 

class BluetoothMonitor:
    def __init__(self):
        self.devices: Dict[str, dict] = {}
        self.running = True
        self._lock = asyncio.Lock()
        self.oui_lookup = OUILookup()
        self.script_dir = os.path.dirname(os.path.abspath(__file__))
        self.device_classes_file = os.path.join(self.script_dir, 'data', 'device_classes.json')
        self.device_classes = self.load_json_data(self.device_classes_file)

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
                    major_class = (device_class >> 8) & 0x1F
                    minor_class = (device_class >> 2) & 0x3F
                    device_type = self.get_device_class_name(major_class, minor_class)
                    company_name = self.get_company_from_mac(addr)
                    
                    services = await self.get_device_services(addr)
                    
                    if addr in self.devices:
                        self.devices[addr].update({
                            "Last_Seen": current_time,
                            "Device_Name": name if name else addr.replace(":", "-"),
                            "services": services
                        })
                    else:
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

class BLEMonitor:
    def __init__(self):
        self.devices: Dict[str, dict] = {}
        self.running = True
        self.script_dir = os.path.dirname(os.path.abspath(__file__))
        self.scan_logs_dir = os.path.join(self.script_dir, 'logs', 'scan_logs')
        os.makedirs(self.scan_logs_dir, exist_ok=True)
        self.json_path = os.path.join(self.scan_logs_dir, 'bluetooth_scan.json')
        self.company_codes_file = os.path.join(self.script_dir, 'data', 'company_codes.json')
        self.service_uuids_file = os.path.join(self.script_dir, 'data', 'service_uuids.json')
        self.oui_lookup = OUILookup()
        
        self.company_codes = self.load_json_data(self.company_codes_file)
        self.service_uuids = self.load_json_data(self.service_uuids_file)
        
        self.ble_frequencies = {
            37: 2402,
            38: 2426,
            39: 2480,
            **{i: 2402 + (2 * (i-1)) for i in range(1, 37)}
        }

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

    def get_device_name_from_services(self, adv: AdvertisementData) -> Optional[str]:
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
            "manufacturer_info": manufacturer_info,
            "protocol": protocol,
            "services": [self.get_service_name(uuid) for uuid in adv.service_uuids] if adv.service_uuids else []
        }
        
        if device.address not in self.devices:
            self.devices[device.address] = device_info
        else:
            device_info["First_Seen"] = self.devices[device.address]["First_Seen"]
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

    async def cleanup(self):
        self.running = False 

class DeviceMonitor:
    def __init__(self):
        self.wifi_scanner = WiFiMonitor()
        self.ble_scanner = BLEMonitor()
        self.classic_scanner = BluetoothMonitor()
        self.running = True
        self.script_dir = os.path.dirname(os.path.abspath(__file__))
        self.logs_dir = os.path.join(self.script_dir, 'logs')
        self.scan_logs_dir = os.path.join(self.logs_dir, 'scan_logs')
        self.gps_logs_dir = os.path.join(self.logs_dir, 'gps_logs')
        self.gps_data_path = os.path.join(self.gps_logs_dir, 'gps_data.json')
        
        # Create scan_logs directory if it doesn't exist
        os.makedirs(self.scan_logs_dir, exist_ok=True)
        
        self.combined_json_path = os.path.join(self.scan_logs_dir, 'device_data.json')
        self.wifi_base_path = os.path.join(self.scan_logs_dir, 'wifi_scan')
        self.all_devices = {}
        
        # Clean up any existing files at startup
        self.cleanup_files()

    def cleanup_files(self):
        """Clean up all JSON and CSV files created by the script."""
        # List of specific files to remove
        files_to_remove = [
            self.combined_json_path,
            f"{self.combined_json_path}.tmp"
        ]
        
        # Remove any existing wifi scan files (both CSV and CAP files)
        for f in os.listdir(self.scan_logs_dir):
            if f.startswith('wifi_scan-') or f.startswith('wifi_scan.'):
                files_to_remove.append(os.path.join(self.scan_logs_dir, f))
        
        files_deleted = []
        files_failed = []
        
        for file_path in files_to_remove:
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
                    files_deleted.append(os.path.basename(file_path))
            except Exception as e:
                files_failed.append((os.path.basename(file_path), str(e)))
                
        if files_deleted:
            print(f"\nSuccessfully cleaned up {len(files_deleted)} files:")
            for f in files_deleted:
                print(f"  - {f}")
                
        if files_failed:
            print(f"\nFailed to clean up {len(files_failed)} files:")
            for f, error in files_failed:
                print(f"  - {f}: {error}")

    async def load_existing_devices(self):
        try:
            if os.path.exists(self.combined_json_path):
                with open(self.combined_json_path, 'r') as f:
                    data = json.load(f)
                    if "bluetooth" in data and "devices" in data["bluetooth"]:
                        for device in data["bluetooth"]["devices"]:
                            if "BD_ADDR" in device:
                                self.all_devices[device["BD_ADDR"]] = device
                    print(f"Loaded {len(self.all_devices)} existing Bluetooth devices")
        except Exception as e:
            print(f"Error loading existing devices: {e}")

    def get_gps_data(self):
        """Read GPS data from the GPS data file."""
        try:
            if os.path.exists(self.gps_data_path):
                with open(self.gps_data_path, 'r') as f:
                    gps_data = json.load(f)
                    return {
                        "longitude": gps_data.get("longitude"),
                        "latitude": gps_data.get("latitude"),
                        "altitude": gps_data.get("altitude"),
                        "speed": gps_data.get("speed"),
                        "satellites": gps_data.get("satellites")
                    }
        except Exception as e:
            print(f"Error reading GPS data: {e}")
        
        return {
            "longitude": None,
            "latitude": None,
            "altitude": None,
            "speed": None,
            "satellites": None
        }

    async def write_combined_json_data(self):
        current_time = datetime.now()
        
        # Get GPS data
        gps_data = self.get_gps_data()
        
        # Get WiFi data
        wifi_data = await self.wifi_scanner.get_wifi_data()
        
        # Update BLE devices
        for addr, device in self.ble_scanner.devices.items():
            if addr in self.all_devices:
                existing_device = self.all_devices[addr]
                # Only update GPS if signal is stronger and we have valid GPS data
                if (device["RSSI"] is not None and 
                    gps_data["latitude"] is not None and 
                    gps_data["longitude"] is not None and
                    (existing_device.get("RSSI") is None or 
                     device["RSSI"] > existing_device["RSSI"])):
                    existing_device.update({
                        "longitude": gps_data["longitude"],
                        "latitude": gps_data["latitude"],
                        "altitude": gps_data["altitude"],
                        "speed": gps_data["speed"],
                        "satellites": gps_data["satellites"]
                    })
                existing_device.update({
                    "Last_Seen": device["Last_Seen"],
                    "RSSI": device["RSSI"]
                })
            else:
                filtered_device = {k: v for k, v in device.items() 
                                if k not in ['manufacturer_info', 'raw_data', 'country', 'address', 'Channel']}
                # Add GPS data for new devices if available
                if gps_data["latitude"] is not None and gps_data["longitude"] is not None:
                    filtered_device.update({
                        "longitude": gps_data["longitude"],
                        "latitude": gps_data["latitude"],
                        "altitude": gps_data["altitude"],
                        "speed": gps_data["speed"],
                        "satellites": gps_data["satellites"]
                    })
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
                    existing_device = self.all_devices[addr]
                    # Only update GPS if signal is stronger and we have valid GPS data
                    if (device.get("RSSI") is not None and 
                        gps_data["latitude"] is not None and 
                        gps_data["longitude"] is not None and
                        (existing_device.get("RSSI") is None or 
                         device["RSSI"] > existing_device["RSSI"])):
                        existing_device.update({
                            "longitude": gps_data["longitude"],
                            "latitude": gps_data["latitude"],
                            "altitude": gps_data["altitude"],
                            "speed": gps_data["speed"],
                            "satellites": gps_data["satellites"]
                        })
                    existing_device.update({
                        "Last_Seen": device["Last_Seen"],
                        "services": device["services"]
                    })
                    if existing_device.get("Type") != "BT":
                        existing_device["Type"] = "BT"
                else:
                    filtered_device = {k: v for k, v in device.items() 
                                    if k not in ['manufacturer_info', 'raw_data', 'country', 'address', 'Channel']}
                    filtered_device["Type"] = "BT"
                    # Add GPS data for new devices if available
                    if gps_data["latitude"] is not None and gps_data["longitude"] is not None:
                        filtered_device.update({
                            "longitude": gps_data["longitude"],
                            "latitude": gps_data["latitude"],
                            "altitude": gps_data["altitude"],
                            "speed": gps_data["speed"],
                            "satellites": gps_data["satellites"]
                        })
                    self.all_devices[addr] = filtered_device

        # Prepare all devices list with preserved GPS data
        all_devices = []
        
        # Add Bluetooth devices with their preserved GPS data
        for device in self.all_devices.values():
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
                "Last_Seen": device.get("Last_Seen", ""),
                # Preserve existing GPS data
                "longitude": device.get("longitude"),
                "latitude": device.get("latitude"),
                "altitude": device.get("altitude"),
                "speed": device.get("speed"),
                "satellites": device.get("satellites")
            }
            all_devices.append(flattened_device)

        # Add WiFi devices
        wifi_devices = {}  # Keep track of previously seen WiFi devices
        for network in wifi_data["networks"]:
            mac = network.get("MAC", "")
            if mac in wifi_devices:
                existing_device = wifi_devices[mac]
                # Only update GPS if signal is stronger and we have valid GPS data
                if (network.get("RSSI") is not None and 
                    gps_data["latitude"] is not None and 
                    gps_data["longitude"] is not None and
                    (existing_device.get("RSSI") is None or 
                     float(network["RSSI"]) > float(existing_device["RSSI"]))):
                    existing_device.update({
                        "longitude": gps_data["longitude"],
                        "latitude": gps_data["latitude"],
                        "altitude": gps_data["altitude"],
                        "speed": gps_data["speed"],
                        "satellites": gps_data["satellites"]
                    })
            else:
                wifi_device = {
                    "First_Seen": network.get("FirstSeen", ""),
                    "Last_Seen": network.get("LastSeen", ""),
                    "MAC": mac,
                    "Device_Name": network.get("SSID", ""),
                    "Device_Type": network.get("device_type", "Unknown"),
                    "Type": "WiFi",
                    "company_name": network.get("company_name", "Unknown"),
                    "RSSI": network.get("RSSI"),
                    "Channel": network.get("Channel"),
                    "protocol": network.get("protocol", ""),
                    "capabilities": network.get("capabilities", {}),
                    "AuthMode": network.get("AuthMode", ""),
                }
                # Add GPS data for new devices if available
                if gps_data["latitude"] is not None and gps_data["longitude"] is not None:
                    wifi_device.update({
                        "longitude": gps_data["longitude"],
                        "latitude": gps_data["latitude"],
                        "altitude": gps_data["altitude"],
                        "speed": gps_data["speed"],
                        "satellites": gps_data["satellites"]
                    })
                wifi_devices[mac] = wifi_device
                all_devices.append(wifi_device)

        # Add WiFi client devices with the same GPS preservation logic
        wifi_clients = {}
        for station in wifi_data["stations"]:
            mac = station.get("MAC", "")
            if mac in wifi_clients:
                existing_client = wifi_clients[mac]
                # Only update GPS if signal is stronger and we have valid GPS data
                if (station.get("RSSI") is not None and 
                    gps_data["latitude"] is not None and 
                    gps_data["longitude"] is not None and
                    (existing_client.get("RSSI") is None or 
                     float(station["RSSI"]) > float(existing_client["RSSI"]))):
                    existing_client.update({
                        "longitude": gps_data["longitude"],
                        "latitude": gps_data["latitude"],
                        "altitude": gps_data["altitude"],
                        "speed": gps_data["speed"],
                        "satellites": gps_data["satellites"]
                    })
            else:
                station_device = {
                    "First_Seen": station.get("FirstSeen", ""),
                    "Last_Seen": station.get("LastSeen", ""),
                    "MAC": mac,
                    "Device_Name": station.get("Probed", ""),
                    "Device_Type": station.get("device_type", "Unknown"),
                    "Type": "WiFi Client",
                    "company_name": station.get("company_name", "Unknown"),
                    "RSSI": station.get("RSSI"),
                    "BSSID": station.get("BSSID", ""),
                    "probe_history": station.get("probe_history", []),
                }
                # Add GPS data for new devices if available
                if gps_data["latitude"] is not None and gps_data["longitude"] is not None:
                    station_device.update({
                        "longitude": gps_data["longitude"],
                        "latitude": gps_data["latitude"],
                        "altitude": gps_data["altitude"],
                        "speed": gps_data["speed"],
                        "satellites": gps_data["satellites"]
                    })
                wifi_clients[mac] = station_device
                all_devices.append(station_device)

        # Combine all data with counts at top level
        data = {
            "scan_time": current_time.strftime("%Y-%m-%d %H:%M:%S"),
            "total_devices": len(all_devices),
            "ble_devices": len(self.ble_scanner.devices),
            "bt_devices": len(self.classic_scanner.devices),
            "wifi_networks": len(wifi_data["networks"]),
            "wifi_clients": len(wifi_data["stations"]),
            "devices": all_devices
        }
        
        temp_path = f"{self.combined_json_path}.tmp"
        try:
            with open(temp_path, 'w') as f:
                json.dump(data, f, indent=2)
            os.replace(temp_path, self.combined_json_path)
        except Exception as e:
            print(f"Error writing combined JSON file: {e}")
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except:
                    pass

    async def run_classic_scanner(self):
        while self.running:
            try:
                await self.classic_scanner.scan_devices()
                await asyncio.sleep(0.1)
            except Exception as e:
                print(f"Error in classic scanner: {e}")
                await asyncio.sleep(0.1)

    async def periodic_write(self):
        while self.running:
            try:
                await self.write_combined_json_data()
                await asyncio.sleep(1)
            except Exception as e:
                print(f"Error in periodic write: {e}")
                await asyncio.sleep(1)

    async def run_scanners(self):
        try:
            # Clean up any existing files before starting
            self.cleanup_files()
            
            interface = 'wlan1'
            await self.wifi_scanner.start_monitor_mode(interface)
            wifi_task = asyncio.create_task(self.wifi_scanner.scan_networks())
            
            await self.load_existing_devices()
            ble_task = asyncio.create_task(self.ble_scanner.run_scanner())
            classic_task = asyncio.create_task(self.run_classic_scanner())
            write_task = asyncio.create_task(self.periodic_write())
            
            await asyncio.gather(wifi_task, ble_task, classic_task, write_task)
        except Exception:
            await self.cleanup()

    async def cleanup(self):
        print("\nCleaning up and stopping scanners...")
        self.running = False
        self.wifi_scanner.running = False
        self.ble_scanner.running = False
        self.classic_scanner.running = False
        
        try:
            # Give scanners time to stop gracefully
            await asyncio.sleep(1)
            
            await self.wifi_scanner.cleanup()
            await self.ble_scanner.cleanup()
            
            # Final cleanup of all generated files
            self.cleanup_files()
            print("Cleanup completed.")
        except Exception as e:
            print(f"Error during cleanup: {e}")
            # Attempt one final file cleanup even if scanner cleanup failed
            try:
                self.cleanup_files()
            except Exception as cleanup_error:
                print(f"Final cleanup attempt failed: {cleanup_error}")

async def main():
    monitor = DeviceMonitor()
    
    def signal_handler(signum, frame):
        print("\nReceived signal to terminate...")
        asyncio.create_task(monitor.cleanup())
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        await monitor.run_scanners()
    except KeyboardInterrupt:
        await monitor.cleanup()
    except Exception as e:
        print(f"Error in main: {e}")
        await monitor.cleanup()
    finally:
        # Ensure cleanup happens even if other cleanup attempts fail
        monitor.cleanup_files()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nKeyboard interrupt received, cleaning up...")
    except Exception as e:
        print(f"Fatal error: {e}")
    finally:
        # Create a new monitor instance just for final cleanup
        # in case the main instance was not properly initialized
        try:
            monitor = DeviceMonitor()
            monitor.cleanup_files()
        except Exception as e:
            print(f"Final cleanup attempt failed: {e}") 