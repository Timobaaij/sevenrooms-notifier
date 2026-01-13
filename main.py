#!/usr/bin/env python3
"""
SevenRooms availability notifier (multi-search config.json, GitHub Secrets for Email)

Files expected in repo:
- config.json  (editable settings; supports multiple searches)
- state.json   (dedupe store)

Requires:
- requests
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
    # inclusive start and end
    return start <= t <= end


def sha_key(*parts: str) -> str:
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def parse_time_from_time_iso(time_iso: str) -> Optional[dt.datetime]:
    s = (time_iso or "").strip()
    if not s:
        return None
    fmts = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%dT%H:%M",
    ]
    for fmt in fmts:
        try:
            return dt.datetime.strptime(s, fmt)
        except ValueError:
            continue
    try:
        return dt.datetime.fromisoformat(s)
    except Exception:
        return None


# -------------------------
# Notification Systems (NTFY + Email)
# -------------------------

def ntfy_publish(server: str, topic: str, title: str, message: str,
                 priority: str = "high", tags: str = "bell") -> None:
    server = (server or "https://ntfy.sh").strip().rstrip("/")
    topic = (topic or "").strip()
    if not topic:
        # We allow missing topic if user only wants email, so just return
        return

    url = f"{server}/{topic}"
    headers = {
        "Title": title or "SevenRooms alert",
        "Priority": (priority or "high").strip(),
        "Tags": (tags or "bell").strip(),
    }
    try:
        r = requests.post(url, data=message.encode("utf-8"), headers=headers, timeout=20)
        r.raise_for_status()
    except Exception as e:
        print(f"[ERR] ntfy failed: {e}")


def send_email(target_email: str, subject: str, body: str) -> None:
    """Sends an email using secrets from environment variables."""
    email_user = os.environ.get("EMAIL_USER")
    email_pass = os.environ.get("EMAIL_PASS")

    if not email_user or not email_pass:
        print(f"[INFO] Skipping email to {target_email}: EMAIL_USER or EMAIL_PASS secrets not set.")
        return

    msg = EmailMessage()
    msg.set_content(body)
    msg['Subject'] = subject
    msg['From'] = email_user
    msg['To'] = target_email

    try:
        # Using Gmail's standard SSL port
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(email_user, email_pass)
            smtp.send_message(msg)
        print(f"[OK] Email sent to {target_email}")
    except Exception as e:
        print(f"[ERR] Failed to send email: {e}")


# -------------------------
# SevenRooms calls
# -------------------------

def build_query_params(venue: str, party_size: int, start_date: dt.date,
                       num_days: int, time_slot: str,
                       halo_size_interval: int = 64,
                       channel: str = "SEVENROOMS_WIDGET",
                       lang: str = "en") -> Dict[str, str]:
    return {
        "venue": venue,
        "time_slot": time_slot,
        "party_size": str(party_size),
        "halo_size_interval": str(halo_size_interval),
        "start_date": to_mmddyyyy(start_date),
        "num_days": str(num_days),
        "channel": channel,
        "selected_lang_code": lang,
    }


def fetch_availability(params: Dict[str, str]) -> Dict[str, Any]:
    r = requests.get(SEVENROOMS_ENDPOINT, params=params, timeout=25)
    r.raise_for_status()
    return r.json()


def extract_bookable_times(payload: Dict[str, Any]) -> List[Tuple[str, str, str]]:
    """
    Returns list of (date_iso "YYYY-MM-DD", time_label, time_iso_str).
    Excludes request-only times where is_requestable=true.
    """
    out: List[Tuple[str, str, str]] = []

    if payload.get("status") != 200:
        return out

    data = payload.get("data") or {}
    availability = data.get("availability") or {}
    if not isinstance(availability, dict):
        return out

    for date_iso, slots in availability.items():
        if not isinstance(slots, list):
            continue
        for slot in slots:
            if not isinstance(slot, dict):
                continue
            if slot.get("is_closed") is True:
                continue
            times = slot.get("times") or []
            if not isinstance(times, list):
                continue
            for t in times:
                if not isinstance(t, dict):
                    continue
                if t.get("is_requestable") is True:
                    continue
                time_label = str(t.get("time") or "").strip()
                time_iso = str(t.get("time_iso") or "").strip()
                if not time_iso and time_label:
                    time_iso = f"{date_iso} {time_label}"
                if time_label or time_iso:
                    out.append((date_iso, time_label, time_iso))
    return out


# -------------------------
# Config normalization
# -------------------------

def normalize_config(cfg: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any], List[Dict[str, Any]]]:
    """
    Returns (global_cfg, ntfy_default, searches)
    """
    global_cfg = cfg.get("global", {}) if isinstance(cfg.get("global"), dict) else {}

    ntfy_default = cfg.get("ntfy_default", {}) if isinstance(cfg.get("ntfy_default"), dict) else {}
    if not ntfy_default:
        # fallback to legacy 'ntfy' block if present
        if isinstance(cfg.get("ntfy"), dict):
            ntfy_default = cfg.get("ntfy")

    searches = cfg.get("searches")
    if isinstance(searches, list) and searches:
        return global_cfg, ntfy_default, searches

    # Legacy single-search shape
    legacy_search = {
        "id": cfg.get("id", "default"),
        "venues": cfg.get("venues", []),
        "party_size": cfg.get("party_size", 2),
        "date": cfg.get("date"),
        "window_start": cfg.get("window_start", "18:00"),
        "window_end": cfg.get("window_end", "20:30"),
        "time_slot": cfg.get("time_slot", cfg.get("window_start", "18:00")),
        "num_days": cfg.get("num_days", 1),
        "ntfy": cfg.get("ntfy", {}),
        "email_to": cfg.get("email_to", ""), # Added legacy support for email
    }
    return global_cfg, ntfy_default, [legacy_search]


def merge_ntfy(ntfy_default: Dict[str, Any], ntfy_override: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(ntfy_default or {})
    if isinstance(ntfy_override, dict):
        for k, v in ntfy_override.items():
            out[k] = v
    return out


# -------------------------
# Main
# -------------------------

def main():
    config_path = os.getenv("CONFIG_PATH", "config.json")
    state_path = os.getenv("STATE_PATH", "state.json")

    cfg = load_json(config_path, default=None)
    if not isinstance(cfg, dict):
        raise SystemExit(f"Missing/invalid {config_path}. Must be valid JSON object.")

    global_cfg, ntfy_default, searches = normalize_config(cfg)

    # Global tuning
    delay_between_venues_sec = float(global_cfg.get("delay_between_venues_sec", 0.5))
    halo_size_interval = int(global_cfg.get("halo_size_interval", 64))
    channel = str(global_cfg.get("channel", "SEVENROOMS_WIDGET"))
    lang = str(global_cfg.get("lang", "en"))

    # State (dedupe)
    state = load_json(state_path, default={"notified": []})
    notified = set(state.get("notified", [])) if isinstance(state, dict) else set()

    all_new_keys = []
    total_matches = 0

    for s in searches:
        if not isinstance(s, dict):
            continue

        search_id = str(s.get("id", "")).strip() or sha_key(json.dumps(s, sort_keys=True))
        venues = s.get("venues", [])
        if isinstance(venues, str):
            venues = [venues]
        venues = [str(v).strip() for v in venues if str(v).strip()]
        if not venues:
            print(f"[WARN] search '{search_id}': no venues; skipping.")
            continue

        date_str = str(s.get("date", "")).strip()
        if not date_str:
            print(f"[WARN] search '{search_id}': missing date; skipping.")
            continue
        target_date = parse_date_yyyy_mm_dd(date_str)

        party_size = int(s.get("party_size", 2))
        window_start = parse_time_hh_mm(str(s.get("window_start", "18:00")))
        window_end = parse_time_hh_mm(str(s.get("window_end", "20:30")))

        time_slot = str(s.get("time_slot", s.get("window_start", "18:00"))).strip() or "18:00"
        num_days = int(s.get("num_days", 1))
        if num_days < 1:
            num_days = 1

        # Email Config
        email_to = str(s.get("email_to", "")).strip()

        # NTFY Config
        ntfy_cfg = merge_ntfy(ntfy_default, s.get("ntfy", {}))
        ntfy_server = str(ntfy_cfg.get("server", "https://ntfy.sh")).strip()
        ntfy_topic = str(ntfy_cfg.get("topic", "")).strip()
        ntfy_priority = str(ntfy_cfg.get("priority", "high")).strip()
        ntfy_tags = str(ntfy_cfg.get("tags", "bell")).strip()
        ntfy_title = str(ntfy_cfg.get("title", "Reservation slot found")).strip()

        matches_for_search = []  # list of (venue, date_iso, label, iso)

        for venue in venues:
            params = build_query_params(
                venue=venue,
                party_size=party_size,
                start_date=target_date,
                num_days=num_days,
                time_slot=time_slot,
                halo_size_interval=halo_size_interval,
                channel=channel,
                lang=lang,
            )

            try:
                payload = fetch_availability(params)
            except Exception as e:
                print(f"[WARN] search '{search_id}' venue '{venue}': fetch failed: {e}")
                time.sleep(delay_between_venues_sec)
                continue

            times = extract_bookable_times(payload)
            for date_iso, label, iso in times:
                if date_iso != target_date.strftime("%Y-%m-%d"):
                    continue
                dt_obj = parse_time_from_time_iso(iso)
                if dt_obj is None:
                    continue
                t = dt_obj.time()
                if not within_window(t, window_start, window_end):
                    continue

                # IMPORTANT: include search_id in dedupe key so different searches don't collide
                k = sha_key("search", search_id, venue, str(party_size), date_iso, iso)
                if k in notified:
                    continue

                matches_for_search.append((venue, date_iso, label, iso, k))

            time.sleep(delay_between_venues_sec)

        if matches_for_search:
            total_matches += len(matches_for_search)

            # Build message
            lines = []
            lines.append("✅ SevenRooms availability found")
            lines.append(f"Search: {search_id}")
            lines.append(f"Date: {target_date.strftime('%Y-%m-%d')}")
            lines.append(f"Window: {window_start.strftime('%H:%M')}–{window_end.strftime('%H:%M')}")
            lines.append(f"Party size: {party_size}")
            lines.append("")

            # group by venue
            by_venue: Dict[str, List[Tuple[str, str]]] = {}
            for v, d_iso, label, iso, _k in matches_for_search:
                by_venue.setdefault(v, []).append((label, iso))

            for v in sorted(by_venue.keys()):
                lines.append(f"• {v}")
                for label, iso in sorted(by_venue[v], key=lambda x: x[1])[:25]:
                    pretty = label or (parse_time_from_time_iso(iso).strftime("%H:%M") if parse_time_from_time_iso(iso) else iso)
                    lines.append(f"  - {pretty} ({iso})")
                if len(by_venue[v]) > 25:
                    lines.append(f"  …plus {len(by_venue[v]) - 25} more")
                lines.append("")

            message = "\n".join(lines).strip()

            # 1. Send push (NTFY)
            if ntfy_topic:
                ntfy_publish(
                    server=ntfy_server,
                    topic=ntfy_topic,
                    title=ntfy_title,
                    message=message,
                    priority=ntfy_priority,
                    tags=ntfy_tags
                )
            
            # 2. Send Email (New Feature)
            if email_to:
                email_subject = f"Table Available: {search_id} ({target_date})"
                send_email(email_to, email_subject, message)

            # Update dedupe
            for *_rest, k in matches_for_search:
                notified.add(k)
                all_new_keys.append(k)

            print(f"[OK] search '{search_id}': notified {len(matches_for_search)} new slot(s).")
        else:
            print(f"[OK] search '{search_id}': no new matching availability.")

    # Persist state (even if no matches, keep it stable)
    # Keep bounded
    state_out = {"notified": list(notified)[-2000:]}
    save_json(state_path, state_out)

    print(f"[DONE] Total new slots notified this run: {total_matches}")


if __name__ == "__main__":
    main()
