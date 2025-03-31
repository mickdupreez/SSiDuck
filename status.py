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

    # Create each line separately with fixed width formatting and colors
    line1 = f"RUN TIME: [cyan]{active_time}[/] DEVICES FOUND:  WiFi: [green]{wifi_count:>5}[/]  BLE: [green]{ble_count:>5}[/]"
    line2 = f"TRAVELED: [yellow]{distance:>7}[/]    SPEED: [cyan]{current_speed:>7}[/]  MAX: [red]{max_speed:>7}[/]"
    line3 = f"GPS COORDINATES: LONGTITUDE: [magenta]{lon:>10}[/]    LATITUDE: [magenta]{lat:>10}[/]"

    # Calculate the width needed for the box based on the longest line plus 15 characters
    base_width = max(len(line1.replace('[cyan]', '').replace('[/]', '')
                     .replace('[green]', '').replace('[yellow]', '')
                     .replace('[red]', '').replace('[magenta]', '')),
                 len(line2.replace('[cyan]', '').replace('[/]', '')
                     .replace('[green]', '').replace('[yellow]', '')
                     .replace('[red]', '').replace('[magenta]', '')),
                 len(line3.replace('[cyan]', '').replace('[/]', '')
                     .replace('[green]', '').replace('[yellow]', '')
                     .replace('[red]', '').replace('[magenta]', '')))
    width = base_width + 10  # Add 15 characters to width
    
    # Create box parts with proper width
    title = "CURRENT STATS"
    padding = "─" * ((width - len(title) - 2) // 2)  # Subtract 2 to account for the corners
    title_line = f"{padding} {title} {padding}─" if (width - len(title) - 2) % 2 != 0 else f"{padding} {title} {padding}"
    box_top = f"[blue]╭{title_line}╮[/blue]"
    box_bottom = f"[blue]╰{'─' * width}╯[/blue]"

    # Format each line to fill the width, accounting for the color markup in padding calculation
    def pad_line(line):
        clean_line = (line.replace('[cyan]', '').replace('[/]', '')
                     .replace('[green]', '').replace('[yellow]', '')
                     .replace('[red]', '').replace('[magenta]', ''))
        # Calculate padding for centering
        padding = (width - len(clean_line)) // 2
        left_padding = ' ' * padding
        right_padding = ' ' * (width - len(clean_line) - padding)
        return f"[blue]│[/blue]{left_padding}{line}{right_padding}[blue]│[/blue]"

    box_line1 = pad_line(line1)
    box_line2 = pad_line(line2)
    box_line3 = pad_line(line3)

    return f"{box_top}\n{box_line1}\n{box_line2}\n{box_line3}\n{box_bottom}"

def create_last_known_location_box(data: dict) -> str:
    """Create the last known location display box."""
    location_data = data.get('location', {})
    weather_data = data.get('weather', {})
    address = location_data.get('address', 'N/A')
    suburb = location_data.get('suburb', 'N/A')
    city = location_data.get('city', 'N/A')
    state = location_data.get('state', 'N/A')
    postcode = location_data.get('postcode', 'N/A')
    
    # Format the location string with colors for each component
    location_string = f"[cyan]{address}[/], [green]{suburb}[/], [yellow]{city}[/], [magenta]{state}[/], [red]{postcode}[/]"
    
    # Format weather string
    weather_string = (
        f"TEMP: [cyan]{weather_data.get('temperature', 'N/A')}[/], "
        f"HUMID: [green]{weather_data.get('humidity', 'N/A')}[/], "
        f"RAIN: [blue]{weather_data.get('precipitation', 'N/A')}[/], "
        f"CLOUD COVER: [white]{weather_data.get('cloud_cover', 'N/A')}[/], "
        f"WIND SPEED: [yellow]{weather_data.get('wind_speed', 'N/A')}[/]"
    )
    
    # Calculate the actual display length (without markup)
    location_length = len(f"{address}, {suburb}, {city}, {state}, {postcode}")
    weather_length = len(weather_string.replace('[cyan]', '').replace('[/]', '')
                        .replace('[green]', '').replace('[blue]', '')
                        .replace('[white]', '').replace('[yellow]', ''))
    
    # Create box with fixed width plus 11 characters (original width)
    title = "LAST KNOWN LOCATION"
    base_width = max(location_length, weather_length, len(title))
    width = base_width + 11  # Reduced from 11 to 11 to fix alignment
    
    # Create title line with proper centering
    padding = "─" * ((width - len(title) - 2) // 2)  # Back to -2
    title_line = f"{padding} {title} {padding}─"  # Added an extra ─ at the end
    
    box_top = f"[blue]╭{title_line}╮[/blue]"
    box_bottom = f"[blue]╰{'─' * width}╯[/blue]"
    
    # Center both lines of content
    def center_line(content, content_length):
        padding = (width - content_length) // 2
        return f"[blue]│[/blue]{' ' * padding}{content}{' ' * (width - padding - content_length)}[blue]│[/blue]"
    
    box_content1 = center_line(location_string, location_length)
    box_content2 = center_line(weather_string, weather_length)
    
    return f"{box_top}\n{box_content1}\n{box_content2}\n{box_bottom}"

def create_live_feed_box(data: dict) -> str:
    """Create the live feed display box."""
    # Calculate width based on the location box width
    location_data = data.get('location', {})
    weather_data = data.get('weather', {})
    address = location_data.get('address', 'N/A')
    suburb = location_data.get('suburb', 'N/A')
    city = location_data.get('city', 'N/A')
    state = location_data.get('state', 'N/A')
    postcode = location_data.get('postcode', 'N/A')
    
    # Calculate base width from location string
    location_length = len(f"{address}, {suburb}, {city}, {state}, {postcode}")
    weather_string = (
        f"TEMP: {weather_data.get('temperature', 'N/A')}, "
        f"HUMID: {weather_data.get('humidity', 'N/A')}, "
        f"RAIN: {weather_data.get('precipitation', 'N/A')}, "
        f"CLOUD COVER: {weather_data.get('cloud_cover', 'N/A')}, "
        f"WIND SPEED: {weather_data.get('wind_speed', 'N/A')}"
    )
    weather_length = len(weather_string)
    
    # Use the same width calculation as the location box
    base_width = max(location_length, weather_length, len("LAST KNOWN LOCATION"))
    width = base_width + 11  # Match the location box width
    
    # Create box parts with proper width
    title = "LIVE FEED"
    padding = "─" * ((width - len(title) - 2) // 2)
    title_line = f"{padding} {title} {padding}─" if (width - len(title) - 2) % 2 != 0 else f"{padding} {title} {padding}"
    box_top = f"[blue]╭{title_line}╮[/blue]"
    box_bottom = f"[blue]╰{'─' * width}╯[/blue]"
    
    # Create empty content lines with proper padding to match the width exactly
    empty_space = ' ' * width  # Full width of empty space
    content_line = f"[blue]│[/blue]{empty_space}[blue]│[/blue]"
    content_lines = [content_line] * 8  # Create 8 empty lines
    
    return f"{box_top}\n" + "\n".join(content_lines) + f"\n{box_bottom}"

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
                
                # Create live feed box with the same data to match width
                live_feed_box = create_live_feed_box(data)
                
                # Join all boxes with location box and live feed box underneath
                combined_display = '\n'.join(combined_box) + '\n' + location_box + '\n' + live_feed_box

                # Format all content as a single string
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
