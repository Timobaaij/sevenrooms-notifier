
import os
import json
import hashlib
import datetime as dt
from typing import Dict, Any, List, Tuple, Set, Optional

import requests


SEVENROOMS_ENDPOINT = "https://www.sevenrooms.com/api-yoa/availability/widget/range"


def env(name: str, default: Optional[str] = None) -> str:
    val = os.getenv(name, default)
    if val is None or str(val).strip() == "":
        raise SystemExit(f"Missing required environment variable: {name}")
    return str(val).strip()


def env_int(name: str, default: int) -> int:
    v = os.getenv(name)
    return int(v) if v is not None and v.strip() != "" else default


def parse_date(d: str) -> dt.date:
    # Accept YYYY-MM-DD
    return dt.datetime.strptime(d, "%Y-%m-%d").date()


def to_mmddyyyy(d: dt.date) -> str:
    # SevenRooms widget endpoint often uses MM-DD-YYYY in the query string. [3](https://gist.github.com/JamesIves/ce8e9d5e83a17eeacdf46b5b7906382c/)
    return d.strftime("%m-%d-%Y")


def parse_time_24h(t: str) -> dt.time:
    return dt.datetime.strptime(t.strip(), "%H:%M").time()


def within_window(t: dt.time, start: dt.time, end: dt.time) -> bool:
    # inclusive start, exclusive end
    return (t >= start) and (t < end)


def safe_json_load(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except json.JSONDecodeError:
        return default


def safe_json_write(path: str, obj: Any) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, sort_keys=True)
    os.replace(tmp, path)


def ntfy_publish(topic: str, title: str, message: str) -> None:
    server = os.getenv("NTFY_SERVER", "https://ntfy.sh").strip()
    token = os.getenv("NTFY_TOKEN", "").strip()  # optional
    url = f"{server.rstrip('/')}/{topic}"

    headers = {
        "Title": title,
        "Priority": os.getenv("NTFY_PRIORITY", "high"),
        "Tags": os.getenv("NTFY_TAGS", "bell"),
    }
    if os.getenv("NTFY_MARKDOWN", "no").lower() in ("1", "true", "yes"):
        headers["Markdown"] = "yes"

    auth = None
    if token:
        # ntfy supports bearer token auth if you use it; optional. [5](https://docs.ntfy.sh/)
        headers["Authorization"] = f"Bearer {token}"

    r = requests.post(url, data=message.encode("utf-8"), headers=headers, timeout=20)
    r.raise_for_status()


def build_query(venue: str, party_size: int, start_date: dt.date, num_days: int, time_slot: str) -> Dict[str, str]:
    # This mirrors the commonly used widget availability endpoint pattern. [3](https://gist.github.com/JamesIves/ce8e9d5e83a17eeacdf46b5b7906382c/)
    return {
        "venue": venue,
        "time_slot": time_slot,  # e.g. "18:00"
        "party_size": str(party_size),
        "halo_size_interval": os.getenv("HALO_SIZE_INTERVAL", "64"),
        "start_date": to_mmddyyyy(start_date),
        "num_days": str(num_days),
        "channel": os.getenv("CHANNEL", "SEVENROOMS_WIDGET"),
        "selected_lang_code": os.getenv("LANG", "en"),
    }


def fetch_availability(params: Dict[str, str]) -> Dict[str, Any]:
    r = requests.get(SEVENROOMS_ENDPOINT, params=params, timeout=25)
    r.raise_for_status()
    return r.json()


def extract_bookable_times(payload: Dict[str, Any]) -> List[Tuple[str, str, str]]:
    """
    Returns list of (date_iso, time_label, time_iso_str) for bookable times.
    Filters out "requestable" slots that require calling. [3](https://gist.github.com/JamesIves/ce8e9d5e83a17eeacdf46b5b7906382c/)
    """
    out: List[Tuple[str, str, str]] = []
    if payload.get("status") != 200:
        return out

    data = payload.get("data") or {}
    availability = data.get("availability") or {}
    # availability: { "YYYY-MM-DD": [ {times: [...]}, ... ] }
    for date_iso, slots in availability.items():
        if not isinstance(slots, list):
            continue
        for slot in slots:
            if slot.get("is_closed") is True:
                continue
            times = slot.get("times") or []
            for t in times:
                # Keep times that are not requestable-only.
                if t.get("is_requestable") is True:
                    continue
                time_label = str(t.get("time") or "").strip()
                time_iso = str(t.get("time_iso") or "").strip()  # e.g. "2025-05-30 21:45:00" [3](https://gist.github.com/JamesIves/ce8e9d5e83a17eeacdf46b5b7906382c/)
                if not time_iso and time_label:
                    # fallback: can't filter precisely, but still return label
                    time_iso = f"{date_iso} {time_label}"
                out.append((date_iso, time_label, time_iso))
    return out


def parse_time_from_time_iso(time_iso: str) -> Optional[dt.time]:
    # Best-effort: expect "YYYY-MM-DD HH:MM:SS" [3](https://gist.github.com/JamesIves/ce8e9d5e83a17eeacdf46b5b7906382c/)
    try:
        return dt.datetime.strptime(time_iso, "%Y-%m-%d %H:%M:%S").time()
    except Exception:
        return None


def slot_key(venue: str, party_size: int, date_iso: str, time_iso: str) -> str:
    raw = f"{venue}|{party_size}|{date_iso}|{time_iso}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def main():
    # --- Required config ---
    venue = env("SEVENROOMS_VENUE")
    topic = env("NTFY_TOPIC")

    # --- Reservation search config ---
    party_size = env_int("PARTY_SIZE", 2)
    num_days = env_int("NUM_DAYS", 3)

    start_date_str = os.getenv("START_DATE", "").strip()
    if start_date_str:
        start_date = parse_date(start_date_str)
    else:
        # Use today (UTC) by default; GitHub Actions schedules are UTC. [8](https://dev.to/britzdm/how-to-run-scheduled-cron-jobs-in-github-workflows-for-free-4pgn)[9](https://jackharner.com/blog/github-actions-cron/)
        start_date = dt.datetime.utcnow().date()

    # The API needs a time_slot parameter; it’s used as a “search anchor” in many examples. [3](https://gist.github.com/JamesIves/ce8e9d5e83a17eeacdf46b5b7906382c/)
    time_slot = os.getenv("TIME_SLOT", "18:00").strip()

    # Match logic (either exact times list or a time window)
    desired_times_csv = os.getenv("DESIRED_TIMES", "").strip()  # "19:00,19:30"
    window_start = os.getenv("WINDOW_START", "").strip()        # "18:30"
    window_end = os.getenv("WINDOW_END", "").strip()            # "21:00"

    desired_times: Set[dt.time] = set()
    if desired_times_csv:
        for part in desired_times_csv.split(","):
            desired_times.add(parse_time_24h(part))

    use_window = bool(window_start and window_end)
    w_start = parse_time_24h(window_start) if use_window else None
    w_end = parse_time_24h(window_end) if use_window else None

    # --- State to prevent repeat notifications ---
    state_path = os.getenv("STATE_PATH", "state.json")
    state = safe_json_load(state_path, default={"notified": []})
    notified: Set[str] = set(state.get("notified", []))

    # --- Fetch and evaluate ---
    params = build_query(venue, party_size, start_date, num_days, time_slot)
    payload = fetch_availability(params)
    times = extract_bookable_times(payload)

    matches: List[Tuple[str, str, str, str]] = []  # (date, label, iso, key)
    for date_iso, label, iso in times:
        t = parse_time_from_time_iso(iso)
        if desired_times and t:
            if t not in desired_times:
                continue
        if use_window and t and w_start and w_end:
            if not within_window(t, w_start, w_end):
                continue
        k = slot_key(venue, party_size, date_iso, iso)
        if k in notified:
            continue
        matches.append((date_iso, label, iso, k))

    if matches:
        lines = []
        for date_iso, label, iso, _k in matches[:20]:
            lines.append(f"- {date_iso} • {label} ({iso})")
        more = ""
        if len(matches) > 20:
            more = f"\n…plus {len(matches) - 20} more."
        msg = (
            f"✅ SevenRooms availability found\n\n"
            f"Venue: {venue}\nParty size: {party_size}\n\n"
            f"Slots:\n" + "\n".join(lines) + more
        )

        # Send push
        ntfy_publish(
            topic=topic,
            title="Reservation slot found",
            message=msg
        )

        # Mark notified
        for *_rest, k in matches:
            notified.add(k)

        # Keep state bounded
        state["notified"] = list(notified)[-500:]
        safe_json_write(state_path, state)

        print(f"Notified {len(matches)} new slot(s).")
    else:
        print("No new matching availability.")

    # Always write state (even if unchanged) is optional; we only write if we notified above.
    # If you want to persist 'last checked', you could extend state here.


if __name__ == "__main__":
    main()
