
#!/usr/bin/env python3
"""
SevenRooms availability notifier (config-file driven, no GitHub Secrets required)

Files expected in repo:
- config.json  (editable settings)
- state.json   (dedupe store, committed back by workflow or kept local)

Requires:
- requests
"""

import os
import json
import time
import hashlib
import datetime as dt
from typing import Any, Dict, List, Optional, Tuple

import requests


SEVENROOMS_ENDPOINT = "https://www.sevenrooms.com/api-yoa/availability/widget/range"


# -------------------------
# Utilities
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


def parse_date_yyyy_mm_dd(s: str) -> dt.date:
    return dt.datetime.strptime(s.strip(), "%Y-%m-%d").date()


def parse_time_hh_mm(s: str) -> dt.time:
    return dt.datetime.strptime(s.strip(), "%H:%M").time()


def to_mmddyyyy(d: dt.date) -> str:
    # SevenRooms widget endpoint uses MM-DD-YYYY in many implementations.
    return d.strftime("%m-%d-%Y")


def within_window(t: dt.time, start: dt.time, end: dt.time) -> bool:
    # Inclusive start, inclusive end (user expectation for booking windows)
    return start <= t <= end


def sha_key(*parts: str) -> str:
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def safe_get(d: Dict[str, Any], keys: List[str], default=None):
    cur = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def parse_time_from_time_iso(time_iso: str) -> Optional[dt.datetime]:
    """
    Best-effort parsing for SevenRooms time strings.
    Seen formats:
      - "2025-05-30 21:45:00"
      - "2025-05-30T21:45:00"
      - ISO with timezone (rare in widget)
    """
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

    # last resort: try fromisoformat (handles some variants)
    try:
        return dt.datetime.fromisoformat(s)
    except Exception:
        return None


# -------------------------
# NTFY push
# -------------------------

def ntfy_publish(server: str, topic: str, title: str, message: str,
                 priority: str = "high", tags: str = "bell") -> None:
    server = (server or "https://ntfy.sh").strip().rstrip("/")
    topic = (topic or "").strip()
    if not topic:
        raise RuntimeError("Missing ntfy topic in config.json (ntfy.topic)")

    url = f"{server}/{topic}"
    headers = {
        "Title": title,
        "Priority": priority or "high",
        "Tags": tags or "bell",
    }

    r = requests.post(url, data=message.encode("utf-8"), headers=headers, timeout=20)
    r.raise_for_status()


# -------------------------
# SevenRooms fetch + extract
# -------------------------

def build_query_params(venue: str, party_size: int, start_date: dt.date,
                       num_days: int, time_slot: str,
                       halo_size_interval: int = 64,
                       channel: str = "SEVENROOMS_WIDGET",
                       lang: str = "en") -> Dict[str, str]:
    return {
        "venue": venue,
        "time_slot": time_slot,  # anchor time used by widget endpoint
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
    Returns list of tuples: (date_iso "YYYY-MM-DD", time_label, time_iso_str)
    Filters out requestable slots where is_requestable=true.
    """
    out: List[Tuple[str, str, str]] = []

    if payload.get("status") != 200:
        return out

    availability = safe_get(payload, ["data", "availability"], default={})
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

                # Exclude request-only slots
                if t.get("is_requestable") is True:
                    continue

                time_label = str(t.get("time") or "").strip()
                time_iso = str(t.get("time_iso") or "").strip()

                # Some payloads may omit time_iso; best effort fallback
                if not time_iso and time_label:
                    time_iso = f"{date_iso} {time_label}"

                if time_label or time_iso:
                    out.append((date_iso, time_label, time_iso))

    return out


# -------------------------
# Main logic
# -------------------------

def main():
    config_path = os.getenv("CONFIG_PATH", "config.json")
    state_path = os.getenv("STATE_PATH", "state.json")

    cfg = load_json(config_path, default=None)
    if not isinstance(cfg, dict):
        raise SystemExit(f"Missing or invalid {config_path}. Create it as valid JSON.")

    # Venues (list) — your "any restaurant" = keep adding venue slugs here
    venues = cfg.get("venues", [])
    if isinstance(venues, str):
        venues = [venues]
    if not isinstance(venues, list) or not venues:
        raise SystemExit("config.json must include a non-empty 'venues' list.")

    venues = [str(v).strip() for v in venues if str(v).strip()]

    # Reservation search config
    party_size = int(cfg.get("party_size", 2))
    date_str = str(cfg.get("date", "")).strip()
    if not date_str:
        raise SystemExit("config.json must include 'date' in YYYY-MM-DD format.")
    target_date = parse_date_yyyy_mm_dd(date_str)

    window_start = parse_time_hh_mm(str(cfg.get("window_start", "18:00")))
    window_end = parse_time_hh_mm(str(cfg.get("window_end", "20:30")))

    # Widget query tuning (optional)
    halo_size_interval = int(cfg.get("halo_size_interval", 64))
    channel = str(cfg.get("channel", "SEVENROOMS_WIDGET"))
    lang = str(cfg.get("lang", "en"))

    # Anchor time_slot (optional but recommended: set near window start)
    time_slot = str(cfg.get("time_slot", cfg.get("window_start", "18:00"))).strip() or "18:00"

    # How many days to search — for a specific date, we search exactly 1 day
    num_days = int(cfg.get("num_days", 1))
    if num_days < 1:
        num_days = 1

    # ntfy config
    ntfy_cfg = cfg.get("ntfy", {}) if isinstance(cfg.get("ntfy", {}), dict) else {}
    ntfy_server = str(ntfy_cfg.get("server", "https://ntfy.sh")).strip()
    ntfy_topic = str(ntfy_cfg.get("topic", "")).strip()
    ntfy_priority = str(ntfy_cfg.get("priority", "high")).strip()
    ntfy_tags = str(ntfy_cfg.get("tags", "bell")).strip()

    # Dedupe state
    state = load_json(state_path, default={"notified": []})
    notified = set(state.get("notified", [])) if isinstance(state, dict) else set()

    all_matches = []  # list of dicts: {venue, date, label, iso, key}

    # Small polite delay between venues (reduces chance of rate limiting)
    delay_between_venues_sec = float(cfg.get("delay_between_venues_sec", 0.5))

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
            # Continue other venues even if one fails
            print(f"[WARN] Venue '{venue}': fetch failed: {e}")
            time.sleep(delay_between_venues_sec)
            continue

        times = extract_bookable_times(payload)

        for date_iso, label, iso in times:
            # Only keep target date (sometimes API returns beyond range depending on parameters)
            if date_iso != target_date.strftime("%Y-%m-%d"):
                continue

            dt_obj = parse_time_from_time_iso(iso)
            if dt_obj is None:
                # If no parse, skip to avoid false alerts
                continue

            t = dt_obj.time()
            if not within_window(t, window_start, window_end):
                continue

            k = sha_key(venue, str(party_size), date_iso, iso)
            if k in notified:
                continue

            all_matches.append({
                "venue": venue,
                "date": date_iso,
                "time_label": label,
                "time_iso": iso,
                "key": k
            })

        time.sleep(delay_between_venues_sec)

    # If matches found: send push + update state.json
    if all_matches:
        # Group by venue for nicer message
        by_venue: Dict[str, List[Dict[str, str]]] = {}
        for m in all_matches:
            by_venue.setdefault(m["venue"], []).append(m)

        # Sort times within each venue
        for v in by_venue:
            by_venue[v].sort(key=lambda x: x["time_iso"])

        lines = []
        lines.append(f"✅ SevenRooms availability found")
        lines.append(f"Date: {target_date.strftime('%Y-%m-%d')}")
        lines.append(f"Window: {window_start.strftime('%H:%M')}–{window_end.strftime('%H:%M')}")
        lines.append(f"Party size: {party_size}")
        lines.append("")

        for v, items in by_venue.items():
            lines.append(f"• {v}")
            for it in items[:25]:
                # Prefer the human label, fall back to parsed time
                pretty = it["time_label"] or parse_time_from_time_iso(it["time_iso"]).strftime("%H:%M")
                lines.append(f"  - {pretty} ({it['time_iso']})")
            if len(items) > 25:
                lines.append(f"  …plus {len(items)-25} more")
            lines.append("")

        message = "\n".join(lines).strip()

        # Send push
        ntfy_publish(
            server=ntfy_server,
            topic=ntfy_topic,
            title="Reservation slot found",
            message=message,
            priority=ntfy_priority,
            tags=ntfy_tags
        )

        # Update state
        for m in all_matches:
            notified.add(m["key"])

        # Keep bounded
        state_out = {"notified": list(notified)[-1000:]}
        save_json(state_path, state_out)

        print(f"[OK] Notified {len(all_matches)} new slot(s).")
    else:
        print("[OK] No new matching availability.")


if __name__ == "__main__":
    main()
