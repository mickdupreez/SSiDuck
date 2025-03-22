#!/usr/bin/env python3
import socket          # For creating and managing socket connections
import json            # For loading and saving JSON settings
import os              # For file and path management
import sys             # For system-level operations (exiting, etc.)
import pty             # For creating pseudo-terminals
import subprocess      # For running external commands (e.g., creating symlinks)
import fcntl           # For manipulating file descriptors and I/O modes
import time            # For time-based operations (sleep, current time)
from loguru import logger  # For logging with flexible, pluggable sinks

def load_settings(file_path="gps_settings.json"):
    """
    Loads the settings from the given JSON file.
    If the file is missing or invalid JSON, a default settings file is created.
    Returns a dictionary of settings on success, or exits with an error on failure.
    """
    # Define a default settings object to be written if the config file is missing or invalid
    default_settings = {
        "GPS_SETTINGS": {
            "udp_ip": "172.20.10.3",
            "udp_port": 11123,
            "buffer_size": 4096,
            "socket_timeout_sec": 10,
            "log_gps_data": True,
            "gps_log_path": "~/.local/bin/wardriver/logs/gps_data.log",
            "requests_dir": "~/.local/bin/wardriver/logs/"
        },
        "LOGGING_SETTINGS": {
            "log_to_file": True,
            "log_to_terminal": True,
            "log_file_path": "~/.local/bin/wardriver/logs/gps_monitor.log",
            "log_level": "TRACE"
        }
    }
    try:
        # Attempt to open and load the JSON settings file
        with open(file_path, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        # If not found or invalid JSON, create a default file
        print(f"Settings file '{file_path}' missing/invalid. Creating default...")
        try:
            # Write default settings to the file
            with open(file_path, "w") as f:
                json.dump(default_settings, f, indent=2)
            print(f"Default settings written to '{file_path}'.")
            return default_settings
        except Exception as e:
            print(f"Failed to create settings file: {e}")
            sys.exit(1)
    except Exception as e:
        # Catch-all for any other exceptions
        print(f"Unexpected error: {e}")
        sys.exit(1)

# Load the settings from the JSON file (or create a default one if it doesn't exist)
settings = load_settings()
gps_settings = settings["GPS_SETTINGS"]
logging_settings = settings["LOGGING_SETTINGS"]

# Expand user paths to turn something like '~/.local/bin...' into an absolute path
gps_log_path = os.path.expanduser(gps_settings["gps_log_path"])
log_file_path = os.path.expanduser(logging_settings["log_file_path"])
requests_directory = os.path.expanduser(gps_settings["requests_dir"])

# Ensure the requests directory exists; if it does not, create it
os.makedirs(requests_directory, exist_ok=True)

# Clear the existing log file if it exists (for a clean start)
if os.path.exists(log_file_path):
    open(log_file_path, "w").close()

# Remove any existing logger configuration to set ours afresh
logger.remove()

# If logging to file is enabled, add a file sink
if logging_settings.get("log_to_file", True):
    logger.add(log_file_path, level=logging_settings["log_level"].upper())

# If logging to terminal is enabled, add a terminal (stderr) sink
if logging_settings.get("log_to_terminal", True):
    logger.add(
        sys.stderr,
        level=logging_settings["log_level"].upper(),
        colorize=True,
        format="<yellow>{time:DD/MM @ HH:mm:ss}</yellow><red>| GPS |</red><level>{level:^7}</level><red>|</red> <cyan>{message}</cyan>"
    )

# Indicate that the logger has been successfully set up
logger.success("Logger initialized.")

# Extract key GPS-related settings
UDP_IP = gps_settings["udp_ip"]                    # IP address to bind the UDP socket
UDP_PORT = gps_settings["udp_port"]                # Port to bind the UDP socket
BUFFER_SIZE = gps_settings["buffer_size"]          # Read buffer size for UDP
SOCKET_TIMEOUT = gps_settings["socket_timeout_sec"] # Timeout in seconds for UDP receptions

# Create a UDP socket object for receiving GPS data
udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

# This interval (in seconds) determines how long we wait between bind attempts
attempt_interval = 1

# Counter to track how many binding attempts have been made
attempt_count = 0

# Continuously try to bind the socket until successful
while True:
    try:
        # Attempt to bind the socket to (UDP_IP, UDP_PORT)
        udp_socket.bind((UDP_IP, UDP_PORT))
        # If successful, set the timeout for receiving data
        udp_socket.settimeout(SOCKET_TIMEOUT)
        # Log success and break from the loop
        logger.success(f"UDP socket bound to {UDP_IP}:{UDP_PORT}")
        break
    except Exception:
        # Increment the attempt counter
        attempt_count += 1

        # For the first 30 failed attempts, log a WARNING
        if attempt_count <= 30:
            logger.warning("!NETWORK DOWN! Trying to reconnect")

        # On the 31st failed attempt, log an ERROR (only once)
        elif attempt_count == 31:
            logger.error(
                "Network down. Too many attempts to reconnect. "
                "Will keep retrying silently until we succeed."
            )

        # If more than 31 attempts, do not log again (remain silent)

        # Wait for some time before trying again
        time.sleep(attempt_interval)

# Dictionary to hold active pseudo-terminal devices (keys: device name, values: master_fd)
active_devices = {}

# Dictionary to track how many times each device's write buffer was full
buffer_full_counts = {}

def create_virtual_device(device_name):
    """
    Creates a virtual TTY device using pty.openpty() and symlinks it to /dev/tty{device_name}.
    Returns the master file descriptor if creation is successful, or None otherwise.
    """
    try:
        # Create a new pseudo-terminal pair (master_fd, slave_fd)
        master_fd, slave_fd = pty.openpty()
        # Get the OS-level name of the slave device
        device_path = os.ttyname(slave_fd)
        # Build the symlink path: /dev/tty{device_name}
        symlink_path = f"/dev/tty{device_name}"

        # Use 'sudo ln -sf' to create or overwrite the symlink
        subprocess.run(["sudo", "ln", "-sf", device_path, symlink_path], check=True)
        logger.success(f"Created virtual device: {symlink_path} -> {device_path}")

        # Retrieve the current flags for the master file descriptor
        flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
        # Set the file descriptor to non-blocking
        fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

        # Initialize the "buffer is full" count for this newly created device
        buffer_full_counts[device_name] = 0

        # Return the master file descriptor for further writes
        return master_fd
    except subprocess.CalledProcessError as e:
        # If we fail to create the symlink, log a critical error
        logger.critical(f"Failed to create symlink for {device_name}: {e}")
        return None

def cleanup_virtual_device(device_name, fd):
    """
    Cleans up a previously created virtual device by:
      1. Removing the symlink /dev/tty{device_name}.
      2. Removing the request file if it exists in the requests directory.
      3. Closing the master file descriptor.
      4. Removing the device from buffer_full_counts.
    """
    # Construct the symlink path: /dev/tty{device_name}
    symlink_path = f"/dev/tty{device_name}"
    # Build the full path to the request file
    request_file_path = os.path.join(requests_directory, device_name)

    # Try removing the symlink
    try:
        subprocess.run(["sudo", "rm", "-f", symlink_path], check=True)
        logger.info(f"Virtual device {symlink_path} removed.")
    except subprocess.CalledProcessError as e:
        logger.critical(f"Virtual device {symlink_path} removal failed: {e}")

    # If a request file with the same name exists, remove it
    if os.path.exists(request_file_path):
        try:
            os.remove(request_file_path)
            logger.info(f"Removed stale request file {request_file_path}")
        except Exception as e:
            logger.error(f"Error removing {request_file_path}: {e}")

    # Close the master file descriptor
    try:
        os.close(fd)
    except Exception:
        pass

    # Remove the device from the buffer-full dictionary
    buffer_full_counts.pop(device_name, None)

def validate_checksum(sentence):
    """
    Validates the NMEA sentence checksum. Returns True if valid, False otherwise.
    NMEA sentences are generally formatted as: $GPGGA,....*CHECKSUM
    """
    try:
        # Separate the sentence into the main body and the checksum part
        sentence_body, checksum_str = sentence.strip().split('*')
        # Strip out the leading '$' character from the body
        sentence_body = sentence_body.lstrip('$')

        # Compute the checksum by XOR'ing the ASCII values of all characters
        calculated_checksum = 0
        for char in sentence_body:
            calculated_checksum ^= ord(char)

        # Convert the provided checksum from hex to an integer, then compare
        return int(checksum_str, 16) == calculated_checksum
    except Exception as e:
        # If there's an error during parsing, log it and return False
        logger.error(f"Checksum error: {e}")
        return False

def convert_to_decimal(degree_min_str, direction):
    """
    Converts an NMEA latitude or longitude string to a decimal degree value.
    Applies negative sign for 'S' or 'W' directions.
    """
    try:
        if direction in ['N', 'S']:
            # For latitude, which typically is in DDMM.MMMM format
            degrees = int(degree_min_str[:2])
            minutes = float(degree_min_str[2:])
        else:
            # For longitude, which typically is in DDDMM.MMMM format
            degrees = int(degree_min_str[:3])
            minutes = float(degree_min_str[3:])

        # Calculate decimal degrees
        decimal_value = degrees + (minutes / 60.0)

        # If direction is South or West, make the value negative
        return -decimal_value if direction in ['S', 'W'] else decimal_value
    except Exception as e:
        logger.error(f"Conversion error for {degree_min_str} {direction}: {e}")
        return None

def parse_gga(sentence):
    """
    Parses a GPGGA sentence for latitude, longitude, altitude, and satellite count.
    Returns a dictionary with those keys if successful, or None if incomplete/invalid.
    """
    try:
        # Split the sentence by commas
        fields = sentence.split(',')
        # We need at least 10 fields for a valid GGA parse
        if len(fields) < 10:
            return None

        # Extract latitude, longitude, satellite count, and altitude from the fields
        latitude = convert_to_decimal(fields[2], fields[3]) if fields[2] and fields[3] else None
        longitude = convert_to_decimal(fields[4], fields[5]) if fields[4] and fields[5] else None
        satellites = int(fields[7]) if fields[7] else None
        altitude = float(fields[9]) if fields[9] else None

        return {
            "latitude": latitude,
            "longitude": longitude,
            "altitude": altitude,
            "satellites": satellites
        }
    except Exception as e:
        logger.error(f"GGA parse error: {e}")
        return None

def parse_rmc(sentence):
    """
    Parses a GPRMC sentence for speed (in knots), then converts it to km/h.
    Returns a dict with the key 'speed' if successful, or None if invalid.
    """
    try:
        # Split the sentence by commas
        fields = sentence.split(',')
        # We need at least 8 fields for minimal RMC parse
        if len(fields) < 8:
            return None

        # The speed in knots is typically at index 7
        speed_knots = float(fields[7]) if fields[7] else None
        # Convert knots to km/h (1 knot = 1.852 km/h)
        return {"speed": speed_knots * 1.852 if speed_knots is not None else None}
    except Exception as e:
        logger.error(f"RMC parse error: {e}")
        return None

def monitor_requests():
    """
    Checks for files within the requests_directory that indicate a user wants a new virtual device.
    Any file matching "GPS_*" and not ending with .log or .zip is considered a request.
    - Create a virtual device for each newly discovered request file.
    - Remove devices that are no longer requested.
    """
    # Gather filenames in the requests directory that start with "GPS_" but not .log/.zip
    current_requests = set(
        f for f in os.listdir(requests_directory)
        if f.startswith("GPS_") and not f.endswith((".log", ".zip"))
    )

    # For each request file, if we don't already have a device, create one
    for request_file in current_requests:
        if request_file not in active_devices:
            device_fd = create_virtual_device(request_file)
            if device_fd is not None:
                active_devices[request_file] = device_fd

    # For any device that's active, but not present in the requests anymore, clean it up
    for device_name in list(active_devices.keys()):
        if device_name not in current_requests:
            logger.warning(f"Virtual device {device_name} disconnected, cleaning up.")
            cleanup_virtual_device(device_name, active_devices[device_name])
            active_devices.pop(device_name, None)

def main_loop():
    """
    The main loop that continuously reads data from the UDP socket and manages:
      - Monitoring request files for new/removal of virtual devices
      - Parsing and validating NMEA sentences
      - Writing valid sentences to active devices
      - Logging GPS data to a file (if enabled)
      - Logging periodic information like updates/second and the stable connection status
    """
    # Trackers for timing various operations
    last_check_time = time.time()      # Last time we checked request files
    last_stats_time = time.time()      # Last time we logged updates/second
    last_valid_time = time.time()      # Last time we received a valid NMEA sentence
    last_fix_log_time = time.time()    # Last time we logged fix information

    # New tracker to mark when the connection became stable
    stable_start_time = None           # Time when the connection was re-established or first became stable

    # Counters and states
    valid_update_count = 0            # Total count of valid NMEA sentences processed
    stats_update_count = 0            # Count used to compute updates/second in a window
    error_logged = False              # Whether we've logged a "No GPS" error to avoid spamming
    connection_lost = False           # Whether we've declared the GPS "lost"
    summary_logged = False            # Flag to ensure the "GPS is STABLE" message is logged only once per stable session

    # Data structure for holding the latest GPS fix information
    gps_fix = {
        "latitude": None,
        "longitude": None,
        "altitude": None,
        "satellites": None,
        "speed": None
    }

    # Run indefinitely until the script is terminated or an unrecoverable error occurs
    while True:
        try:
            current_time = time.time()

            # Check for new/unneeded device requests every 2 seconds
            if current_time - last_check_time > 2:
                monitor_requests()
                last_check_time = current_time

            # Attempt to receive data from the UDP socket
            data, addr = udp_socket.recvfrom(BUFFER_SIZE)

            # Decode the raw data into a UTF-8 string, ignoring decode errors
            raw_data = data.decode(errors="ignore").strip()

            # Each UDP packet can contain multiple lines; split them
            for sentence in [line.strip() for line in raw_data.splitlines() if line.strip()]:
                # Check if it starts with '$' (a sign of a valid NMEA sentence)
                if not sentence.startswith('$'):
                    logger.warning(f"Ignored malformed sentence: {sentence}")
                    continue

                # Validate the checksum of the NMEA sentence
                if validate_checksum(sentence):
                    # Update the last_valid_time since we got a good sentence
                    last_valid_time = current_time

                    # If connection was previously lost, log that we have reconnected, and set stable_start_time
                    if connection_lost:
                        logger.success("GPS is UP.")
                        connection_lost = False
                        stable_start_time = current_time  # mark the start of a stable connection period
                        summary_logged = False
                    # If connection was never marked lost and we haven't set stable_start_time, set it now.
                    elif stable_start_time is None:
                        stable_start_time = current_time

                    error_logged = False

                    # If it's a GGA sentence, parse for position, altitude, satellite count
                    if sentence.startswith('$GPGGA'):
                        gga_data = parse_gga(sentence)
                        if gga_data:
                            gps_fix.update(gga_data)

                    # If it's a GPRMC sentence, parse for speed in km/h
                    elif sentence.startswith('$GPRMC'):
                        rmc_data = parse_rmc(sentence)
                        if rmc_data:
                            gps_fix["speed"] = rmc_data.get("speed")

                    # Write the valid sentence to all active pseudo-terminals
                    for device_name, device_fd in list(active_devices.items()):
                        try:
                            # If this device's buffer was previously full, show a warning
                            if buffer_full_counts.get(device_name, 0) > 0:
                                logger.warning(
                                    f"Buffer full on /dev/tty{device_name}, "
                                    f"skipping write ({buffer_full_counts[device_name]}/10)."
                                )

                            # Write NMEA sentence + newline to the device
                            os.write(device_fd, (sentence + "\n").encode())

                            # If successful, reset the buffer-full count
                            buffer_full_counts[device_name] = 0

                        except BlockingIOError:
                            # This indicates the device's buffer is still full
                            logger.warning(
                                f"Buffer full on /dev/tty{device_name}, "
                                f"skipping write ({buffer_full_counts[device_name]}/10)."
                            )
                            buffer_full_counts[device_name] += 1

                            # If the buffer is full too many times, assume device is dead
                            if buffer_full_counts[device_name] > 10:
                                logger.critical(
                                    f"Device /dev/tty{device_name} unresponsive. Cleaning up."
                                )
                                cleanup_virtual_device(device_name, device_fd)
                                active_devices.pop(device_name, None)

                        except OSError as e:
                            # Any other I/O-related errors
                            logger.error(f"Write error on /dev/tty{device_name}: {e}")

                    # If configured to log raw GPS data, append the sentence to the log file
                    if gps_settings["log_gps_data"]:
                        with open(gps_log_path, "a") as gps_log_file:
                            gps_log_file.write(sentence + "\n")

                    # Update counters
                    valid_update_count += 1
                    stats_update_count += 1

                    # Every 5 seconds, log how many updates per second we're getting
                    if current_time - last_stats_time >= 5:
                        updates_per_sec = stats_update_count / (current_time - last_stats_time)
                        logger.info(f"GPS updates per second: {updates_per_sec:.2f}")
                        stats_update_count = 0
                        last_stats_time = current_time

                    # Log position, altitude, satellites, and speed every 5 seconds if we have a recent fix
                    if (current_time - last_valid_time < 30) and (current_time - last_fix_log_time >= 5):
                        latitude = f"{gps_fix['latitude']:.3f}" if gps_fix['latitude'] is not None else "N/A"
                        longitude = f"{gps_fix['longitude']:.3f}" if gps_fix['longitude'] is not None else "N/A"
                        altitude = f"{gps_fix['altitude']:.3f}" if gps_fix['altitude'] is not None else "N/A"
                        satellites = gps_fix['satellites'] if gps_fix['satellites'] is not None else "N/A"
                        speed = f"{gps_fix['speed']:.3f}" if gps_fix['speed'] is not None else "N/A"

                        logger.info(f"Latitude: {latitude}")
                        logger.info(f"Longitude: {longitude}")
                        logger.info(f"Altitude: {altitude} m")
                        logger.info(f"Satellites: {satellites}")
                        logger.info(f"Speed: {speed} km/h")

                        last_fix_log_time = current_time

                else:
                    # If checksum is invalid, log a warning
                    logger.warning(f"Invalid checksum: {sentence}")

            # Log a TRACE message with the current fix details
            logger.trace(" | ".join([
                f"Latitude: {gps_fix['latitude']:.3f}" if gps_fix['latitude'] is not None else "Latitude: N/A",
                f"Longitude: {gps_fix['longitude']:.3f}" if gps_fix['longitude'] is not None else "Longitude: N/A",
                f"Altitude: {gps_fix['altitude']:.3f} m" if gps_fix['altitude'] is not None else "Altitude: N/A",
                f"Satellites: {gps_fix['satellites']}" if gps_fix['satellites'] is not None else "Satellites: N/A",
                f"Speed: {gps_fix['speed']:.3f} km/h" if gps_fix['speed'] is not None else "Speed: N/A"
            ]))

        except socket.timeout:
            # Handle socket timeout errors
            current_time = time.time()
            # If no valid data for 30+ seconds, log error once and mark connection as lost
            if current_time - last_valid_time >= 30:
                if not error_logged:
                    logger.error("No GPS connection for more than 30 seconds.")
                    error_logged = True
                    connection_lost = True
                    stable_start_time = None  # reset stable start since connection is lost
                    summary_logged = False
            else:
                # If connection is not already marked as lost, log the drop warning only once
                if not connection_lost:
                    logger.warning("GPS is DOWN.")
                    connection_lost = True
                    stable_start_time = None  # reset stable start on connection drop
                    summary_logged = False
            continue

        except KeyboardInterrupt:
            # Allow the user to gracefully kill the script (CTRL+C)
            logger.success("Terminated by user.")
            sys.exit(0)

        except Exception as e:
            # Catch-all for any other unexpected error
            logger.critical(f"Unexpected error: {e}")
            sys.exit(1)

        # If the connection is not lost and has been stable for at least 10 seconds,
        # log "GPS is STABLE" once per stable session.
        if (not connection_lost) and (stable_start_time is not None) and (time.time() - stable_start_time >= 10) and (not summary_logged):
            logger.success("GPS is STABLE")
            summary_logged = True

if __name__ == "__main__":
    # Indicate the script is starting
    logger.success("GPS Logger started.")
    # Start the main loop
    main_loop()
