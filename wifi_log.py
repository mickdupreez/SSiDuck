#!/usr/bin/env python3

import subprocess
import signal
import sys
import time
import os
import json
from datetime import datetime

class WiFiScanner:
    def __init__(self):
        self.monitor_interface = None
        self.original_interface = 'wlan1'
        self.running = True
        script_dir = os.path.dirname(os.path.abspath(__file__))
        self.base_path = os.path.join(script_dir, 'wifi_scan')
        self.json_path = os.path.join(script_dir, 'wifi_scan.json')
        print("[DEBUG] Scanner initialized")

    def format_auth_mode(self, privacy, cipher, authentication):
        components = []
        if privacy:
            components.append(privacy)
        if authentication:
            components.append(authentication)
        if cipher:
            components.append(cipher)
        return f"[{' '.join(components)}]"

    def write_json_data(self, networks, stations):
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

    def get_wireless_interfaces(self):
        try:
            print("[DEBUG] Checking wireless interfaces...")
            result = subprocess.run(['sudo', 'iwconfig'], capture_output=True, text=True)
            return ['wlan1']
        except Exception as e:
            print(f"Error getting wireless interfaces: {e}")
            sys.exit(1)

    def manage_network_manager(self, interface, action='stop'):
        try:
            if action == 'stop':
                subprocess.run(['sudo', 'nmcli', 'device', 'set', interface, 'managed', 'no'], capture_output=True)
            else:
                subprocess.run(['sudo', 'nmcli', 'device', 'set', interface, 'managed', 'yes'], capture_output=True)
        except Exception as e:
            print(f"Error managing NetworkManager: {e}")

    def start_monitor_mode(self, interface):
        try:
            print(f"[DEBUG] Starting monitor mode on {interface}")
            self.manage_network_manager(interface, 'stop')
            subprocess.run(['sudo', 'airmon-ng', 'start', interface], capture_output=True)
            self.monitor_interface = interface + 'mon'
            self.original_interface = interface
            print(f"Started monitor mode on {self.monitor_interface}")
        except Exception as e:
            print(f"Error starting monitor mode: {e}")
            sys.exit(1)

    def stop_monitor_mode(self):
        if self.monitor_interface:
            try:
                print(f"[DEBUG] Stopping monitor mode on {self.monitor_interface}")
                subprocess.run(['sudo', 'airmon-ng', 'stop', self.monitor_interface], capture_output=True)
                self.manage_network_manager(self.original_interface, 'start')
                print(f"Stopped monitor mode on {self.monitor_interface}")
            except Exception as e:
                print(f"Error stopping monitor mode: {e}")

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
                    return {
                        "FirstSeen": first_seen,
                        "LastSeen": last_seen,
                        "MAC": bssid,
                        "RSSI": power,
                        "Channel": channel,
                        "Speed": speed,
                        "SSID": essid if essid else "<hidden>",
                        "AuthMode": self.format_auth_mode(privacy, cipher, authentication),
                        "Beacons": beacons
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
                    return {
                        "MAC": mac,
                        "FirstSeen": first_seen,
                        "LastSeen": last_seen,
                        "RSSI": power,
                        "BSSID": "not associated" if bssid == "(not associated)" else bssid,
                        "Probed": probed
                    }
            return None
        except Exception as e:
            return None

    def scan_networks(self):
        try:
            print(f"[DEBUG] Starting airodump-ng on interface {self.monitor_interface}")
            
            process = subprocess.Popen(
                f"sudo airodump-ng {self.monitor_interface} --channel 1,2,3,4,5,6,7,8,9,10,11,12,13,36,40,44,48,149,153,157,161 --output-format csv -w {self.base_path} --write-interval 1 >/dev/null 2>&1",
                shell=True,
                preexec_fn=os.setsid
            )
            
            print("\nScanning and logging networks to JSON file...")
            print("Press Ctrl+C to stop\n")
            
            csv_file_path = f"{self.base_path}-01.csv"
            
            while self.running:
                try:
                    if os.path.exists(csv_file_path):
                        with open(csv_file_path, 'r') as f:
                            lines = f.readlines()
                            
                            networks_info = []
                            stations_info = []
                            
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
                            
                            self.write_json_data(networks_info, stations_info)
                                
                except Exception as e:
                    pass
                
                time.sleep(1)
                
        except KeyboardInterrupt:
            print("\nStopping scan...")
        finally:
            print("[DEBUG] Cleaning up...")
            try:
                os.killpg(process.pid, signal.SIGTERM)
                for f in os.listdir(os.path.dirname(self.base_path)):
                    if f.startswith(os.path.basename(self.base_path)):
                        try:
                            os.remove(os.path.join(os.path.dirname(self.base_path), f))
                        except:
                            pass
            except:
                pass

    def cleanup(self, signum, frame):
        print("\nCleaning up...")
        self.running = False
        self.stop_monitor_mode()
        sys.exit(0)

def main():
    scanner = WiFiScanner()
    interface = 'wlan1'
    print(f"Using wireless interface: {interface}")
    signal.signal(signal.SIGINT, scanner.cleanup)
    scanner.start_monitor_mode(interface)
    scanner.scan_networks()

if __name__ == "__main__":
    main() 