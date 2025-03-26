#!/usr/bin/env python3
import os
import glob
import csv
import argparse
import signal
import sys
import re
import shutil
import time
from datetime import datetime
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

logger.remove()
fmt="<yellow>{time:DD/MM @ HH:mm:ss.SSS} </yellow><blue>|</blue><level>{level:^9}</level><blue>|</blue><magenta> MERGE </magenta><blue>|</blue> <cyan>{message}</cyan>"
logger.add(sys.stderr,level="INFO",colorize=True,format=fmt)

HEADER_LINE_1="WigleWifi-1.4,appRelease=Kismet202307R1,model=Kismet202307R1,release=2023.07.R1,device=kismet,display=kismet,board=kismet,brand=kismet"
HEADER_LINE_2="MAC,SSID,AuthMode,FirstSeen,Channel,RSSI,CurrentLatitude,CurrentLongitude,AltitudeMeters,AccuracyMeters,Type"
OUTPUT_COLUMNS=["MAC","SSID","AuthMode","FirstSeen","Channel","RSSI","CurrentLatitude","CurrentLongitude","AltitudeMeters","AccuracyMeters","Type"]

def signal_handler(sig,frame):
    set_log_level("info","Shutdown signal received. Exiting...")
    sys.exit(0)

signal.signal(signal.SIGINT,signal_handler)
signal.signal(signal.SIGTERM,signal_handler)

def parse_bettercap_file(filepath,base_date):
    data={}
    try:
        with open(filepath,"r",encoding="utf-8") as f:
            lines=f.read().splitlines()
    except Exception as e:
        set_log_level("error",f"Error reading bettercap: {e}")
        return data
    for line in lines[1:]:
        if not line.strip():
            continue
        parts=[p.strip() for p in line.split(",")]
        rssi=mac=vendor=flags=seen=""
        if len(parts)==5:
            rssi,mac,vendor,flags,seen=parts
        elif len(parts)==7:
            rssi=parts[0]
            mac=parts[1]
            vendor=parts[2]+", "+parts[3]
            flags=parts[4]+", "+parts[5]
            seen=parts[6]
        elif len(parts)>7:
            rssi=parts[0]
            mac=parts[1]
            seen=parts[-1]
            middle=parts[2:-1]
            half=len(middle)//2
            vendor=", ".join(middle[:half])
            flags=", ".join(middle[half:])
        else:
            continue
        rssi=rssi.replace("dBm","").strip()
        full_timestamp=f"{base_date} {seen}"
        record={
            "MAC":mac,
            "SSID":vendor,
            "AuthMode":flags,
            "FirstSeen":full_timestamp,
            "Channel":"",
            "RSSI":rssi,
            "CurrentLatitude":"",
            "CurrentLongitude":"",
            "AltitudeMeters":"",
            "AccuracyMeters":"",
            "Type":""
        }
        data[mac]=record
    return data

def parse_kismet_file(filepath):
    data={}
    try:
        with open(filepath,"r",encoding="utf-8",errors="replace") as f:
            reader=csv.reader(f)
            next(reader,None)
            header2=next(reader,None)
            if not header2:
                set_log_level("error",f"No valid header in {filepath}")
                return data
            for row in reader:
                if not row or len(row)<len(OUTPUT_COLUMNS):
                    continue
                record=dict(zip(OUTPUT_COLUMNS,row))
                mac=record.get("MAC","")
                if mac:
                    data[mac]=record
    except Exception as e:
        set_log_level("error",f"Error reading kismet: {e}")
    return data

def merge_logs(bettercap_data,kismet_data):
    merged={}
    for mac,record in kismet_data.items():
        merged[mac]=record
    for mac,bc_record in bettercap_data.items():
        if mac in merged:
            merged[mac]["SSID"]=bc_record.get("SSID",merged[mac]["SSID"])
            merged[mac]["AuthMode"]=bc_record.get("AuthMode",merged[mac]["AuthMode"])
            merged[mac]["FirstSeen"]=bc_record.get("FirstSeen",merged[mac]["FirstSeen"])
            merged[mac]["RSSI"]=bc_record.get("RSSI",merged[mac]["RSSI"])
        else:
            merged[mac]=bc_record
        merged[mac]["Type"]="BLE"
    fallback_gps=None
    fallback_gps_time=None
    for record in kismet_data.values():
        if record.get("CurrentLatitude","").strip() and record.get("CurrentLongitude","").strip():
            try:
                t=datetime.strptime(record["FirstSeen"],"%Y-%m-%d %H:%M:%S")
            except:
                continue
            if fallback_gps is None or t>fallback_gps_time:
                fallback_gps=record
                fallback_gps_time=t
    if fallback_gps:
        for rec in merged.values():
            if rec.get("Type")=="BLE":
                if not rec.get("Channel","").strip() or not rec.get("CurrentLatitude","").strip():
                    rec["Channel"]=fallback_gps.get("Channel","")
                    rec["CurrentLatitude"]=fallback_gps.get("CurrentLatitude","")
                    rec["CurrentLongitude"]=fallback_gps.get("CurrentLongitude","")
                    rec["AltitudeMeters"]=fallback_gps.get("AltitudeMeters","")
                    rec["AccuracyMeters"]=fallback_gps.get("AccuracyMeters","")
    return merged

def write_merged_csv(merged_data,output_filepath):
    try:
        with open(output_filepath,"w",newline="",encoding="utf-8") as f:
            f.write(HEADER_LINE_1+"\n")
            f.write(HEADER_LINE_2+"\n")
            writer=csv.writer(f)
            sorted_records=sorted(merged_data.values(),key=lambda r:r.get("FirstSeen",""))
            for record in sorted_records:
                row=[record.get(col,"") for col in OUTPUT_COLUMNS]
                writer.writerow(row)
        set_log_level("debug",f"Merged CSV updated: {output_filepath}")
    except Exception as e:
        set_log_level("error",f"Error writing merged CSV: {e}")

def parse_wigle_file(filepath):
    lines=[]
    try:
        with open(filepath,"r",encoding="utf-8",errors="replace") as f:
            lines=f.read().splitlines()
    except:
        return {},{}
    if not lines:
        return {},{}
    if "WigleWifi-1.4" in lines[0]:
        return {},parse_kismet_file(filepath)
    base_date=""
    bd_match=re.match(r"(\d{4}-\d{2}-\d{2})_",os.path.basename(filepath))
    if bd_match:
        base_date=bd_match.group(1)
    return parse_bettercap_file(filepath,base_date),{}

def main():
    parser=argparse.ArgumentParser(description="")
    parser.add_argument("--log-dir",type=str,default="logs/wardrive_logs/",help="")
    parser.add_argument("--interval",type=int,default=10,help="")
    args=parser.parse_args()
    log_dir=os.path.expanduser(args.log_dir)
    processing_dir=os.path.join(log_dir,"processing")
    upload_dir=os.path.join(log_dir,"uploading")
    if not os.path.isdir(processing_dir):
        set_log_level("error",f"Processing dir '{processing_dir}' does not exist.")
        sys.exit(1)
    os.makedirs(upload_dir,exist_ok=True)
    existing_files=glob.glob(os.path.join(processing_dir,"wardrive-*.csv"))
    for ef in existing_files:
        base=os.path.basename(ef)
        shutil.move(ef,os.path.join(upload_dir,base))
    now_str=datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    output_filename=f"wardrive-{now_str}.csv"
    output_filepath=os.path.join(processing_dir,output_filename)
    set_log_level("info",f"Output CSV: {output_filepath}")
    iteration=0
    merged_previous={}
    while True:
        iteration+=1
        wigle_files=glob.glob(os.path.join(log_dir,"*.wiglecsv"))
        if not wigle_files:
            time.sleep(1)
            continue
        all_bettercap={}
        all_kismet={}
        for wf in wigle_files:
            b,k=parse_wigle_file(wf)
            for m,r in b.items():
                all_bettercap[m]=r
            for m,r in k.items():
                all_kismet[m]=r
        merged=merge_logs(all_bettercap,all_kismet)
        write_merged_csv(merged,output_filepath)
        if iteration%args.interval==0:
            new_lines=0
            updates=0
            if not merged_previous:
                new_lines=len(merged)
            else:
                for m in merged:
                    if m not in merged_previous:
                        new_lines+=1
                    else:
                        if merged[m]!=merged_previous[m]:
                            updates+=1
            set_log_level("info",f"Iteration #{iteration}: new {new_lines}, updates {updates}")
            merged_previous={k:v.copy() for k,v in merged.items()}
        time.sleep(1)

if __name__=="__main__":
    main()
