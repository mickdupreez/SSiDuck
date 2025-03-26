#!/usr/bin/env python3
import socket
import json
import os
import sys
import pty
import subprocess
import fcntl
import time
from loguru import logger

error_flag = False
warning_flag = False
success_flag = False
critical_flag = False

def set_log_level(level,msg):
    global error_flag,warning_flag,success_flag,critical_flag
    if level=="error" and not error_flag:
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

def load_settings(file_path="gps_settings.json"):
    default_settings={"GPS_SETTINGS":{"udp_ip":"172.20.10.3","udp_port":11123},"LOGGING_SETTINGS":{"log_level":"trace"}}
    try:
        with open(file_path,"r") as f:
            return json.load(f)
    except (FileNotFoundError,json.JSONDecodeError):
        print(f"Settings file '{file_path}' missing/invalid. Creating default...")
        try:
            with open(file_path,"w") as f:
                json.dump(default_settings,f,indent=2)
            print(f"Default settings written to '{file_path}'.")
            return default_settings
        except Exception as e:
            print(f"Failed to create settings file: {e}")
            sys.exit(1)
    except Exception as e:
        print(f"Unexpected error: {e}")
        sys.exit(1)

settings=load_settings()
gps_settings=settings["GPS_SETTINGS"]
logging_settings=settings["LOGGING_SETTINGS"]
gps_settings["buffer_size"]=53248
gps_settings["socket_timeout_sec"]=1
gps_settings["log_gps_data"]=True
gps_settings["gps_log_path"]=os.path.join(os.getcwd(),"logs","gps_logs","gps_data.log")
gps_settings["requests_dir"]=os.path.join(os.getcwd(),"logs","gps_logs","gps_devices")
logging_settings["log_to_file"]=True
logging_settings["log_to_terminal"]=True
logging_settings["log_file_path"]=os.path.join(os.getcwd(),"logs","gps_logs","gps_monitor.log")
gps_log_path=os.path.expanduser(gps_settings["gps_log_path"])
log_file_path=os.path.expanduser(logging_settings["log_file_path"])
requests_directory=os.path.expanduser(gps_settings["requests_dir"])
os.makedirs(requests_directory,exist_ok=True)
logger.remove()
logger_format="<yellow>{time:DD/MM @ HH:mm:ss.SSS} </yellow><blue>|</blue><level>{level:^9}</level><blue>|</blue><magenta> GPS </magenta><blue>|</blue> <cyan>{message}</cyan>"
logger.add(log_file_path,level=logging_settings["log_level"].upper(),format=logger_format)
logger.add(sys.stderr,level=logging_settings["log_level"].upper(),colorize=True,format=logger_format)
for p in [log_file_path,gps_log_path]:
    if os.path.exists(p):
        open(p,"w").close()

UDP_IP=gps_settings["udp_ip"]
UDP_PORT=gps_settings["udp_port"]
BUFFER_SIZE=gps_settings["buffer_size"]
SOCKET_TIMEOUT=gps_settings["socket_timeout_sec"]
udp_socket=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
attempt_interval=1
active_devices={}
buffer_full_counts={}
global_gps_warning_time=None

def create_virtual_device(device_name):
    try:
        master_fd,slave_fd=pty.openpty()
        device_path=os.ttyname(slave_fd)
        symlink_path=f"/dev/tty{device_name}"
        subprocess.run(["sudo","ln","-sf",device_path,symlink_path],check=True)
        flags=fcntl.fcntl(master_fd,fcntl.F_GETFL)
        fcntl.fcntl(master_fd,fcntl.F_SETFL,flags|os.O_NONBLOCK)
        buffer_full_counts[device_name]=0
        return master_fd
    except subprocess.CalledProcessError as e:
        set_log_level("error",f"VIRTUAL DEVICE {device_name} CREATION FAILED: {e}")
        return None

def cleanup_virtual_device(device_name,fd):
    symlink_path=f"/dev/tty{device_name}"
    request_file_path=os.path.join(requests_directory,device_name)
    try:
        subprocess.run(["sudo","rm","-f",symlink_path],check=True)
    except subprocess.CalledProcessError as e:
        set_log_level("error",f"VIRTUAL DEVICE {symlink_path} REMOVAL FAILED: {e}")
    if os.path.exists(request_file_path):
        try:
            os.remove(request_file_path)
        except Exception as e:
            set_log_level("error",f"CANT REMOVE {request_file_path}: {e}")
    try:
        os.close(fd)
    except Exception:
        pass
    buffer_full_counts.pop(device_name,None)

def validate_checksum(sentence):
    try:
        sentence_body,checksum_str=sentence.strip().split('*')
        sentence_body=sentence_body.lstrip('$')
        calculated_checksum=0
        for char in sentence_body:
            calculated_checksum^=ord(char)
        return int(checksum_str,16)==calculated_checksum
    except Exception as e:
        set_log_level("error",f"FILE ERROR {e}")
        return False

def convert_to_decimal(degree_min_str,direction):
    try:
        if direction in ["N","S"]:
            degrees=int(degree_min_str[:2])
            minutes=float(degree_min_str[2:])
        else:
            degrees=int(degree_min_str[:3])
            minutes=float(degree_min_str[3:])
        decimal_value=degrees+(minutes/60.0)
        return -decimal_value if direction in ["S","W"] else decimal_value
    except Exception as e:
        set_log_level("error",f"DATA MANIPULATION ERROR: {degree_min_str} {direction}: {e}")
        return None

def parse_gga(sentence):
    try:
        fields=sentence.split(',')
        if len(fields)<10:
            return None
        latitude=convert_to_decimal(fields[2],fields[3]) if fields[2] and fields[3] else None
        longitude=convert_to_decimal(fields[4],fields[5]) if fields[4] and fields[5] else None
        satellites=int(fields[7]) if fields[7] else None
        altitude=float(fields[9]) if fields[9] else None
        return {"latitude":latitude,"longitude":longitude,"altitude":altitude,"satellites":satellites}
    except Exception as e:
        set_log_level("error",f"GGA PARSE ERROR: {e}")
        return None

def parse_rmc(sentence):
    try:
        fields=sentence.split(',')
        if len(fields)<8:
            return None
        speed_knots=float(fields[7]) if fields[7] else None
        return {"speed":speed_knots*1.852 if speed_knots is not None else None}
    except Exception as e:
        set_log_level("error",f"RMC PARSE ERROR: {e}")
        return None

def monitor_requests():
    current_requests=set(f for f in os.listdir(requests_directory) if f.startswith("GPS_") and not f.endswith((".log",".zip")))
    for request_file in current_requests:
        if request_file not in active_devices:
            device_fd=create_virtual_device(request_file)
            if device_fd is not None:
                active_devices[request_file]=device_fd
    for device_name in list(active_devices.keys()):
        if device_name not in current_requests:
            cleanup_virtual_device(device_name,active_devices[device_name])
            active_devices.pop(device_name,None)

def main_loop():
    global udp_socket,global_gps_warning_time
    last_check_time=time.time()
    last_valid_time=time.time()
    stable_start_time=None
    connection_lost=False
    while True:
        try:
            current_time=time.time()
            if current_time-last_check_time>2:
                monitor_requests()
                last_check_time=current_time
            data,addr=udp_socket.recvfrom(BUFFER_SIZE)
            raw_data=data.decode(errors="ignore").strip()
            for sentence in [line.strip() for line in raw_data.splitlines() if line.strip()]:
                if not sentence.startswith('$'):
                    set_log_level("warning","MALFORMED SENTENCE IGNORED.")
                    continue
                if validate_checksum(sentence):
                    last_valid_time=current_time
                    global_gps_warning_time=None
                    if connection_lost:
                        connection_lost=False
                        stable_start_time=current_time
                    elif stable_start_time is None:
                        stable_start_time=current_time
                    if sentence.startswith('$GPGGA'):
                        gga_data=parse_gga(sentence)
                        if gga_data:
                            pass
                    elif sentence.startswith('$GPRMC'):
                        rmc_data=parse_rmc(sentence)
                        if rmc_data:
                            pass
                    for device_name,device_fd in list(active_devices.items()):
                        if buffer_full_counts.get(device_name,0)>=10:
                            continue
                        try:
                            os.write(device_fd,(sentence+"\n").encode())
                            buffer_full_counts[device_name]=0
                        except BlockingIOError:
                            buffer_full_counts[device_name]+=1
                            if buffer_full_counts[device_name]>=10:
                                cleanup_virtual_device(device_name,device_fd)
                                active_devices.pop(device_name,None)
                        except OSError as e:
                            set_log_level("error",f"{device_name}: ERROR: {e}")
                    if gps_settings["log_gps_data"]:
                        with open(gps_log_path,"a") as gps_log_file:
                            gps_log_file.write(sentence+"\n")
                else:
                    set_log_level("warning",f"SOMETHING DOESN'T LOOK RIGHT {sentence}")
        except socket.timeout:
            current_time=time.time()
            if current_time-last_valid_time>=10:
                if global_gps_warning_time is None:
                    global_gps_warning_time=current_time
                    set_log_level("warning","UNSTABLE.")
                elif current_time-global_gps_warning_time>=30:
                    set_log_level("error","NO GPS DATA.")
                try:
                    udp_socket.close()
                except Exception:
                    pass
                udp_socket=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
                while True:
                    try:
                        udp_socket.bind((UDP_IP,UDP_PORT))
                        udp_socket.settimeout(SOCKET_TIMEOUT)
                        break
                    except OSError as e:
                        if e.errno==99 and UDP_IP!="0.0.0.0":
                            set_log_level("critical",f"CONNECTION: {UDP_IP} LOST.")
                            time.sleep(attempt_interval)
                return
            else:
                if not connection_lost:
                    connection_lost=True
                continue
        except KeyboardInterrupt:
            set_log_level("critical","CLOSING CONNECTION.")
            sys.exit(0)
        except Exception as e:
            set_log_level("critical",f"CLOSING CONNECTION: {e}")
            sys.exit(1)
        if (not connection_lost) and (stable_start_time is not None) and (time.time()-stable_start_time>=5) and (not success_flag):
            set_log_level("success","STABLE.")

if __name__=="__main__":
    try:
        while True:
            try:
                udp_socket.bind((UDP_IP,UDP_PORT))
                udp_socket.settimeout(SOCKET_TIMEOUT)
                set_log_level("warning","INITIALIZING.")
                break
            except OSError as e:
                if e.errno==99 and UDP_IP!="0.0.0.0":
                    set_log_level("critical","CONNECTION ERROR.")
                    time.sleep(attempt_interval)
        while True:
            main_loop()
    except KeyboardInterrupt:
        set_log_level("critical","CLOSING CONNECTION.")
        sys.exit(0)
    except Exception as e:
        set_log_level("critical",f"UNEXPECTED ERROR: {e}")
        sys.exit(1)
