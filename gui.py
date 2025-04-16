#!/usr/bin/env python3

import customtkinter as ctk
import ttkbootstrap as ttk
from ttkbootstrap.constants import *
from PIL import Image, ImageTk
import json
from datetime import datetime
import os
import subprocess
import signal
import shutil

# Set theme and appearance
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

class InfoBar(ctk.CTkFrame):
    def __init__(self, master, **kwargs):
        # Create outer container frame
        self.container = ctk.CTkFrame(master, fg_color="#2d2d2d", corner_radius=15)
        self.container.pack(fill="x", padx=10, pady=5)
        
        # Initialize inner frame
        super().__init__(self.container, **kwargs)
        self.pack(fill="x", padx=10, pady=5)
        
        # Styling
        self.configure(fg_color="#1a1a1a", height=30)
        
        # Create labels with small font
        self.font = ("Roboto", 10)
        self.location_label = ctk.CTkLabel(self, text="", font=self.font, text_color="#00ffff")
        self.location_label.pack(side="left", padx=5)
        
        self.weather_label = ctk.CTkLabel(self, text="", font=self.font, text_color="#00ff00")
        self.weather_label.pack(side="left", padx=5)
        
        self.stats_label = ctk.CTkLabel(self, text="", font=self.font, text_color="#ff00ff")
        self.stats_label.pack(side="left", padx=5)
        
        # Start/Stop button
        self.wardrive_process = None
        self.start_stop_btn = ctk.CTkButton(
            self,
            text="Start Scan",
            font=self.font,
            width=80,
            height=20,
            command=self.toggle_wardrive
        )
        self.start_stop_btn.pack(side="right", padx=5)
        
    def reset_labels(self):
        """Reset all labels to their default empty state"""
        self.location_label.configure(text="")
        self.weather_label.configure(text="")
        self.stats_label.configure(text="")
        
    def toggle_wardrive(self):
        if self.wardrive_process is None:
            # Start wardrive.py
            try:
                # Start process in its own process group
                self.wardrive_process = subprocess.Popen(
                    ["python3", "wardrive.py"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    preexec_fn=os.setsid  # Create new process group
                )
                self.start_stop_btn.configure(text="Stop Scan")
            except Exception as e:
                print(f"Error starting wardrive.py: {e}")
        else:
            # Stop wardrive.py and all its child processes
            try:
                # Send SIGTERM to the entire process group
                os.killpg(os.getpgid(self.wardrive_process.pid), signal.SIGTERM)
                
                # Wait for the main process to terminate
                try:
                    self.wardrive_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    # If timeout expires, send SIGKILL to the process group
                    os.killpg(os.getpgid(self.wardrive_process.pid), signal.SIGKILL)
                    self.wardrive_process.wait(timeout=2)
                
            except ProcessLookupError:
                # Process already terminated
                pass
            except Exception as e:
                print(f"Error stopping wardrive.py: {e}")
                # Ensure process is killed as last resort
                try:
                    os.killpg(os.getpgid(self.wardrive_process.pid), signal.SIGKILL)
                except:
                    pass
            
            self.wardrive_process = None
            self.start_stop_btn.configure(text="Start Scan")
            
            # Reset UI and clean up files
            self.reset_labels()
            self.master.cleanup_scan_files()
    
    def update_info(self):
        try:
            with open("logs/scan_logs/gps/gps_scan.json", "r") as f:
                data = json.load(f)
                
                # Format location info - use full address
                location = data.get("location_info", {})
                address = location.get("address", "No Address")  # Use full address
                self.location_label.configure(text=f"{address}")
                
                # Format weather info
                weather = data.get("weather_info", {})
                temp = weather.get("temperature", "N/A")
                humidity = weather.get("humidity", "N/A")
                conditions = weather.get("conditions", "N/A")
                self.weather_label.configure(text=f"{temp}°C | {humidity}% | {conditions}")
                
                # Format movement stats
                speed = data.get("speed", 0)
                distance = data.get("distance_traveled", 0)
                self.stats_label.configure(text=f"{speed:.1f} km/h | {distance:.2f} km")
                
        except Exception as e:
            self.location_label.configure(text="No GPS Data")
            self.weather_label.configure(text="")
            self.stats_label.configure(text="")

class CurrentScanFrame(ctk.CTkFrame):
    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        
        # Styling
        self.configure(fg_color="#2d2d2d", corner_radius=15)
        
        # Create container for device stats
        self.stats_container = ctk.CTkFrame(self, fg_color="transparent")
        self.stats_container.pack(pady=10, padx=15, fill="x")
        
        # Create individual stat labels with icons and colors
        self.stats = {
            "total": {"icon": "", "color": "#ffffff", "text": "Total"},
            "ble": {"icon": "", "color": "#00ff99", "text": "BLE"},
            "bt": {"icon": "", "color": "#3498db", "text": "BT"},
            "ap": {"icon": "", "color": "#e74c3c", "text": "AP"},
            "st": {"icon": "", "color": "#f1c40f", "text": "ST"}
        }
        
        # Configure grid columns with equal weight
        for i in range(5):
            self.stats_container.grid_columnconfigure(i, weight=1)
        
        # Create labels for each stat
        self.stat_labels = {}
        for i, (key, info) in enumerate(self.stats.items()):
            frame = ctk.CTkFrame(self.stats_container, fg_color="#1a1a1a", corner_radius=10)
            frame.grid(row=0, column=i, padx=5, sticky="ew")
            
            # Title with icon
            title = ctk.CTkLabel(
                frame,
                text=f"{info['icon']} {info['text']}",
                font=("Roboto", 12),
                text_color=info['color']
            )
            title.pack(pady=(5,0))
            
            # Value
            value = ctk.CTkLabel(
                frame,
                text="0",
                font=("Roboto", 16, "bold"),
                text_color=info['color']
            )
            value.pack(pady=(0,5))
            
            self.stat_labels[key] = value
    
    def update_counts(self, wardrive_data):
        """Update device counts from wardrive.json data"""
        try:
            summary = wardrive_data.get("summary", {})
            counts = {
                "total": summary.get("TOTAL_DEVICES", 0),
                "ble": summary.get("BLE_DEVICES", 0),
                "bt": summary.get("BT_DEVICES", 0),
                "ap": summary.get("ACCESS_POINTS", 0),
                "st": summary.get("STATIONS", 0)
            }
            
            for key, value in counts.items():
                self.stat_labels[key].configure(text=str(value))
        except Exception:
            self.reset_stats()
    
    def reset_stats(self):
        """Reset stats to zero"""
        for label in self.stat_labels.values():
            label.configure(text="0")

class DeviceFeedFrame(ctk.CTkFrame):
    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        
        # Styling
        self.configure(fg_color="#1a1a1a", corner_radius=10)
        
        # Configure grid layout
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        
        # Title
        self.title = ctk.CTkLabel(
            self,
            text="Found Devices",
            font=("Roboto", 14, "bold"),
            text_color="#00ffff"
        )
        self.title.grid(row=0, column=0, pady=5, sticky="ew")
        
        # Create scrollable text widget
        self.text_widget = ctk.CTkTextbox(
            self,
            font=("Roboto", 12),
            fg_color="#2d2d2d",
            text_color="#ffffff"
        )
        self.text_widget.grid(row=1, column=0, padx=10, pady=(0, 10), sticky="nsew")
        
        # Initialize device tracking
        self.known_devices = set()
        self.last_file_size = 0
        
        # Start periodic updates
        self.update_feed()
    
    def update_feed(self):
        try:
            csv_file = "logs/scan_logs/wardrive/wardrive.csv"
            if not os.path.exists(csv_file):
                self.after(1000, self.update_feed)
                return
                
            current_size = os.path.getsize(csv_file)
            if current_size == self.last_file_size:
                self.after(1000, self.update_feed)
                return
                
            self.last_file_size = current_size
            
            with open(csv_file, 'r') as f:
                # Skip header lines
                next(f)  # Skip WigleWifi header
                next(f)  # Skip column headers
                
                new_devices = []
                for line in f:
                    try:
                        parts = line.strip().split(',')
                        if len(parts) >= 2:
                            mac = parts[0]
                            ssid = parts[1]
                            device_type = parts[-1] if len(parts) > 10 else "Unknown"
                            
                            device_info = f"{ssid} ({mac}) - {device_type}"
                            if device_info not in self.known_devices:
                                new_devices.append(device_info)
                                self.known_devices.add(device_info)
                    except Exception:
                        continue
                
                if new_devices:
                    self.text_widget.configure(state="normal")
                    for device in new_devices:
                        self.text_widget.insert("end", device + "\n")
                    self.text_widget.configure(state="disabled")
                    self.text_widget.see("end")  # Auto-scroll to latest entries
                
        except Exception as e:
            print(f"Error updating device feed: {e}")
        
        self.after(1000, self.update_feed)

class StatisticsFrame(ctk.CTkFrame):
    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        
        # Styling
        self.configure(fg_color="#2d2d2d", corner_radius=15)
        
        # Configure grid layout
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)
        
        # Left side container for stats (2x3 grid)
        self.stats_container = ctk.CTkFrame(self, fg_color="transparent")
        self.stats_container.grid(row=0, column=0, sticky="nsew", padx=10, pady=5)
        
        # Right side for device feed
        self.device_feed = DeviceFeedFrame(self)
        self.device_feed.grid(row=0, column=1, sticky="nsew", padx=10, pady=5)
        
        # Initialize stats labels
        self.stats_labels = {}
        self.create_stat_labels()
        
    def create_stat_labels(self):
        stats_info = [
            ("Username", "#9900ff"),
            ("Global Rank", "#00ff00"),
            ("Month Rank", "#00ffff"),
            ("Previous Month", "#ff00ff"),
            ("Discovered WiFi", "#ffff00"),
            ("Discovered BT", "#00ff00")
        ]
        
        # Configure grid for stats container
        for i in range(3):
            self.stats_container.grid_rowconfigure(i, weight=1)
        self.stats_container.grid_columnconfigure(0, weight=1)
        self.stats_container.grid_columnconfigure(1, weight=1)
        
        for i, (label, color) in enumerate(stats_info):
            frame = ctk.CTkFrame(self.stats_container, fg_color="#1a1a1a", corner_radius=10)
            frame.grid(row=i//2, column=i%2, padx=5, pady=5, sticky="nsew")
            
            title = ctk.CTkLabel(frame, text=label, font=("Roboto", 12), text_color=color)
            title.pack(pady=2)
            
            value = ctk.CTkLabel(frame, text="--", font=("Roboto", 14, "bold"), text_color="white")
            value.pack(pady=2)
            
            self.stats_labels[label] = value

class StatsViewer(ctk.CTk):
    def __init__(self):
        super().__init__()
        
        # Window setup
        self.title("SSiDuck Stats Viewer")
        self.geometry("800x480")
        self.configure(fg_color="#1a1a1a")
        
        # Info bar at top (container is now handled by InfoBar class)
        self.info_bar = InfoBar(self)
        
        # Current scan frame
        self.current_scan_frame = CurrentScanFrame(self)
        self.current_scan_frame.pack(fill="x", padx=10, pady=5)
        
        # Statistics frame
        self.stats_frame = StatisticsFrame(self)
        self.stats_frame.pack(fill="x", padx=10, pady=5)
        
        # Load initial data
        self.load_data()
        
        # Setup periodic updates
        self.after(5000, self.update_data)
    
    def cleanup_scan_files(self):
        """Clean up scan-related files"""
        files_to_delete = [
            "logs/scan_logs/ble/ble_scan.json",
            "logs/scan_logs/gps/gps_scan.json",
            "logs/scan_logs/bluetooth/bt_scan.json",
            "logs/scan_logs/wardrive/wardrive.json",
            "logs/scan_logs/wardrive/wardrive.csv",
            "logs/scan_logs/wifi/wifi_scan-01.csv",
            "logs/scan_logs/wifi/wifi_scan.json"
        ]
        
        for path in files_to_delete:
            try:
                if os.path.exists(path):
                    os.remove(path)
                    print(f"Deleted {path}")
            except Exception as e:
                print(f"Error cleaning up {path}: {e}")
        
        # Reset UI components
        self.stats_frame.device_feed.text_widget.configure(state="normal")
        self.stats_frame.device_feed.text_widget.delete("1.0", "end")
        self.stats_frame.device_feed.text_widget.configure(state="disabled")
        self.stats_frame.device_feed.known_devices.clear()
        self.current_scan_frame.reset_stats()
    
    def load_data(self):
        try:
            # Load stats.json for WiGLE stats
            with open("logs/stats/stats.json", "r") as f:
                data = json.load(f)
            
            # Update WiGLE stats
            wigle_stats = data.get("wigle_stats", {})
            self.stats_frame.stats_labels["Username"].configure(text=wigle_stats.get("userName", "--"))
            self.stats_frame.stats_labels["Global Rank"].configure(text=str(wigle_stats.get("rank", "--")))
            self.stats_frame.stats_labels["Month Rank"].configure(text=str(wigle_stats.get("monthRank", "--")))
            self.stats_frame.stats_labels["Previous Month"].configure(text=str(wigle_stats.get("prevMonthRank", "--")))
            self.stats_frame.stats_labels["Discovered WiFi"].configure(text=str(wigle_stats.get("discoveredWiFiGPS", "--")))
            self.stats_frame.stats_labels["Discovered BT"].configure(text=str(wigle_stats.get("discoveredBtGPS", "--")))
            
            # Load device counts from wardrive.json
            try:
                with open("logs/scan_logs/wardrive/wardrive.json", "r") as f:
                    wardrive_data = json.load(f)
                    self.current_scan_frame.update_counts(wardrive_data)
            except Exception:
                self.current_scan_frame.reset_stats()
            
        except Exception as e:
            print(f"Error loading data: {e}")
            
        # Update info bar
        self.info_bar.update_info()
    
    def update_data(self):
        self.load_data()
        self.after(5000, self.update_data)

if __name__ == "__main__":
    app = StatsViewer()
    app.mainloop() 