#!/usr/bin/env python3

import json
import asyncio
from pathlib import Path
from textual.app import App, ComposeResult
from textual.containers import Container, Vertical, Horizontal, Grid
from textual.widgets import Static, Button
from textual.reactive import reactive
from textual import events
from textual.timer import Timer
from textual.scroll_view import ScrollView
from textual.widgets._static import Static
from rich.text import Text
from rich.style import Style
from datetime import datetime, timedelta
import subprocess
import psutil
import sys

class DataManager:
    """Centralized data manager that reads and caches log file data."""
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(DataManager, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if not self._initialized:
            self._initialized = True
            self.data = {}
            self.callbacks = []
            self.update_timer = None
    
    def start_updates(self, app):
        """Start periodic updates of the log file data."""
        self.update_timer = app.set_interval(0.5, self.update_data)
    
    def register_callback(self, callback):
        """Register a callback to be notified when data updates."""
        if callback not in self.callbacks:
            self.callbacks.append(callback)
    
    def update_data(self) -> None:
        """Update the cached data from the log file."""
        try:
            with open('logs/stats_logs/stats_data.log', 'r') as f:
                self.data = json.load(f)
                # Notify all registered callbacks
                for callback in self.callbacks:
                    callback(self.data)
        except (FileNotFoundError, json.JSONDecodeError):
            self.data = {}
    
    def get_data(self) -> dict:
        """Get the current cached data."""
        return self.data

def load_settings():
    """Load settings from settings.json file."""
    try:
        with open('settings.json', 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        print("Warning: settings.json not found. Using default settings.")
        return {}

class LocationBox(Static):
    """A box widget specifically for displaying scrolling location data."""
    
    current_offset = reactive(0)
    content_width = reactive(0)
    scroll_text = reactive("")
    
    DEFAULT_CSS = """
    LocationBox {
        background: transparent;
        border: round $accent;
        padding: 0 1;
        margin: 0 1;
        content-align: center middle;
        width: 100%;
        overflow-x: hidden;
    }
    """
    
    def __init__(self):
        super().__init__("")
        self.scroll_timer = None
        self.data_manager = DataManager()
    
    def on_mount(self) -> None:
        """Set up the scroll timer when the widget is mounted."""
        self.scroll_timer = self.set_interval(0.1, self.scroll_text_content)
        # Register for data updates instead of reading directly
        self.data_manager.register_callback(self.handle_data_update)
    
    def handle_data_update(self, data: dict) -> None:
        """Handle data updates from the DataManager."""
        self.scroll_text = self.format_location_data(data)
        self.content_width = len(str(self.scroll_text))
    
    def format_location_data(self, data: dict) -> str:
        """Format location data with colors."""
        location = data.get("location", {})
        address = location.get("address", "N/A")
        suburb = location.get("suburb", "N/A")
        city = location.get("city", "N/A")
        state = location.get("state", "N/A")
        postcode = location.get("postcode", "N/A")
        
        text = Text()
        text.append(address, Style(color="cyan"))
        text.append(" | ", Style(color="white"))
        text.append(suburb, Style(color="green"))
        text.append(" | ", Style(color="white"))
        text.append(city, Style(color="yellow"))
        text.append(" | ", Style(color="white"))
        text.append(state, Style(color="magenta"))
        text.append(" | ", Style(color="white"))
        text.append(postcode, Style(color="red"))
        
        return text
    
    def scroll_text_content(self) -> None:
        """Scroll the text content horizontally."""
        if self.content_width > self.size.width:
            self.current_offset = (self.current_offset + 1) % self.content_width
            self.refresh()
    
    def render(self) -> Text:
        """Render the scrolling text content."""
        if not self.scroll_text:
            return Text("Loading...", Style(color="yellow"))
        
        if self.content_width <= self.size.width:
            return self.scroll_text
        
        # Calculate the visible portion of the text
        text_str = str(self.scroll_text)
        wrapped_text = text_str + "    " + text_str
        start_pos = self.current_offset
        visible_text = wrapped_text[start_pos:start_pos + self.size.width]
        
        return Text(visible_text)

class ButtonGrid(Vertical):
    """A vertical layout with WARDRIVING button and 2x2 grid below."""
    
    # Add button state tracking
    active_buttons = reactive(set())
    wardrive_process = None
    
    # Status color mapping
    STATUS_COLORS = {
        "SUCCESS": "green",
        "WARNING": "orange",
        "ERROR": "red",
        "CRITICAL": "transparent"
    }
    
    def get_status_color(self, status):
        """Get the color for a status, defaulting to CRITICAL color for unknown statuses."""
        return self.STATUS_COLORS.get(status, self.STATUS_COLORS["CRITICAL"])
    
    # Button to status field mapping
    BUTTON_STATUS_MAP = {
        "wardriving-btn": "wardriving",
        "gps-btn": "gps_lock",
        "wifi-btn": "wifi_recon",
        "ble-btn": "ble_recon",
        "sync-btn": "uploading"
    }
    
    DEFAULT_CSS = """
    ButtonGrid {
        height: 100%;
        width: 100%;
        background: transparent;
        align: center middle;
    }
    
    Button {
        width: 100%;
        height: 100%;
        background: transparent;
        border: solid transparent;
        content-align: center middle;
        text-align: center;
        padding: 0;
        margin: 0;
    }
    
    Button:hover {
        background: blue !important;
        border: solid blue;
    }
    
    Button.-active {
        background: $accent !important;
        color: $text;
        border: solid $accent;
    }

    Button:focus {
        border: solid $accent;
        background: transparent;
    }
    
    #wardriving-container {
        width: 100%;
        height: auto;
        align: center middle;
    }

    #button-grid {
        width: 100%;
        height: 1fr;
        grid-size: 2 2;
        grid-rows: 1fr 1fr;
        grid-columns: 1fr 1fr;
        align: center middle;
        background: transparent;
    }

    #gps-btn, #sync-btn, #ble-btn, #wifi-btn {
        margin: 1;
    }
    """

    def __init__(self):
        super().__init__()
        self.data_manager = DataManager()

    def compose(self) -> ComposeResult:
        """Create the button layout."""
        with Container(id="wardriving-container"):
            yield Button("WARDRIVING", id="wardriving-btn")
        
        with Grid(id="button-grid"):
            yield Button("GPS", id="gps-btn")
            yield Button("SYNC", id="sync-btn")
            yield Button("BLE", id="ble-btn")
            yield Button("WIFI", id="wifi-btn")

    def on_mount(self) -> None:
        """Register for data updates when the widget is mounted."""
        self.data_manager.register_callback(self.handle_data_update)

    def handle_data_update(self, data: dict) -> None:
        """Handle data updates from the DataManager."""
        status_data = data.get('status', {})
        
        for button_id, status_field in self.BUTTON_STATUS_MAP.items():
            button = self.query_one(f"#{button_id}", Button)
            status = status_data.get(status_field, "CRITICAL")
            color = self.get_status_color(status)
            
            if color != "transparent":
                button.styles.background = color
            else:
                button.styles.background = "transparent"

    def is_wardrive_running(self):
        """Check if wardrive.py is running."""
        if self.wardrive_process:
            try:
                # Check if process is still running
                if self.wardrive_process.poll() is None:
                    return True
                self.wardrive_process = None
            except:
                self.wardrive_process = None
        return False

    def stop_wardrive(self):
        """Stop the wardrive.py process if it's running."""
        if self.wardrive_process:
            try:
                # Try graceful termination first
                self.wardrive_process.terminate()
                try:
                    self.wardrive_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    # If graceful termination fails, force kill
                    self.wardrive_process.kill()
                    self.wardrive_process.wait()
            except:
                pass
            finally:
                self.wardrive_process = None

    def start_wardrive(self):
        """Start the wardrive.py process."""
        try:
            # Get the path to wardrive.py relative to the current script
            script_dir = Path(__file__).parent
            wardrive_path = script_dir / "wardrive.py"
            
            # Start the process
            self.wardrive_process = subprocess.Popen(
                [sys.executable, str(wardrive_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            return True
        except Exception as e:
            print(f"Failed to start wardrive.py: {e}")
            return False

    def toggle_wardrive(self):
        """Toggle the wardrive.py process on/off."""
        if self.is_wardrive_running():
            self.stop_wardrive()
            return False
        else:
            return self.start_wardrive()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        event.stop()
        button_id = event.button.id
        
        if button_id == "wardriving-btn":
            # Handle wardriving button specifically
            success = self.toggle_wardrive()
            if success:
                self.active_buttons.add(button_id)
                event.button.add_class("-active")
            else:
                self.active_buttons.discard(button_id)
                event.button.remove_class("-active")
        else:
            # Handle other buttons as before
            if button_id in self.active_buttons:
                self.active_buttons.remove(button_id)
                event.button.remove_class("-active")
            else:
                self.active_buttons.add(button_id)
                event.button.add_class("-active")
        
        # Ensure focus is removed
        self.screen.set_focus(None)

class Box(Static):
    """A custom box widget with modern styling."""
    DEFAULT_CSS = """
    Box {
        background: transparent;
        border: round $accent;
        padding: 0 1;
        margin: 0 1;
        content-align: left middle;
    }
    """

    def __init__(self, content: str, id: str | None = None):
        super().__init__(content, id=id)
        if id == "wardrive_status":
            self.styles.content_align = ("center", "middle")
            self.styles.text_align = "center"
            self.styles.padding = (0, 1)
            self.styles.height = 5

class StatsBox(Static):
    """A widget for displaying wardriving statistics in a 1x3 grid layout."""
    
    DEFAULT_CSS = """
    StatsBox {
        background: transparent;
        border: round $accent;
        padding: 0 1;
        margin: 0 1;
        height: 5;
        width: 80%;
    }
    
    .stats-grid {
        grid-size: 3 1;
        grid-columns: 1fr 1fr 1fr;
        height: 100%;
        padding: 0;
    }
    
    .stats-column {
        width: 100%;
        height: 100%;
        content-align: center middle;
        text-align: center;
    }
    """
    
    def __init__(self):
        super().__init__()
        self.data_manager = DataManager()
        self.start_time = datetime.now()
        self.timer = None
    
    def compose(self) -> ComposeResult:
        """Create the grid layout with three columns."""
        with Grid(classes="stats-grid"):
            yield Static("Loading...", classes="stats-column")
            yield Static("Loading...", classes="stats-column")
            yield Static("Loading...", classes="stats-column")
    
    def on_mount(self) -> None:
        """Register for data updates when mounted and start the timer."""
        self.data_manager.register_callback(self.handle_data_update)
        # Update timer every second
        self.timer = self.set_interval(1.0, self.update_timer)
    
    def get_elapsed_time(self) -> str:
        """Calculate and format the elapsed time."""
        elapsed = datetime.now() - self.start_time
        hours = int(elapsed.total_seconds() // 3600)
        minutes = int((elapsed.total_seconds() % 3600) // 60)
        seconds = int(elapsed.total_seconds() % 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    
    def update_timer(self) -> None:
        """Update the timer display."""
        columns = self.query(".stats-column")
        if columns and len(columns) >= 1:
            self.update_first_column(columns[0])
    
    def update_first_column(self, column: Static) -> None:
        """Update the first column with current timer and stats."""
        wardrive_data = self.data_manager.get_data().get('wardrive', {})
        total_devices = wardrive_data.get('wifi_count', 0) + wardrive_data.get('ble_count', 0)
        
        stats_data = self.data_manager.get_data().get('stats', {})
        distance = stats_data.get('distance', '0.00m')
        avg_speed = stats_data.get('avg_speed', '0.0km/h')
        
        first_column = Text()
        first_column.append(f"{self.get_elapsed_time()}\n", Style(color="magenta"))
        first_column.append(f"DEVICES FOUND: {total_devices}\n", Style(color="blue"))
        first_column.append(f"{avg_speed} : {distance}", Style(color="bright_yellow"))
        
        column.update(first_column)
    
    def update_second_column(self, column: Static, data: dict) -> None:
        """Update the second column with GPS and movement data."""
        gps_data = data.get('gps', {})
        stats_data = data.get('stats', {})
        
        # Format GPS coordinates
        position = gps_data.get('position', 'N/A')
        try:
            lat, lon = position.split(', ')
            lat = float(lat)
            lon = float(lon)
            coord_line = f"LON: {lat:.3f} LAT:{lon:.3f}"
        except (ValueError, AttributeError):
            coord_line = "LON: N/A LAT: N/A"
        
        # Format altitude data
        current_alt = gps_data.get('altitude', 'N/A').replace('m', '')
        avg_alt = stats_data.get('avg_altitude', 'N/A').replace('m', '')
        try:
            current_alt = float(current_alt)
            avg_alt = float(avg_alt)
            alt_line = f"ALT: {current_alt:.1f}m MAX: {avg_alt:.1f}m"
        except (ValueError, AttributeError):
            alt_line = "ALT: N/A MAX: N/A"
        
        # Format max speed
        max_speed = stats_data.get('max_speed', 'N/A').replace('km/h', '')
        try:
            max_speed = float(max_speed)
            speed_line = f"MAX: {max_speed:.1f}km/h"
        except (ValueError, AttributeError):
            speed_line = "MAX: N/A"
        
        second_column = Text()
        second_column.append(f"{coord_line}\n", Style(color="yellow"))
        second_column.append(f"{alt_line}\n", Style(color="cyan"))
        second_column.append(speed_line, Style(color="green"))
        
        column.update(second_column)
    
    def update_third_column(self, column: Static, data: dict) -> None:
        """Update the third column with weather data."""
        weather_data = data.get('weather', {})
        if not weather_data:
            column.update("Loading...")
            return
            
        # Get weather values with defaults
        temperature = weather_data.get('temperature', 'N/A')
        humidity = weather_data.get('humidity', 'N/A')
        wind_speed = weather_data.get('wind_speed', 'N/A')
        wind_direction = weather_data.get('wind_direction', 'N/A')
        cloud_cover = weather_data.get('cloud_cover', 'N/A')
        
        third_column = Text()
        third_column.append(f"TEMP: {temperature} HUM: {humidity}\n", Style(color="red"))
        third_column.append(f"WIND: {wind_speed}:{wind_direction}\n", Style(color="magenta2"))
        third_column.append(f"CLOUDCOVER: {cloud_cover}", Style(color="bright_white"))
        
        column.update(third_column)

    def handle_data_update(self, data: dict) -> None:
        """Update the display with new data."""
        columns = self.query(".stats-column")
        if not columns or len(columns) != 3:
            return
        
        self.update_first_column(columns[0])
        self.update_second_column(columns[1], data)
        self.update_third_column(columns[2], data)

class LiveFeedBox(Static):
    """A box widget for displaying a live feed of latest devices with scrolling effect."""
    
    DEFAULT_CSS = """
    LiveFeedBox {
        background: transparent;
        border: round $accent;
        padding: 0 1;
        margin: 0 1;
        content-align: center middle;
        height: 13;
    }
    """
    
    def __init__(self):
        super().__init__("")
        self.data_manager = DataManager()
        self.device_history = []
        self.max_history = 13  # Match the height of the box
    
    def on_mount(self) -> None:
        """Register for data updates when the widget is mounted."""
        self.data_manager.register_callback(self.handle_data_update)
    
    def parse_device_string(self, device: str) -> tuple[str, str, str]:
        """Parse device string into MAC, name/ESSID, and security components."""
        try:
            # Split on first ' - ' to separate MAC from rest
            mac, rest = device.split(' - ', 1)
            
            # For WiFi devices, split security info in parentheses
            if '(' in rest and ')' in rest:
                name, security = rest.rsplit(' (', 1)
                security = f"({security}"  # Add back the opening parenthesis
            else:
                name = rest
                security = ""
                
            return mac.strip(), name.strip(), security.strip()
        except ValueError:
            return device, "", ""
    
    def handle_data_update(self, data: dict) -> None:
        """Handle data updates from the DataManager."""
        latest_devices = data.get('wardrive', {}).get('latest_devices', [])
        
        # Add new devices to history
        for device in latest_devices:
            if device not in self.device_history:
                self.device_history.append(device)
        
        # Keep only the most recent devices
        if len(self.device_history) > self.max_history:
            self.device_history = self.device_history[-self.max_history:]
        
        self.refresh()
    
    def render(self) -> Text:
        """Render the live feed with centered, truncated text."""
        result = Text()
        max_width = self.size.width - 2  # Account for padding
        
        # Add each device to the display
        for i, device in enumerate(self.device_history):
            # Parse device string into components
            mac, name, security = self.parse_device_string(device)
            
            # Determine if this is a BLE or WiFi device
            is_ble = "Apple Inc." in device or "LE" in device
            
            # Color MAC address based on device type
            if is_ble:
                result.append(mac, Style(color="bright_blue"))  # BLE MACs in bright blue
            else:
                result.append(mac, Style(color="bright_yellow"))  # WiFi MACs in bright yellow
            
            # Add separator
            result.append(" - ", Style(color="white"))
            
            # Color name/ESSID
            if "[Hidden]" in name:
                result.append(name, Style(color="bright_red"))
            elif is_ble:
                result.append(name, Style(color="cyan"))  # BLE names in cyan
            else:
                result.append(name, Style(color="bright_green"))  # WiFi ESSIDs in bright green
            
            # Add security info if present
            if security:
                result.append(" ", Style(color="white"))
                result.append(security, Style(color="magenta"))  # Security info in magenta
            
            # Add newline if not the last item
            if i < len(self.device_history) - 1:
                result.append("\n")
            
            # Check if we need to truncate
            current_line = str(result).split('\n')[-1]
            if len(current_line) > max_width:
                # Remove the last line
                result = Text('\n'.join(str(result).split('\n')[:-1]) + '\n')
                # Add truncated version
                truncated = current_line[:max_width-3] + "..."
                result.append(truncated)
        
        return result

class ModernApp(App):
    """A modern Textual application with three boxes."""
    
    def __init__(self):
        super().__init__()
        self.settings = load_settings()
        self.ui_settings = self.settings.get('STATUS_BOXES', {})
        self.data_manager = DataManager()
    
    @property
    def CSS(self) -> str:
        """Generate CSS dynamically based on settings."""
        wardrive_status = self.ui_settings.get('wardrive_status', {'width': '20%', 'height': '5'})
        current_stats = self.ui_settings.get('current_stats', {'width': '80%', 'height': '5'})
        last_location = self.ui_settings.get('last_location', {'width': '100%', 'height': '3'})
        live_feed = self.ui_settings.get('live_feed', {'width': '100%', 'height': '13'})

        return f"""
        Screen {{
            background: transparent;
            color: $text;
            align: center middle;
        }}

        #main-container {{
            background: transparent;
            layout: vertical;
            align: center middle;
            height: 100%;
            width: 100%;
            padding: 1;
        }}

        #top-row {{
            layout: horizontal;
            height: 5;
            width: 100%;
            align: center middle;
        }}

        #wardrive_status {{
            width: {wardrive_status['width']};
            height: {wardrive_status['height']};
            margin: 0 0 0 0;
            border: round $accent;
            background: transparent;
            content-align: center middle;
            padding: 0;
        }}

        ButtonGrid {{
            padding: 0;
            margin: 0;
            align: center middle;
        }}

        Button {{
            background: transparent;
            border: none;
            height: auto;
            min-width: 0;
            padding: 0;
            margin: 0;
            color: $text;
        }}

        Button:hover {{
            background: $accent-darken-2;
        }}

        #wardriving-btn {{
            text-style: bold;
        }}

        #current_stats {{
            width: {current_stats['width']};
            height: {current_stats['height']};
            margin: 0;
        }}

        #last_location {{
            width: {last_location['width']};
            height: {last_location['height']};
            margin-top: 0;
        }}

        #live_feed {{
            width: {live_feed['width']};
            height: {live_feed['height']};
            margin-top: 0;
        }}
        """

    def compose(self) -> ComposeResult:
        """Create child widgets for the app."""
        with Container(id="main-container"):
            with Container(id="top-row"):
                with Container(id="wardrive_status"):
                    yield ButtonGrid()
                yield StatsBox()
            yield LocationBox()
            yield LiveFeedBox()

    def on_mount(self) -> None:
        """Set up the application when it starts."""
        self.title = "Modern Boxes"
        # Start the data manager's update timer
        self.data_manager.start_updates(self)

if __name__ == "__main__":
    app = ModernApp()
    app.run()
