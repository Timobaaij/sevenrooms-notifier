#!/usr/bin/env python3
"""
Reservation Notifier - Supports SevenRooms & OpenTable
"""

import os
import json
import time
import hashlib
import datetime as dt
import smtplib
from email.message import EmailMessage
from typing import Any, Dict, List, Optional, Tuple
import requests

# --- ENDPOINTS ---
SEVENROOMS_ENDPOINT = "https://www.sevenrooms.com/api-yoa/availability/widget/range"
OPENTABLE_ENDPOINT = "https://www.opentable.com/api/v2/reservation/availability"

# --- JSON HELPERS ---
def load_json(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f: return json.load(f)
    except: return default

def save_json(path: str, obj: Any) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f: json.dump(obj, f, indent=2, sort_keys=True)
    os.replace(tmp, path)

# --- TIME HELPERS ---
def parse_date_iso(s: str) -> dt.date:
    return dt.datetime.strptime(s.strip(), "%Y-%m-%d").date()

def parse_time_iso(s: str) -> dt.time:
    return dt.datetime.strptime(s.strip(), "%H:%M").time()

def sha_key(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()

def within_window(t: dt.time, start: dt.time, end: dt.time) -> bool:
    return start <= t <= end

# --- NOTIFICATIONS ---
def send_ntfy(server, topic, title, message, priority="high"):
    if not topic: return
    try:
        requests.post(f"{(server or 'https://ntfy.sh').rstrip('/')}/{topic}", 
                      data=message.encode("utf-8"), 
                      headers={"Title": title, "Priority": priority}, timeout=15)
    except Exception as e: print(f"[ERR] ntfy: {e}")

def send_email(target, subject, body):
    user, pwd = os.environ.get("EMAIL_USER"), os.environ.get("EMAIL_PASS")
    if not user or not pwd: return
    msg = EmailMessage()
    msg.set_content(body)
    msg['Subject'] = subject
    msg['From'] = user
    msg['To'] = target
    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as s:
            s.login(user, pwd)
            s.send_message(msg)
        print(f"[OK] Email sent to {target}")
    except Exception as e: print(f"[ERR] Email: {e}")

# --- FETCHERS ---

def check_sevenrooms(venue_slug, date, party, days):
    """SevenRooms Fetcher"""
    params = {
        "venue": venue_slug,
        "time_slot": "18:00", # Arbitrary start point
        "party_size": str(party),
        "halo_size_interval": "120", # Look 2 hours around
        "start_date": date.strftime("%m-%d-%Y"),
        "num_days": str(days),
        "channel": "SEVENROOMS_WIDGET"
    }
    slots = []
    try:
        r = requests.get(SEVENROOMS_ENDPOINT, params=params, timeout=20)
        if r.status_code != 200: return []
        data = r.json().get("data", {}).get("availability", {})
        for day, times in data.items():
            for t in times:
                if t.get("is_closed"): continue
                for slot in t.get("times", []):
                    if slot.get("is_requestable"): continue # Skip 'request only'
                    iso_str = slot.get("time_iso")
                    if iso_str: slots.append(iso_str)
    except Exception as e:
        print(f"[WARN] SR {venue_slug}: {e}")
    return slots

def check_opentable(venue_id, date, party, days):
    """OpenTable Fetcher (Requires Numeric ID)"""
    slots = []
    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.0 Mobile/15E148 Safari/604.1",
        "Accept": "application/json",
        "Content-Type": "application/json"
    }
    
    # OpenTable checks one day at a time, so we loop manually
    for i in range(days):
        check_date = date + dt.timedelta(days=i)
        check_str = check_date.strftime("%Y-%m-%d")
        
        # We check a broad range to simulate a "day view"
        params = {
            "rid": venue_id,
            "partySize": str(party),
            "dateTime": f"{check_str}T19:00" # Center around 7pm
        }
        
        try:
            r = requests.get(OPENTABLE_ENDPOINT, params=params, headers=headers, timeout=20)
            if r.status_code == 200:
                # OpenTable returns 'availability' keys
                avail = r.json().get("availability", {})
                for day_key, day_data in avail.items():
                    for time_slot in day_data:
                        # Construct ISO string: YYYY-MM-DDTHH:MM
                        # OT format is usually "2025-01-01T19:00:00"
                        if time_slot.get("isAvailable"):
                            slots.append(time_slot.get("dateTime"))
        except Exception as e:
            print(f"[WARN] OT {venue_id}: {e}")
        
        time.sleep(1) # Be polite to OpenTable
        
    return slots

# --- MAIN ENGINE ---

def main():
    config_path = os.getenv("CONFIG_PATH", "config.json")
    state_path = os.getenv("STATE_PATH", "state.json")
    
    cfg = load_json(config_path, {})
    state = load_json(state_path, {"notified": []})
    notified = set(state.get("notified", []))
    
    searches = cfg.get("searches", [])
    total_new = 0
    
    for s in searches:
        s_id = s.get("id")
        platform = s.get("platform", "sevenrooms").lower() # Default to SR
        venues = s.get("venues", [])
        salt = s.get("salt", "")
        
        # Parse Dates/Times
        try:
            start_date = parse_date_iso(s.get("date"))
            w_start = parse_time_iso(s.get("window_start"))
            w_end = parse_time_iso(s.get("window_end"))
        except: continue
            
        party = int(s.get("party_size", 2))
        days = int(s.get("num_days", 1))
        
        found_matches = []
        
        for v in venues:
            # SWITCH: CHOOSE PLATFORM
            if platform == "opentable":
                slots = check_opentable(v, start_date, party, days)
            else:
                slots = check_sevenrooms(v, start_date, party, days)
                
            for slot_iso in slots:
                # Convert slot to objects
                try:
                    slot_dt = dt.datetime.fromisoformat(slot_iso)
                    slot_date = slot_dt.date()
                    slot_time = slot_dt.time()
                except: continue
                
                # Check Logic: Date Range & Time Window
                date_diff = (slot_date - start_date).days
                if not (0 <= date_diff < days): continue
                if not within_window(slot_time, w_start, w_end): continue
                
                # Fingerprint
                k = sha_key(s_id, platform, v, str(party), slot_iso, salt)
                if k in notified: continue
                
                found_matches.append(f"{v} ({platform}) @ {slot_dt.strftime('%Y-%m-%d %H:%M')}")
                notified.add(k)

        # Notify
        if found_matches:
            total_new += len(found_matches)
            msg = "\n".join(found_matches)
            print(f"FOUND: {msg}")
            
            # Push
            ntfy = cfg.get("ntfy", {}) or s.get("ntfy", {})
            if ntfy.get("topic"):
                send_ntfy(ntfy.get("server"), ntfy.get("topic"), f"Table: {s_id}", msg)
            
            # Email
            if s.get("email_to"):
                send_email(s.get("email_to"), f"Table: {s_id}", msg)
                
    # Save State
    save_json(state_path, {"notified": list(notified)[-5000:]})

if __name__ == "__main__":
    main()
