#!/usr/bin/env python3
import os
import sys
import time
import signal
import psutil
import subprocess
import select
import re
import fcntl
import json
from datetime import datetime
from loguru import logger

error_flag=False
warning_flag=False
success_flag=False
critical_flag=False
bettercap_data_fd=None

# Add these constants at the top after imports
SETTINGS_FILE = os.path.join(os.getcwd(), "bettercap_settings.json")
MAX_LOG_LINES = 10000

# Add these global variables after the existing ones
gps_latitude = None
gps_longitude = None
gps_altitude = None
gps_accuracy = None
request_file_path = None

def set_log_level(level,msg):
    global error_flag,warning_flag,success_flag,critical_flag
    if level=="trace":
        logger.trace(msg)
    elif level=="debug":
        logger.debug(msg)
    elif level=="info":
        logger.info(msg)
    elif level=="error" and not error_flag:
        logger.error(msg)
        error_flag=True
        warning_flag=False
        success_flag=False
        critical_flag=False
    elif level=="warning" and not warning_flag:
        logger.warning(msg)
        warning_flag=True
        error_flag=False
        success_flag=False
        critical_flag=False
    elif level=="success" and not success_flag:
        logger.success(msg)
        success_flag=True
        error_flag=False
        warning_flag=False
        critical_flag=False
    elif level=="critical" and not critical_flag:
        logger.critical(msg)
        critical_flag=True
        error_flag=False
        warning_flag=False
        success_flag=False

SKIP_KILL_SELF=True
KILL_RETRY_ATTEMPTS=3
KILL_RETRY_WAIT_SECONDS=2
IGNORE_TAIL=True
last_killed_pid=None
gps_monitor_log_path=os.path.join(os.getcwd(),"logs","gps_logs","gps_monitor.log")
bettercap_log_dir=os.path.join(os.getcwd(),"logs","bettercap_logs")
wardrive_log_dir=os.path.join(os.getcwd(),"logs","wardrive_logs")
log_file_path=os.path.join(bettercap_log_dir,"bettercap_monitor.log")
bettercap_data_path=os.path.join(bettercap_log_dir,"bettercap_data.log")
pty_fd=None
bettercap_process=None
best_entries={}
ansi_escape=re.compile(r'\x1B\[[0-?]*[ -/]*[@-~]')
logger.remove()
logger_format="<yellow>{time:DD/MM @ HH:mm:ss.SSS} </yellow><blue>|</blue><level>{level:^9}</level><blue>|</blue><magenta> BETTERCAP </magenta><blue>|</blue> <cyan>{message}</cyan>"

def load_settings():
    """Load settings from configuration file"""
    try:
        with open(SETTINGS_FILE, 'r') as f:
            settings = json.load(f)
        return settings
    except Exception as e:
        logger.error(f"Failed to load settings: {e}")
        return {
            "BETTERCAP_SETTINGS": {
                "gps_device_name": "GPS_BETTERCAP"
            },
            "LOGGING_SETTINGS": {
                "log_level": "INFO"
            }
        }

def kill_existing_bettercap():
    global last_killed_pid
    current_pid=os.getpid()
    for _ in range(KILL_RETRY_ATTEMPTS):
        found_processes=False
        for proc in psutil.process_iter(["pid","name","cmdline"]):
            try:
                n=(proc.info["name"] or "").lower()
                c=[a.lower() for a in (proc.info["cmdline"] or [])]
                if IGNORE_TAIL and ("tail" in n or any("tail" in x for x in c)):
                    continue
                if "bettercap" in n or any("bettercap" in x for x in c):
                    if SKIP_KILL_SELF and proc.pid==current_pid:
                        continue
                    if last_killed_pid is not None and proc.pid==last_killed_pid:
                        continue
                    found_processes=True
                    subprocess.run(f"sudo kill -9 {proc.pid}",shell=True,check=False)
            except:
                pass
        if found_processes:
            time.sleep(KILL_RETRY_WAIT_SECONDS)
        else:
            break

def cleanup_old_csv():
    p=os.path.join(wardrive_log_dir,"bettercap.wiglecsv")
    if os.path.exists(p):
        os.remove(p)

def extract_box(text):
    lines=text.splitlines()
    s=None
    e=None
    for i,l in enumerate(lines):
        if l.startswith("┌") or l.startswith("╔") or (l.startswith("+") and "-" in l):
            s=i
            break
    if s is not None:
        for j in range(s,len(lines)):
            if lines[j].startswith("└") or lines[j].startswith("╝") or (lines[j].startswith("+") and "-" in lines[j]):
                e=j
                break
        if e is None:
            e=len(lines)-1
        return "\n".join(lines[s:e+1])
    return text

def parse_box_data(box_text):
    rows=[]
    lines=box_text.splitlines()
    t=None
    for i,l in enumerate(lines):
        if l.startswith("├") or l.startswith("╟"):
            t=i
            break
        if (l.startswith("+") and "-" in l and "┬" not in l) or "┼" in l:
            t=i
            break
    if t is None:
        return rows
    d=[]
    for l in lines[t+1:]:
        if l.startswith("└") or l.startswith("╚") or (l.startswith("+") and "-" in l):
            break
        if "│" in l:
            d.append(l)
    for dl in d:
        p=dl.split("│")
        if len(p)<7:
            continue
        r=p[1].strip()
        m=p[2].strip()
        v=p[3].strip()
        f=p[4].strip()
        s=p[6].strip()
        rows.append({"RSSI":r,"MAC":m,"Vendor":v,"Flags":f,"Seen":s})
    return rows

def parse_seen_time(s):
    try:
        n = datetime.now()
        hh, mm, ss = s.split(":")
        # Create a datetime with current date and the seen time
        return n.replace(hour=int(hh), minute=int(mm), second=int(ss), microsecond=0).strftime("%Y-%m-%d %H:%M:%S")
    except:
        return datetime.max.strftime("%Y-%m-%d %H:%M:%S")

def update_best_entries(new_rows):
    global best_entries
    u=False
    for r in new_rows:
        m=r["MAC"]
        try:
            nr=float(r["RSSI"].lower().replace("dbm","").strip())
        except:
            nr=-9999.0
        if m not in best_entries:
            best_entries[m]=r
            u=True
        else:
            try:
                orv=float(best_entries[m]["RSSI"].lower().replace("dbm","").strip())
            except:
                orv=-9999.0
            if nr>orv:
                best_entries[m]=r
                u=True
    return u

def parse_gps_data(text):
    global gps_latitude, gps_longitude, gps_altitude, gps_accuracy
    try:
        # Look for the GPS line that looks like:
        # latitude:-38.193832 longitude:144.471814 quality:1 satellites:8 altitude:43.000000
        gps_line = re.search(r'latitude:([-.0-9]+)\s+longitude:([-.0-9]+).*?altitude:([-.0-9]+)', text)
        
        if gps_line:
            gps_latitude = float(gps_line.group(1))
            gps_longitude = float(gps_line.group(2))
            gps_altitude = float(gps_line.group(3))
            
            # Try to get quality/satellites for accuracy
            quality_match = re.search(r'quality:(\d+)', text)
            satellites_match = re.search(r'satellites:(\d+)', text)
            
            if quality_match and satellites_match:
                # Use a combination of quality and satellites as accuracy
                quality = int(quality_match.group(1))
                satellites = int(satellites_match.group(1))
                gps_accuracy = 100.0 / (quality * satellites) if quality * satellites > 0 else 100.0
            
            return True
    except Exception as e:
        logger.error(f"Failed to parse GPS data: {e}")
    return False

def write_output_csv():
    global best_entries, gps_latitude, gps_longitude, gps_altitude, gps_accuracy
    p = os.path.join(wardrive_log_dir, "bettercap.wiglecsv")
    def k(i):
        return parse_seen_time(i[1]["Seen"])
    s = sorted(best_entries.items(), key=k)
    try:
        with open(p, "w", encoding="utf-8") as f:
            # Write Wigle header
            f.write("WigleWifi-1.4,appRelease=Bettercap,model=Bettercap,release=1.0,device=bettercap,display=bettercap,board=bettercap,brand=bettercap\n")
            f.write("MAC,SSID,AuthMode,FirstSeen,Channel,RSSI,CurrentLatitude,CurrentLongitude,AltitudeMeters,AccuracyMeters,Type\n")
            # Write data
            for m, r in s:
                # Extract RSSI value and remove commas
                try:
                    rssi = float(r["RSSI"].lower().replace("dbm", "").strip())
                except:
                    rssi = 0
                
                # Clean all fields by removing commas
                mac = r['MAC'].replace(',', '')
                vendor = r['Vendor'].replace(',', '')
                flags = r['Flags'].replace(',', '')
                seen_time = parse_seen_time(r['Seen'])
                
                # Format the line with cleaned data
                f.write(f"{mac},{vendor},{flags},{seen_time},,{rssi},{gps_latitude or ''},{gps_longitude or ''},{gps_altitude or ''},{gps_accuracy or ''},BLE\n")
    except Exception as e:
        logger.error(f"Failed to write CSV: {e}")

def wait_for_gps_device():
    device_path = f"/dev/tty{settings['BETTERCAP_SETTINGS']['gps_device_name']}"
    while not os.path.exists(device_path):
        time.sleep(1)

def start_bettercap():
    global bettercap_process, pty_fd, best_entries, last_killed_pid, bettercap_data_fd, request_file_path
    settings = load_settings()
    gps_device = settings["BETTERCAP_SETTINGS"]["gps_device_name"]
    
    # Create GPS device request file
    request_file_path = os.path.join(os.getcwd(), "logs", "gps_logs", "gps_devices", gps_device)
    os.makedirs(os.path.dirname(request_file_path), exist_ok=True)
    open(request_file_path, "a").close()
    
    # Wait for GPS device to be created
    wait_for_gps_device()
    
    kill_existing_bettercap()
    cleanup_old_csv()
    last_killed_pid = None
    best_entries = {}
    os.makedirs(bettercap_log_dir, exist_ok=True)
    os.makedirs(wardrive_log_dir, exist_ok=True)
    
    # Delete existing bettercap_data.log file
    if os.path.exists(bettercap_data_path):
        os.remove(bettercap_data_path)
    
    pty_master, slave_fd = os.openpty()
    fl = fcntl.fcntl(pty_master, fcntl.F_GETFL)
    fcntl.fcntl(pty_master, fcntl.F_SETFL, fl | os.O_NONBLOCK)
    
    try:
        bettercap_process = subprocess.Popen(
            ["sudo", "bettercap", "--no-colors"],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            universal_newlines=False,
            bufsize=0,
            preexec_fn=os.setsid
        )
    except:
        return
        
    os.close(slave_fd)
    time.sleep(1)
    
    try:
        os.write(pty_master, f"set gps.device /dev/tty{gps_device}\n".encode())
        os.write(pty_master, b"gps on\n")
        os.write(pty_master, b"ble.recon on\n")
    except:
        pass
        
    pty_fd = pty_master
    set_log_level("success", "WARDRIVING")

def stop_bettercap():
    global bettercap_process,pty_fd,last_killed_pid,bettercap_data_fd
    if bettercap_process is not None and bettercap_process.poll() is None:
        try:
            os.write(pty_fd,b"quit\n")
            time.sleep(1)
            bettercap_process.terminate()
            last_killed_pid=bettercap_process.pid
        except:
            pass
    if bettercap_data_fd:
        bettercap_data_fd.close()
    bettercap_data_fd=None
    bettercap_process=None
    pty_fd=None
    # Add cleanup of wiglecsv file when bettercap stops
    cleanup_old_csv()

def rotate_log_file(new_content):
    global bettercap_data_path
    try:
        # Read existing lines if file exists
        lines = []
        if os.path.exists(bettercap_data_path):
            with open(bettercap_data_path, 'r') as f:
                lines = f.readlines()
        
        # Add new content
        new_lines = new_content.splitlines(True)  # Keep line endings
        lines.extend(new_lines)
        
        # Keep only last MAX_LOG_LINES
        if len(lines) > MAX_LOG_LINES:
            lines = lines[-MAX_LOG_LINES:]
        
        # Write back to file
        with open(bettercap_data_path, 'w') as f:
            f.writelines(lines)
    except Exception as e:
        logger.error(f"Failed to rotate log file: {e}")

def handle_bettercap_output():
    if bettercap_process is None or bettercap_process.poll() is not None or pty_fd is None:
        return
    try:
        # Get GPS data first
        os.write(pty_fd, b"gps.show\n")
        time.sleep(0.1)  # Give time for GPS data to be received
        # Then get BLE data
        os.write(pty_fd, b"ble.show\n")
    except:
        return
    c = b""
    t = time.time() + 0.2
    while time.time() < t:
        r, _, _ = select.select([pty_fd], [], [], 0.05)
        if pty_fd in r:
            try:
                x = os.read(pty_fd, 1024)
                if x:
                    c += x
                    t = time.time() + 0.2
            except:
                break
    txt = c.decode("utf-8", errors="replace")
    
    # Replace direct file write with rotate_log_file
    rotate_log_file(txt)
    
    # Parse GPS data first
    parse_gps_data(txt)
    
    ct = ansi_escape.sub("", txt)
    bxt = extract_box(ct)
    nr = parse_box_data(bxt)
    if nr:
        ch = update_best_entries(nr)
        if ch:
            write_output_csv()

def cleanup():
    global request_file_path
    if request_file_path and os.path.exists(request_file_path):
        os.remove(request_file_path)
    cleanup_old_csv()

def monitor_gps_connection():
    g=None
    while True:
        l=""
        try:
            with open(gps_monitor_log_path,"r") as f:
                lines=f.readlines()
            if lines:
                for line in reversed(lines):
                    if "SUCCESS" in line or "WARNING" in line or "ERROR" in line or "CRITICAL" in line:
                        l=line.strip()
                        break
                if not l:
                    l=lines[-1].strip()
        except:
            pass
        if "SUCCESS" in l:
            if g!="SUCCESS":
                start_bettercap()
            g="SUCCESS"
        elif "WARNING" in l:
            if g!="WARNING":
                set_log_level("warning","GPS INITIALIZING")
                stop_bettercap()
            g="WARNING"
        elif "ERROR" in l:
            if g!="ERROR":
                set_log_level("error","NO GPS DEVICES")
                stop_bettercap()
            g="ERROR"
        elif "CRITICAL" in l:
            if g!="CRITICAL":
                set_log_level("critical","GPS IS NOT RUNNING")
                stop_bettercap()
            g="CRITICAL"
        if g=="SUCCESS":
            handle_bettercap_output()
        time.sleep(1)

def handle_exit(sig,frame):
    stop_bettercap()
    cleanup()
    set_log_level("critical","STOPPED, KEYBOARD INTERRUPT.")
    sys.exit(0)

signal.signal(signal.SIGINT,handle_exit)
signal.signal(signal.SIGTERM,handle_exit)

if __name__=="__main__":
    settings = load_settings()
    log_level = settings["LOGGING_SETTINGS"]["log_level"]
    logger.remove()
    logger.add(sys.stderr, level=log_level, colorize=True, format=logger_format)
    logger.add(log_file_path, level=log_level, format=logger_format, mode="w")
    
    monitor_gps_connection()
