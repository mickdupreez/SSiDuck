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
from datetime import datetime
from loguru import logger

error_flag=False
warning_flag=False
success_flag=False
critical_flag=False
bettercap_data_fd=None

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
logger.add(sys.stderr,level="INFO",colorize=True,format=logger_format)
logger.add(log_file_path,level="INFO",format=logger_format,mode="w")

def kill_existing_bettercap():
    global last_killed_pid
    current_pid=os.getpid()
    for _ in range(KILL_RETRY_ATTEMPTS):
        found_processes=False
        for proc in psutil.process_iter(['pid','name','cmdline']):
            try:
                n=(proc.info['name']or'').lower()
                c=[a.lower()for a in(proc.info['cmdline']or[])]
                if IGNORE_TAIL and('tail'in n or any('tail'in x for x in c)):
                    continue
                if 'bettercap'in n or any('bettercap'in x for x in c):
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
        u=os.path.join(wardrive_log_dir,"processing")
        os.makedirs(u,exist_ok=True)
        os.rename(p,os.path.join(u,"bettercap.wiglecsv"))

def extract_box(text):
    lines=text.splitlines()
    s=None
    e=None
    for i,l in enumerate(lines):
        if l.startswith("┌")or l.startswith("╔")or(l.startswith("+")and"-"in l):
            s=i
            break
    if s is not None:
        for j in range(s,len(lines)):
            if lines[j].startswith("└")or lines[j].startswith("╝")or(lines[j].startswith("+")and"-"in lines[j]):
                e=j
                break
        if e is None:
            e=len(lines)-1
        return"\n".join(lines[s:e+1])
    return text

def parse_box_data(box_text):
    rows=[]
    lines=box_text.splitlines()
    t=None
    for i,l in enumerate(lines):
        if l.startswith("├")or l.startswith("╟"):
            t=i
            break
        if(l.startswith("+")and"-"in l and"┬"not in l)or"┼"in l:
            t=i
            break
    if t is None:
        return rows
    d=[]
    for l in lines[t+1:]:
        if l.startswith("└")or l.startswith("╚")or(l.startswith("+")and"-"in l):
            break
        if"│"in l:
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
        n=datetime.now()
        hh,mm,ss=s.split(":")
        return n.replace(hour=int(hh),minute=int(mm),second=int(ss),microsecond=0)
    except:
        return datetime.max

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

def write_output_csv():
    global best_entries
    p=os.path.join(wardrive_log_dir,"bettercap.wiglecsv")
    def k(i):
        return parse_seen_time(i[1]["Seen"])
    s=sorted(best_entries.items(),key=k)
    try:
        with open(p,"w",encoding="utf-8")as f:
            f.write("RSSI,MAC,Vendor,Flags,Seen\n")
            for m,r in s:
                f.write("{},{},{},{},{}\n".format(r["RSSI"],r["MAC"],r["Vendor"],r["Flags"],r["Seen"]))
    except:
        pass

def start_bettercap():
    global bettercap_process,pty_fd,best_entries,last_killed_pid,bettercap_data_fd
    kill_existing_bettercap()
    cleanup_old_csv()
    last_killed_pid=None
    best_entries={}
    os.makedirs(bettercap_log_dir,exist_ok=True)
    os.makedirs(wardrive_log_dir,exist_ok=True)
    open(bettercap_data_path,"w").close()
    bettercap_data_fd=open(bettercap_data_path,"a")
    pty_master,slave_fd=os.openpty()
    fl=fcntl.fcntl(pty_master,fcntl.F_GETFL)
    fcntl.fcntl(pty_master,fcntl.F_SETFL,fl|os.O_NONBLOCK)
    try:
        bettercap_process=subprocess.Popen(["sudo","bettercap"],stdin=slave_fd,stdout=slave_fd,stderr=slave_fd,universal_newlines=False,bufsize=0,preexec_fn=os.setsid)
    except:
        return
    os.close(slave_fd)
    time.sleep(1)
    try:
        os.write(pty_master,b"ble.recon on\n")
    except:
        pass
    pty_fd=pty_master
    set_log_level("success","WARDRIVING STARTED")

def stop_bettercap():
    global bettercap_process,pty_fd,last_killed_pid,bettercap_data_fd
    if bettercap_process is not None and bettercap_process.poll()is None:
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

def handle_bettercap_output():
    if bettercap_process is None or bettercap_process.poll()is not None or pty_fd is None:
        return
    try:
        os.write(pty_fd,b"ble.show\n")
    except:
        return
    c=b""
    t=time.time()+0.2
    while time.time()<t:
        r,_,_=select.select([pty_fd],[],[],0.05)
        if pty_fd in r:
            try:
                x=os.read(pty_fd,1024)
                if x:
                    c+=x
                    t=time.time()+0.2
            except:
                break
    txt=c.decode("utf-8",errors="replace")
    if bettercap_data_fd:
        bettercap_data_fd.write(txt)
        bettercap_data_fd.flush()
    ct=ansi_escape.sub("",txt)
    bxt=extract_box(ct)
    nr=parse_box_data(bxt)
    if nr:
        ch=update_best_entries(nr)
        if ch:
            write_output_csv()

def monitor_gps_connection():
    g=None
    while True:
        l=""
        try:
            with open(gps_monitor_log_path,"r")as f:
                lines=f.readlines()
            if lines:
                for line in reversed(lines):
                    if"SUCCESS"in line or"WARNING"in line or"ERROR"in line or"CRITICAL"in line:
                        l=line.strip()
                        break
                if not l:
                    l=lines[-1].strip()
        except:
            pass
        if"SUCCESS"in l:
            if g!="SUCCESS":
                start_bettercap()
            g="SUCCESS"
        elif"WARNING"in l:
            if g!="WARNING":
                set_log_level("warning","GPS INITIALIZING")
                stop_bettercap()
            g="WARNING"
        elif"ERROR"in l:
            if g!="ERROR":
                set_log_level("error","NO GPS DEVICES")
                stop_bettercap()
            g="ERROR"
        elif"CRITICAL"in l:
            if g!="CRITICAL":
                set_log_level("critical","GPS IS NOT RUNNING")
                stop_bettercap()
            g="CRITICAL"
        if g=="SUCCESS":
            handle_bettercap_output()
        time.sleep(1)

def handle_exit(sig,frame):
    stop_bettercap()
    set_log_level("critical","STOPPED, KEYBOARD INTERRUPT.")
    sys.exit(0)

signal.signal(signal.SIGINT,handle_exit)
signal.signal(signal.SIGTERM,handle_exit)

if __name__=="__main__":
    monitor_gps_connection()
