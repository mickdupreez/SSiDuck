#!/usr/bin/env python3

import asyncio
import json
import os
import signal
import sys
import time
from datetime import datetime
from typing import Dict, Optional, List
import subprocess
import re

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

class WiFiMonitor:
    def __init__(self):
        self.monitor_interface = None
        self.original_interface = 'wlan1'
        self.running = True
        script_dir = os.path.dirname(os.path.abspath(__file__))
        self.scan_logs_dir = os.path.join(script_dir, 'logs', 'scan_logs')
        self.wifi_logs_dir = os.path.join(self.scan_logs_dir, 'wifi')
        self.gps_file = os.path.join(script_dir, 'logs', 'scan_logs', 'gps', 'gps_scan.json')
        os.makedirs(self.wifi_logs_dir, exist_ok=True)
        self.base_path = os.path.join(self.wifi_logs_dir, 'wifi_scan')
        self.json_path = os.path.join(self.wifi_logs_dir, 'wifi_scan.json')
        self.oui_lookup = OUILookup()
        
        # Load existing devices from JSON file
        self.devices = self.load_existing_devices()
        
        # Clean up any existing files at startup
        self.cleanup_files()
        
        # Channel to frequency mappings
        self.wifi_frequencies = {
            # 2.4 GHz band
            1: 2412, 2: 2417, 3: 2422, 4: 2427, 5: 2432,
            6: 2437, 7: 2442, 8: 2447, 9: 2452, 10: 2457,
            11: 2462, 12: 2467, 13: 2472, 14: 2484,
            # 5 GHz band
            36: 5180, 40: 5200, 44: 5220, 48: 5240,
            52: 5260, 56: 5280, 60: 5300, 64: 5320,
            100: 5500, 104: 5520, 108: 5540, 112: 5560,
            116: 5580, 120: 5600, 124: 5620, 128: 5640,
            132: 5660, 136: 5680, 140: 5700, 144: 5720,
            149: 5745, 153: 5765, 157: 5785, 161: 5805,
            165: 5825
        }
        
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

    def load_existing_devices(self) -> Dict:
        """Load existing devices from the JSON file."""
        devices = {}
        try:
            if os.path.exists(self.json_path):
                with open(self.json_path, 'r') as f:
                    data = json.load(f)
                    # Convert list of networks and stations to dict keyed by MAC
                    for network in data.get('networks', []):
                        mac = network.get('MAC')
                        if mac:
                            devices[mac] = network
                    for station in data.get('stations', []):
                        mac = station.get('MAC')
                        if mac:
                            devices[mac] = station
        except Exception as e:
            print(f"Error loading existing devices: {e}")
        return devices

    def should_update_device(self, mac: str, new_rssi: int) -> bool:
        """Determine if device should be updated based on RSSI."""
        if mac not in self.devices:
            return True
        
        try:
            current_rssi = int(self.devices[mac].get('RSSI', '-100'))
            new_rssi = int(new_rssi)
            return new_rssi > current_rssi  # Higher RSSI means stronger signal
        except (ValueError, TypeError):
            return True

    def format_auth_mode(self, privacy, cipher, authentication):
        components = []
        if privacy:
            components.append(privacy)
        if authentication:
            components.append(authentication)
        if cipher:
            components.append(cipher)
        return f"[{' '.join(components)}]"

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

    def get_frequency(self, channel):
        """Convert WiFi channel to frequency in MHz."""
        try:
            channel = int(channel)
            return self.wifi_frequencies.get(channel)
        except (ValueError, TypeError):
            return None

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
                    frequency = self.get_frequency(channel)
                    
                    # Only process if RSSI is stronger or device is new
                    if self.should_update_device(bssid, power):
                        # Get current GPS data
                        gps_data = self.get_current_gps()
                        
                        device_info = {
                            "FirstSeen": self.devices.get(bssid, {}).get("FirstSeen", first_seen),
                            "LastSeen": last_seen,
                            "MAC": bssid,
                            "Type": "AP",
                            "RSSI": power,
                            "Channel": channel,
                            "Frequency": frequency,
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
                            },
                            "gps_data": gps_data
                        }
                        
                        self.devices[bssid] = device_info
                        return device_info
            return None
        except Exception as e:
            print(f"Error parsing network info: {e}")
            return None

    def parse_csv_station_info(self, line):
        try:
            parts = line.strip().split(',')
            if len(parts) >= 6:
                mac = parts[0].strip()
                first_seen = parts[1].strip()
                last_seen = parts[2].strip()
                power = parts[3].strip()
                channel = parts[4].strip()
                bssid = parts[5].strip()
                probed = parts[6].strip().replace('\u2019', "'") if len(parts) > 6 else ""
                
                if mac:
                    # Only process if RSSI is stronger or device is new
                    if self.should_update_device(mac, power):
                        company_info = self.oui_lookup.lookup_mac(mac)
                        device_type = self.detect_device_type(probed, "", mac)
                        
                        # Get current GPS data
                        gps_data = self.get_current_gps()
                        
                        # Handle device name according to rules
                        device_name = probed
                        if not device_name and company_info["company"] != "Unknown":
                            device_name = company_info["company"]
                        # Limit to first 2 words if longer
                        if device_name and len(device_name.split()) > 2:
                            device_name = " ".join(device_name.split()[:2])
                        
                        device_info = {
                            "MAC": mac,
                            "Type": "ST",
                            "FirstSeen": self.devices.get(mac, {}).get("FirstSeen", first_seen),
                            "LastSeen": last_seen,
                            "RSSI": power,
                            "Channel": channel,
                            "BSSID": "not associated" if bssid == "(not associated)" else bssid,
                            "Probed": device_name,
                            "company_name": company_info["company"],
                            "device_type": device_type,
                            "probe_history": [probed] if probed else [],
                            "gps_data": gps_data
                        }
                        
                        self.devices[mac] = device_info
                        return device_info
            return None
        except Exception as e:
            print(f"Error parsing station info: {e}")
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
            cmd = f"sudo airodump-ng {self.monitor_interface} --band abg --background 1 --output-format csv -w {self.base_path} --write-interval 1 >/dev/null 2>&1" 
            
            process = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                stdin=asyncio.subprocess.DEVNULL
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

    def cleanup_files(self):
        """Clean up all JSON and CSV files created by the script."""
        # List of specific files to remove
        files_to_remove = [
            self.json_path,
            f"{self.json_path}.tmp"
        ]
        
        # Remove any existing wifi scan files
        for f in os.listdir(os.path.dirname(self.base_path)):
            if f.startswith('wifi_scan-') or f.startswith('wifi_scan.'):
                files_to_remove.append(os.path.join(os.path.dirname(self.base_path), f))
        
        for file_path in files_to_remove:
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
            except Exception as e:
                print(f"Error removing file {file_path}: {e}")

    async def write_json_data(self, data):
        """Write current scan data to JSON file."""
        # Convert devices dict to lists for networks and stations
        networks = []
        stations = []
        
        for device in self.devices.values():
            if "SSID" in device:  # This is a network
                networks.append(device)
            else:  # This is a station
                stations.append(device)
        
        json_data = {
            "scan_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "total_networks": len(networks),
            "total_stations": len(stations),
            "networks": networks,
            "stations": stations
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
        await self.stop_monitor_mode()
        self.cleanup_files()

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

async def main():
    """Main entry point for WiFi scanning."""
    wifi_monitor = WiFiMonitor()
    
    def signal_handler(signum, frame):
        wifi_monitor.running = False
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        # Start monitor mode on wlan1
        await wifi_monitor.start_monitor_mode('wlan1')
        
        print("Starting WiFi scan... Press Ctrl+C to stop.")
        scan_task = asyncio.create_task(wifi_monitor.scan_networks())
        
        while wifi_monitor.running:
            # Get and display current scan data
            data = await wifi_monitor.get_wifi_data()
            
            # Write data to JSON file
            await wifi_monitor.write_json_data(data)
            
            await asyncio.sleep(5)
            
    except KeyboardInterrupt:
        print("\nWiFi scan stopped.")
    except Exception:
        sys.exit(1)
    finally:
        await wifi_monitor.cleanup()
        # Ensure the scan task is properly cancelled
        if 'scan_task' in locals():
            scan_task.cancel()
            try:
                await scan_task
            except asyncio.CancelledError:
                pass

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception:
        sys.exit(1) 