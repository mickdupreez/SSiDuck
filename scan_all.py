#!/usr/bin/env python3

import asyncio
import json
import os
import signal
import sys
from datetime import datetime
from typing import Dict, List, Optional
import subprocess

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

    async def wait_for_gps_file(self) -> bool:
        """Wait for GPS file to be created and contain valid data."""
        print("Waiting for GPS data...")
        retry_count = 0
        max_retries = 30  # Wait up to 30 seconds
        
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
        except Exception as e:
            print(f"Error starting {script_name}: {e}")
            return None

    def get_device_counts(self) -> Dict[str, int]:
        """Get device counts from all scanner JSON files."""
        counts = {
            "wifi_networks": 0,
            "wifi_stations": 0,
            "bluetooth": 0,
            "ble": 0,
            "total": 0
        }
        
        try:
            # Get WiFi counts
            if os.path.exists(self.wifi_file):
                with open(self.wifi_file, 'r') as f:
                    data = json.load(f)
                    counts["wifi_networks"] = data.get("total_networks", 0)
                    counts["wifi_stations"] = data.get("total_stations", 0)
            
            # Get Bluetooth counts
            if os.path.exists(self.bt_file):
                with open(self.bt_file, 'r') as f:
                    data = json.load(f)
                    counts["bluetooth"] = data.get("total_devices", 0)
            
            # Get BLE counts
            if os.path.exists(self.ble_file):
                with open(self.ble_file, 'r') as f:
                    data = json.load(f)
                    counts["ble"] = data.get("total_devices", 0)
            
            # Calculate total
            counts["total"] = (
                counts["wifi_networks"] +
                counts["wifi_stations"] +
                counts["bluetooth"] +
                counts["ble"]
            )
            
        except Exception as e:
            print(f"Error getting device counts: {e}")
        
        return counts

    def clear_screen(self):
        """Clear the terminal screen and move cursor to home position."""
        print('\033[2J\033[H', end='')

    def display_counts(self, counts: Dict[str, int]):
        """Display device counts in a formatted way."""
        self.clear_screen()
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Build the entire output string before printing
        output = []
        output.append("=" * 50)
        output.append(f"Scanner Status - {current_time}")
        output.append("=" * 50)
        output.append(f"WiFi Networks    : {counts['wifi_networks']}")
        output.append(f"WiFi Stations    : {counts['wifi_stations']}")
        output.append(f"Bluetooth Devices: {counts['bluetooth']}")
        output.append(f"BLE Devices      : {counts['ble']}")
        output.append("-" * 50)
        output.append(f"Total Devices    : {counts['total']}")
        output.append("=" * 50)
        output.append("\nPress Ctrl+C to stop scanning...")
        
        # Print everything at once
        print('\n'.join(output))
        
        # Force flush the output
        sys.stdout.flush()

    async def update_display(self):
        """Periodically update the display with current counts."""
        while self.running:
            try:
                counts = self.get_device_counts()
                self.display_counts(counts)
            except Exception as e:
                print(f"Error updating display: {e}")
            await asyncio.sleep(1)

    def cleanup(self):
        """Clean up processes on exit."""
        self.running = False
        for name, process in self.processes.items():
            try:
                process.terminate()
                process.wait(timeout=5)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass

    async def run(self):
        """Main run method."""
        try:
            # Start GPS scanner first
            print("Starting GPS scanner...")
            self.processes['gps'] = self.start_script('scan_gps.py')
            
            # Wait for GPS file
            if not await self.wait_for_gps_file():
                print("Failed to get GPS data. Exiting...")
                self.cleanup()
                return
            
            # Start other scanners
            print("Starting WiFi, Bluetooth, and BLE scanners...")
            self.processes['wifi'] = self.start_script('scan_wifi.py')
            self.processes['bt'] = self.start_script('scan_bt.py')
            self.processes['ble'] = self.start_script('scan_ble.py')
            
            # Start display update loop
            await self.update_display()
            
        except asyncio.CancelledError:
            pass
        finally:
            self.cleanup()

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
        print("\nStopping all scanners...")
    finally:
        manager.cleanup()

if __name__ == "__main__":
    main() 