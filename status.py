#!/usr/bin/env python
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.layout import Layout
from rich.text import Text
from rich.table import Table
from rich.box import SIMPLE
import json
import time
from pathlib import Path
import subprocess
import sys#!/usr/bin/env python
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.layout import Layout
from rich.text import Text
from rich.table import Table
from rich.box import SIMPLE
import json
import time
from pathlib import Path
import subprocess
import sys
import signal

def get_status_color(status: str) -> tuple[str, str]:
    """Get the background and text colors for a status."""
    status = status.upper()
    if status == "SUCCESS":
        return "green", "white"
    elif status == "WARNING":
        return "yellow", "black"
    elif status == "ERROR":
        return "red", "white"
    elif status == "CRITICAL":
        return "black", "white"
    return "white", "black"

def create_status_icons(status_data: dict) -> str:
    """Create the status display as a formatted string."""
    # First create WARDRIVING status separately
    wardriving_status = status_data.get('wardriving', "N/A")
    bg_color, _ = get_status_color(wardriving_status)
    wardriving_text = f"[black on {bg_color}]WARDRIVING[/]"
    
    # Create status indicators for each item with fixed width
    def create_status_text(key, label):
        status = status_data.get(key, "N/A")
        bg_color, _ = get_status_color(status)
        return f"[black on {bg_color}]{label:^4}[/]"
    
    # Create the grid layout with adjusted spacing
    gps = create_status_text('gps_lock', "GPS  ")
    sync = create_status_text('uploading', " SYNC")
    ble = create_status_text('ble_recon', "BLE  ")
    wifi = create_status_text('wifi_recon', " WIFI")
    
    # Return fixed-width layout with adjusted spacing
    return f"{wardriving_text}\n{gps}{sync}\n{ble}{wifi}"

def launch_wardrive():
    """Launch the wardrive.py script as a subprocess."""
    try:
        process = subprocess.Popen(
            [sys.executable, "wardrive.py"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True
        )
        return process
    except Exception as e:
        console = Console()
        console.print(f"[red]Error launching wardrive.py: {str(e)}[/red]")
        return None

def create_stats_box(data: dict, active_time: str) -> str:
    """Create the current stats display box."""
    wifi_count = data.get('wardrive', {}).get('wifi_count', 0)
    ble_count = data.get('wardrive', {}).get('ble_count', 0)
    distance = data.get('stats', {}).get('distance', 'N/A')
    current_speed = data.get('gps', {}).get('speed', 'N/A')
    max_speed = data.get('stats', {}).get('max_speed', 'N/A')
    position = data.get('gps', {}).get('position', 'N/A')
    
    # Parse position into lat/lon if available
    lat, lon = 'N/A', 'N/A'
    if isinstance(position, str) and ',' in position:
        try:
            lat, lon = position.split(',')
        except:
            pass

    # Create each line separately with fixed width formatting for large numbers
    line1 = f"RUN TIME: {active_time}  DEVICES FOUND: WiFi:{wifi_count:>6} BLE:{ble_count:>6}"
    line2 = f"TRAVELED: {distance:>10} SPEED:{current_speed:>10} MAX:{max_speed:>10}"
    line3 = f"GPS COORDINATES: LON:{lon:>15} LAT:{lat:>15}"

    # Calculate the width needed for the box based on the longest line
    width = max(len(line1), len(line2), len(line3))
    
    # Create box parts with proper width
    title = "CURRENT STATS"
    padding = "─" * ((width - len(title)) // 2)
    title_line = f"{padding} {title} {padding}─" if (width - len(title)) % 2 != 0 else f"{padding} {title} {padding}"
    box_top = f"[blue]╭{title_line}╮[/blue]"
    box_bottom = f"[blue]╰{'─' * (width + 2)}╯[/blue]"

    # Format each line to fill the width
    box_line1 = f"[blue]│[/blue] {line1:<{width}} [blue]│[/blue]"
    box_line2 = f"[blue]│[/blue] {line2:<{width}} [blue]│[/blue]"
    box_line3 = f"[blue]│[/blue] {line3:<{width}} [blue]│[/blue]"

    return f"{box_top}\n{box_line1}\n{box_line2}\n{box_line3}\n{box_bottom}"

def create_last_known_location_box(data: dict) -> str:
    """Create the last known location display box."""
    location_data = data.get('location', {})
    address = location_data.get('address', 'N/A')
    suburb = location_data.get('suburb', 'N/A')
    city = location_data.get('city', 'N/A')
    state = location_data.get('state', 'N/A')
    postcode = location_data.get('postcode', 'N/A')
    
    # Format the location string with colors for each component
    location_string = f"[cyan]{address}[/], [green]{suburb}[/], [yellow]{city}[/], [magenta]{state}[/], [red]{postcode}[/]"
    
    # Calculate the actual display length (without markup)
    display_length = len(f"{address}, {suburb}, {city}, {state}, {postcode}")
    
    # Create box with fixed width plus 19 characters
    title = "LAST KNOWN LOCATION"
    base_width = max(display_length + 4, len(title) + 4)
    width = base_width + 19  # Add 19 characters to width
    padding = "─" * ((width - len(title)) // 2)
    title_line = f"{padding} {title} {padding}─" if (width - len(title)) % 2 != 0 else f"{padding} {title} {padding}"
    
    box_top = f"[blue]╭{title_line}╮[/blue]"
    box_bottom = f"[blue]╰{'─' * (width + 2)}╯[/blue]"
    
    # Center the content by calculating padding based on actual display length
    content_padding = (width - display_length) // 2
    box_content = f"[blue]│[/blue]{' ' * (content_padding + 2)}{location_string}{' ' * (width - content_padding - display_length)}[blue]│[/blue]"
    
    return f"{box_top}\n{box_content}\n{box_bottom}"

def main():
    console = Console()
    layout = Layout()
    
    # Add timer tracking variables
    start_time = None
    active_time = "00:00:00"
    
    # Modify the layout to only use the body since status icons will be inside
    layout.split(
        Layout(name="body")
    )

    # Launch wardrive.py
    wardrive_process = launch_wardrive()
    if not wardrive_process:
        console.print("[red]Failed to launch wardrive.py. Exiting...[/red]")
        return

    def signal_handler(signum, frame):
        """Handle termination signals."""
        if wardrive_process:
            wardrive_process.terminate()
        sys.exit(0)

    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    with Live(layout, refresh_per_second=1, screen=True) as live:
        while True:
            try:
                # Check if wardrive process is still running
                if wardrive_process.poll() is not None:
                    console.print("[yellow]Wardrive process stopped. Restarting...[/yellow]")
                    wardrive_process = launch_wardrive()
                    if not wardrive_process:
                        console.print("[red]Failed to restart wardrive.py. Exiting...[/red]")
                        break

                # Read the monitor log to check status
                monitor_log_path = Path("logs/wardrive_logs/wardrive_monitor.log")
                if monitor_log_path.exists():
                    try:
                        with open(monitor_log_path, "r") as f:
                            lines = f.readlines()
                            if lines:  # Only try to get last line if file has content
                                last_line = lines[-1]
                                if "SUCCESS" in last_line or "WARNING" in last_line:
                                    if start_time is None:
                                        start_time = time.time()
                                else:
                                    start_time = None
                                    active_time = "00:00:00"
                            else:  # File is empty
                                start_time = None
                                active_time = "00:00:00"
                    except Exception as e:
                        console.print(f"[yellow]Error reading monitor log: {str(e)}[/yellow]")
                        start_time = None
                        active_time = "00:00:00"
                else:
                    start_time = None
                    active_time = "00:00:00"

                # Calculate active time if we have a start time
                if start_time is not None:
                    elapsed_seconds = int(time.time() - start_time)
                    hours = elapsed_seconds // 3600
                    minutes = (elapsed_seconds % 3600) // 60
                    seconds = elapsed_seconds % 60
                    active_time = f"{hours:02d}:{minutes:02d}:{seconds:02d}"

                # Read the stats data
                log_path = Path("logs/stats_logs/stats_data.log")
                if log_path.exists():
                    with open(log_path, "r") as f:
                        data = json.load(f)
                else:
                    data = {}

                # Get the status icons
                status_icons = create_status_icons(data.get('status', {}))
                status_lines = status_icons.split('\n')
                
                # Create the box with fixed width and rounded corners
                status_box = (
                    "[blue]╭── STATUS ──╮[/blue]\n"
                    f"[blue]│[/blue] {status_lines[0]} [blue]│[/blue]\n"
                    f"[blue]│[/blue] {status_lines[1]} [blue]│[/blue]\n"
                    f"[blue]│[/blue] {status_lines[2]} [blue]│[/blue]\n"
                    "[blue]╰────────────╯[/blue]"
                )

                # Create stats box
                stats_box = create_stats_box(data, active_time)
                
                # Split boxes into lines
                status_lines = status_box.split('\n')
                stats_lines = stats_box.split('\n')
                
                # Combine status and stats boxes side by side
                combined_box = []
                for s_line, st_line in zip(status_lines, stats_lines):
                    combined_box.append(f"{s_line}  {st_line}")
                
                # Create last known location box
                location_box = create_last_known_location_box(data)
                
                # Join all boxes with location box underneath
                combined_display = '\n'.join(combined_box) + '\n' + location_box

                # Format all content as a single string - remove the redundant location information
                content = combined_display

                # Update the body with stats - improved styling
                main_panel = Panel(
                    content,
                    title="[bold blue]Wardrive Monitor[/bold blue]",
                    border_style="blue",
                    padding=(1, 2)
                )
                layout["body"].update(main_panel)

                time.sleep(1)

            except KeyboardInterrupt:
                if wardrive_process:
                    wardrive_process.terminate()
                break
            except Exception as e:
                layout["body"].update(Panel(f"[red]Error: {str(e)}[/red]", border_style="red"))
                time.sleep(1)

if __name__ == "__main__":
    main() 
import signal

def get_status_color(status: str) -> tuple[str, str]:
    """Get the background and text colors for a status."""
    status = status.upper()
    if status == "SUCCESS":
        return "green", "white"
    elif status == "WARNING":
        return "yellow", "black"
    elif status == "ERROR":
        return "red", "white"
    elif status == "CRITICAL":
        return "black", "white"
    return "white", "black"

def create_status_icons(status_data: dict) -> str:
    """Create the status display as a formatted string."""
    # First create WARDRIVING status separately
    wardriving_status = status_data.get('wardriving', "N/A")
    bg_color, _ = get_status_color(wardriving_status)
    wardriving_text = f"[black on {bg_color}]WARDRIVING[/]"
    
    # Create status indicators for each item with fixed width
    def create_status_text(key, label):
        status = status_data.get(key, "N/A")
        bg_color, _ = get_status_color(status)
        return f"[black on {bg_color}]{label:^4}[/]"
    
    # Create the grid layout with adjusted spacing
    gps = create_status_text('gps_lock', "GPS  ")
    sync = create_status_text('uploading', " SYNC")
    ble = create_status_text('ble_recon', "BLE  ")
    wifi = create_status_text('wifi_recon', " WIFI")
    
    # Return fixed-width layout with adjusted spacing
    return f"{wardriving_text}\n{gps}{sync}\n{ble}{wifi}"

def launch_wardrive():
    """Launch the wardrive.py script as a subprocess."""
    try:
        process = subprocess.Popen(
            [sys.executable, "wardrive.py"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True
        )
        return process
    except Exception as e:
        console = Console()
        console.print(f"[red]Error launching wardrive.py: {str(e)}[/red]")
        return None

def main():
    console = Console()
    layout = Layout()
    
    # Add timer tracking variables
    start_time = None
    active_time = "00:00:00"
    
    # Modify the layout to only use the body since status icons will be inside
    layout.split(
        Layout(name="body")
    )

    # Launch wardrive.py
    wardrive_process = launch_wardrive()
    if not wardrive_process:
        console.print("[red]Failed to launch wardrive.py. Exiting...[/red]")
        return

    def signal_handler(signum, frame):
        """Handle termination signals."""
        if wardrive_process:
            wardrive_process.terminate()
        sys.exit(0)

    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    with Live(layout, refresh_per_second=1, screen=True) as live:
        while True:
            try:
                # Check if wardrive process is still running
                if wardrive_process.poll() is not None:
                    console.print("[yellow]Wardrive process stopped. Restarting...[/yellow]")
                    wardrive_process = launch_wardrive()
                    if not wardrive_process:
                        console.print("[red]Failed to restart wardrive.py. Exiting...[/red]")
                        break

                # Read the monitor log to check status
                monitor_log_path = Path("logs/wardrive_logs/wardrive_monitor.log")
                if monitor_log_path.exists():
                    try:
                        with open(monitor_log_path, "r") as f:
                            lines = f.readlines()
                            if lines:  # Only try to get last line if file has content
                                last_line = lines[-1]
                                if "SUCCESS" in last_line or "WARNING" in last_line:
                                    if start_time is None:
                                        start_time = time.time()
                                else:
                                    start_time = None
                                    active_time = "00:00:00"
                            else:  # File is empty
                                start_time = None
                                active_time = "00:00:00"
                    except Exception as e:
                        console.print(f"[yellow]Error reading monitor log: {str(e)}[/yellow]")
                        start_time = None
                        active_time = "00:00:00"
                else:
                    start_time = None
                    active_time = "00:00:00"

                # Calculate active time if we have a start time
                if start_time is not None:
                    elapsed_seconds = int(time.time() - start_time)
                    hours = elapsed_seconds // 3600
                    minutes = (elapsed_seconds % 3600) // 60
                    seconds = elapsed_seconds % 60
                    active_time = f"{hours:02d}:{minutes:02d}:{seconds:02d}"

                # Read the stats data
                log_path = Path("logs/stats_logs/stats_data.log")
                if log_path.exists():
                    with open(log_path, "r") as f:
                        data = json.load(f)
                else:
                    data = {}

                # Get the status icons
                status_icons = create_status_icons(data.get('status', {}))
                status_lines = status_icons.split('\n')
                
                # Create the box with fixed width and rounded corners
                status_box = (
                    "[blue]╭── STATUS ──╮[/blue]\n"
                    f"[blue]│[/blue] {status_lines[0]} [blue]│[/blue]\n"
                    f"[blue]│[/blue] {status_lines[1]} [blue]│[/blue]\n"
                    f"[blue]│[/blue] {status_lines[2]} [blue]│[/blue]\n"
                    "[blue]╰────────────╯[/blue]"
                )

                # Format all content as a single string with active time
                content = f"""{status_box}

[bold blue]Wardrive Status[/bold blue]
Time: {active_time}
WiFi Networks: {data.get('wardrive', {}).get('wifi_count', 0)}
BLE Devices: {data.get('wardrive', {}).get('ble_count', 0)}

[bold green]Location[/bold green]
Address: {data.get('location', {}).get('address', 'N/A')}
Suburb: {data.get('location', {}).get('suburb', 'N/A')}
City: {data.get('location', {}).get('city', 'N/A')}
State: {data.get('location', {}).get('state', 'N/A')}
Postcode: {data.get('location', {}).get('postcode', 'N/A')}

[bold yellow]GPS Data[/bold yellow]
Position: {data.get('gps', {}).get('position', 'N/A')}
Altitude: {data.get('gps', {}).get('altitude', 'N/A')}
Speed: {data.get('gps', {}).get('speed', 'N/A')}
Heading: {data.get('gps', {}).get('heading', 'N/A')}
Satellites: {data.get('gps', {}).get('satellites', 0)}

[bold magenta]Statistics[/bold magenta]
Distance: {data.get('stats', {}).get('distance', 'N/A')}
Average Speed: {data.get('stats', {}).get('avg_speed', 'N/A')}
Max Speed: {data.get('stats', {}).get('max_speed', 'N/A')}
Average Altitude: {data.get('stats', {}).get('avg_altitude', 'N/A')}
Satellites: {data.get('stats', {}).get('satellites', 0)}"""

                # Update the body with stats - improved styling
                main_panel = Panel(
                    content,
                    title="[bold blue]Wardrive Monitor[/bold blue]",
                    border_style="blue",
                    padding=(1, 2)
                )
                layout["body"].update(main_panel)

                time.sleep(1)

            except KeyboardInterrupt:
                if wardrive_process:
                    wardrive_process.terminate()
                break
            except Exception as e:
                layout["body"].update(Panel(f"[red]Error: {str(e)}[/red]", border_style="red"))
                time.sleep(1)

if __name__ == "__main__":
    main() 
