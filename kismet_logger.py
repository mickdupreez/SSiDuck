#!/usr/bin/env python3
import os
import subprocess
import json
import sys
import time
import signal
import psutil
from loguru import logger

error_flag=False
warning_flag=False
success_flag=False
critical_flag=False

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

def load_settings(file_path="kismet_settings.json"):
    with open(file_path,"r") as file:
        return json.load(file)

settings=load_settings()
kismet_settings=settings["KISMET_SETTINGS"]
logging_settings=settings["LOGGING_SETTINGS"]
log_dir=os.path.join(os.getcwd(),"logs","wardrive_logs")
kismet_log_path=os.path.join(os.getcwd(),"logs","kismet_logs","kismet_data.log")
log_file_path=os.path.join(os.getcwd(),"logs","kismet_logs","kismet_monitor.log")
gps_monitor_log_path=os.path.join(os.getcwd(),"logs","gps_logs","gps_monitor.log")
kismet_launch_args="-c wlan1 --no-remote"
run_as_sudo=False
KILL_RETRY_ATTEMPTS=3
KILL_RETRY_WAIT_SECONDS=2
SKIP_KILL_SELF=True
gps_device_name=kismet_settings["gps_device_name"]
request_file_path=os.path.join(os.getcwd(),"logs","gps_logs","gps_devices",gps_device_name)

logger.remove()
logger_format="<yellow>{time:DD/MM @ HH:mm:ss.SSS} </yellow><blue>|</blue><level>{level:^9}</level><blue>|</blue><magenta> KISMET </magenta><blue>|</blue> <cyan>{message}</cyan>"
logger.add(sys.stderr,level=logging_settings["log_level"].upper(),colorize=True,format=logger_format)
if os.path.exists(log_file_path):
    open(log_file_path,"w").close()
logger.add(log_file_path,level=logging_settings["log_level"].upper(),format=logger_format)

kismet_process=None
last_killed_pid=None

def cleanup_old_logs():
    for filename in ["kismet.kismet","kismet.wiglecsv"]:
        file_path=os.path.join(log_dir,filename)
        if os.path.exists(file_path):
            os.remove(file_path)

def kill_existing_kismet():
    global last_killed_pid
    current_pid=os.getpid()
    for _ in range(KILL_RETRY_ATTEMPTS):
        found_processes=False
        for proc in psutil.process_iter(["pid","name","cmdline"]):
            try:
                proc_name=(proc.info["name"] or "").lower()
                proc_cmd=[arg.lower() for arg in (proc.info["cmdline"] or [])]
                if "tail" in proc_name or any("tail" in arg for arg in proc_cmd):
                    continue
                if "kismet" in proc_name or any("kismet" in arg for arg in proc_cmd):
                    if SKIP_KILL_SELF and proc.pid==current_pid:
                        continue
                    if last_killed_pid is not None and proc.pid==last_killed_pid:
                        continue
                    found_processes=True
                    subprocess.run(f"sudo kill -9 {proc.pid}",shell=True,check=False)
            except:
                continue
        if found_processes:
            time.sleep(KILL_RETRY_WAIT_SECONDS)
        else:
            break

def wait_for_gps_device():
    device_path=f"/dev/tty{gps_device_name}"
    while not os.path.exists(device_path):
        time.sleep(1)

def update_kismet_conf(gps_device_name):
    kismet_conf_path="/etc/kismet/kismet.conf"
    backup_path="/etc/kismet/kismet.conf.bak"
    subprocess.run(["sudo","cp",kismet_conf_path,backup_path],check=False)
    result=subprocess.run(["sudo","cat",kismet_conf_path],check=True,stdout=subprocess.PIPE,text=True)
    conf_lines=result.stdout.splitlines()
    updated_lines=[]
    replaced=False
    for line in conf_lines:
        if line.strip().startswith("gps=serial:device="):
            updated_lines.append(f"gps=serial:device=/dev/tty{gps_device_name},name=gps_logger")
            replaced=True
        else:
            updated_lines.append(line)
    if not replaced:
        updated_lines.append(f"gps=serial:device=/dev/tty{gps_device_name},name=gps_logger")
    temp_conf_path="/tmp/kismet.conf.updated"
    with open(temp_conf_path,"w") as temp_conf:
        temp_conf.write("\n".join(updated_lines)+"\n")
    subprocess.run(["sudo","mv",temp_conf_path,kismet_conf_path],check=True)

def start_kismet():
    global kismet_process,last_killed_pid
    kill_existing_kismet()
    cleanup_old_logs()
    os.makedirs(log_dir,exist_ok=True)
    open(request_file_path,"a").close()
    wait_for_gps_device()
    update_kismet_conf(gps_device_name)
    kismet_args=kismet_launch_args.split()
    cmd=(["sudo"] if run_as_sudo else [])+["kismet"]+kismet_args+["--log-prefix",log_dir,"--override","wardrive"]
    with open(kismet_log_path,"w") as kismet_log_file:
        kismet_process=subprocess.Popen(cmd,stdout=kismet_log_file,stderr=subprocess.STDOUT,preexec_fn=os.setsid)
    set_log_level("success","WARDRIVING.")
    last_killed_pid=None
    return kismet_process

def monitor_gps_connection():
    global kismet_process,last_killed_pid
    gps_stable_prev=None
    keywords=["SUCCESS","WARNING","ERROR","CRITICAL"]
    while True:
        try:
            with open(gps_monitor_log_path,"r") as gps_file:
                lines=gps_file.readlines()
            if lines:
                found_line=None
                for line in reversed(lines):
                    if any(keyword in line for keyword in keywords):
                        found_line=line.strip()
                        break
                if found_line is None:
                    found_line=lines[-1].strip()
            else:
                found_line=""
        except:
            found_line=""
        if "SUCCESS" in found_line:
            gps_stable_prev="SUCCESS"
            if kismet_process is None or kismet_process.poll() is not None:
                kismet_process=start_kismet()
                last_killed_pid=None
        elif "WARNING" in found_line:
            if gps_stable_prev!="WARNING":
                set_log_level("warning","INITIALIZING.")
            gps_stable_prev="WARNING"
            if kismet_process is not None and kismet_process.poll() is None and (last_killed_pid is None or kismet_process.pid!=last_killed_pid):
                try:
                    os.killpg(kismet_process.pid,signal.SIGINT)
                except:
                    pass
                last_killed_pid=kismet_process.pid
                kismet_process=None
                cleanup_old_logs()
        elif "ERROR" in found_line:
            if gps_stable_prev!="ERROR":
                set_log_level("error","STOPPING, NO GPS DATA.")
            gps_stable_prev="ERROR"
            if kismet_process is not None and kismet_process.poll() is None and (last_killed_pid is None or kismet_process.pid!=last_killed_pid):
                try:
                    os.killpg(kismet_process.pid,signal.SIGINT)
                except:
                    pass
                last_killed_pid=kismet_process.pid
                kismet_process=None
                cleanup_old_logs()
        elif "CRITICAL" in found_line:
            if gps_stable_prev!="CRITICAL":
                set_log_level("critical","STOPPED, NO GPS DEVICE.")
            gps_stable_prev="CRITICAL"
            if kismet_process is not None and kismet_process.poll() is None and (last_killed_pid is None or kismet_process.pid!=last_killed_pid):
                try:
                    os.killpg(kismet_process.pid,signal.SIGINT)
                except:
                    pass
                last_killed_pid=kismet_process.pid
                kismet_process=None
                cleanup_old_logs()
        time.sleep(1)

def cleanup():
    if os.path.exists(request_file_path):
        os.remove(request_file_path)

def handle_exit(sig,frame):
    global kismet_process
    if kismet_process is not None:
        try:
            os.killpg(kismet_process.pid,signal.SIGINT)
        except:
            pass
    cleanup()
    set_log_level("critical","STOPPED, KEYBOARD INTERRUPT.")
    sys.exit(0)

if __name__=="__main__":
    signal.signal(signal.SIGINT,handle_exit)
    signal.signal(signal.SIGTERM,handle_exit)
    monitor_gps_connection()
    cleanup()
