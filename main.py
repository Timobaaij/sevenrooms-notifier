#!/usr/bin/env python3
"""
SevenRooms notifier - Updated to include Salt for resets
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

SEVENROOMS_ENDPOINT = "https://www.sevenrooms.com/api-yoa/availability/widget/range"

# -------------------------
# JSON helpers
# -------------------------
def load_json(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except json.JSONDecodeError:
        return default

def save_json(path: str, obj: Any) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, sort_keys=True)
    os.replace(tmp, path)

# -------------------------
# Parsing helpers
# -------------------------
def parse_date_yyyy_mm_dd(s: str) -> dt.date:
    return dt.datetime.strptime(s.strip(), "%Y-%m-%d").date()

def parse_time_hh_mm(s: str) -> dt.time:
    return dt.datetime.strptime(s.strip(), "%H:%M").time()

def to_mmddyyyy(d: dt.date) -> str:
    return d.strftime("%m-%d-%Y")

def within_window(t: dt.time, start: dt.time, end: dt.time) -> bool:
    return start <= t <= end

def sha_key(*parts: str) -> str:
    # This creates the unique ID for the notification history
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def parse_time_from_time_iso(time_iso: str) -> Optional[dt.datetime]:
    s = (time_iso or "").strip()
    if not s: return None
    fmts = ["%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M"]
    for fmt in fmts:
        try: return dt.datetime.strptime(s, fmt)
        except ValueError: continue
    try: return dt.datetime.fromisoformat(s)
    except: return None

# -------------------------
# Notification Systems
# -------------------------
def ntfy_publish(server: str, topic: str, title: str, message: str, priority: str = "high", tags: str = "bell") -> None:
    if not topic: return
    url = f"{(server or 'https://ntfy.sh').rstrip('/')}/{topic}"
    headers = {"Title": title or "SevenRooms alert", "Priority": priority, "Tags": tags}
    try: requests.post(url, data=message.encode("utf-8"), headers=headers, timeout=20)
    except Exception as e: print(f"[ERR] ntfy failed: {e}")

def send_email(target_email: str, subject: str, body: str) -> None:
    email_user = os.environ.get("EMAIL_USER")
    email_pass = os.environ.get("EMAIL_PASS")
    if not email_user or not email_pass:
        print(f"[INFO] Skipping email to {target_email}: Secrets not set.")
        return
    msg = EmailMessage()
    msg.set_content(body)
    msg['Subject'] = subject
    msg['From'] = email_user
    msg['To'] = target_email
    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(email_user, email_pass)
            smtp.send_message(msg)
        print(f"[OK] Email sent to {target_email}")
    except Exception as e: print(f"[ERR] Email failed: {e}")

# -------------------------
# SevenRooms calls
# -------------------------
def build_query_params(venue, party_size, start_date, num_days, time_slot, halo_size_interval=64, channel="SEVENROOMS_WIDGET", lang="en"):
    return {
        "venue": venue, "time_slot": time_slot, "party_size": str(party_size),
        "halo_size_interval": str(halo_size_interval), "start_date": to_mmddyyyy(start_date),
        "num_days": str(num_days), "channel": channel, "selected_lang_code": lang,
    }

def fetch_availability(params):
    r = requests.get(SEVENROOMS_ENDPOINT, params=params, timeout=25)
    r.raise_for_status()
    return r.json()

def extract_bookable_times(payload):
    out = []
    if payload.get("status") != 200: return out
    data = payload.get("data", {}).get("availability", {})
    if not isinstance(data, dict): return out
    for date_iso, slots in data.items():
        if not isinstance(slots, list): continue
        for slot in slots:
            if slot.get("is_closed") or not isinstance(slot.get("times"), list): continue
            for t in slot.get("times"):
                if t.get("is_requestable") is True: continue
                time_iso = t.get("time_iso") or f"{date_iso} {t.get('time')}"
                if time_iso: out.append((date_iso, t.get("time", ""), time_iso))
    return out

def normalize_config(cfg):
    global_cfg = cfg.get("global", {})
    ntfy_default = cfg.get("ntfy_default", {}) or cfg.get("ntfy", {})
    searches = cfg.get("searches", [])
    if not searches and "venues" in cfg: # Legacy support
        cfg["salt"] = "legacy"
        searches = [cfg] 
    return global_cfg, ntfy_default, searches

def merge_ntfy(default, override):
    out = dict(default or {})
    out.update(override or {})
    return out

# -------------------------
# Main
# -------------------------
def main():
    config_path = os.getenv("CONFIG_PATH", "config.json")
    state_path = os.getenv("STATE_PATH", "state.json")

    cfg = load_json(config_path, default=None)
    if not isinstance(cfg, dict): raise SystemExit(f"Invalid {config_path}")

    global_cfg, ntfy_default, searches = normalize_config(cfg)
    state = load_json(state_path, default={"notified": []})
    notified = set(state.get("notified", []))
    
    # Prune old notifications to keep state file small
    if len(notified) > 5000:
        notified = set(list(notified)[-5000:])

    total_matches = 0

    for s in searches:
        if not isinstance(s, dict): continue
        
        # --- NEW LOGIC: GET SALT ---
        # The salt makes this configuration unique. Changing it resets notifications.
        salt = str(s.get("salt", "")).strip()
        
        search_id = str(s.get("id", "unknown")).strip()
        venues = [str(v).strip() for v in s.get("venues", []) if str(v).strip()]
        if not venues: continue

        try:
            target_date = parse_date_yyyy_mm_dd(str(s.get("date", "")))
            win_start = parse_time_hh_mm(str(s.get("window_start", "18:00")))
            win_end = parse_time_hh_mm(str(s.get("window_end", "20:30")))
        except ValueError: continue

        party_size = int(s.get("party_size", 2))
        email_to = str(s.get("email_to", "")).strip()
        
        ntfy_cfg = merge_ntfy(ntfy_default, s.get("ntfy", {}))
        matches = []

        for venue in venues:
            params = build_query_params(venue, party_size, target_date, int(s.get("num_days", 1)), "18:00")
            try:
                times = extract_bookable_times(fetch_availability(params))
            except Exception as e:
                print(f"[WARN] {venue} err: {e}")
                continue

            for date_iso, label, iso in times:
                if date_iso != to_mmddyyyy(target_date) and date_iso != str(target_date): pass # simplified check
                
                # Check Time Window
                dt_obj = parse_time_from_time_iso(iso)
                if not dt_obj or not within_window(dt_obj.time(), win_start, win_end): continue

                # --- NEW LOGIC: INCLUDE SALT IN HASH ---
                # If you deleted and re-added the search, 'salt' is new, so 'k' is new.
                k = sha_key("search", search_id, venue, str(party_size), iso, salt)
                
                if k in notified: continue
                matches.append((venue, label, iso, k))

        if matches:
            total_matches += len(matches)
            msg_lines = [f"✅ Table Found: {search_id}", f"Date: {target_date}", f"Party: {party_size}"]
            for v, l, i, k in matches:
                msg_lines.append(f"• {v} @ {l or i}")
                notified.add(k)
            
            full_msg = "\n".join(msg_lines)
            
            # Send Push
            if ntfy_cfg.get("topic"):
                ntfy_publish(ntfy_cfg.get("server"), ntfy_cfg.get("topic"), "Table Found", full_msg, ntfy_cfg.get("priority"), ntfy_cfg.get("tags"))
            
            # Send Email
            if email_to:
                send_email(email_to, f"Table Available: {search_id}", full_msg)
            
            print(f"[OK] {search_id}: Found {len(matches)} slots.")

    save_json(state_path, {"notified": list(notified)})
    print(f"[DONE] New slots: {total_matches}")

if __name__ == "__main__":
    main()
