# =========================
# main.py
# =========================
import os
import json
import hashlib
import datetime as dt
import time
import requests
import smtplib
from email.message import EmailMessage
from typing import Any, List, Optional, Tuple

# Push is implemented as an email to your Pushover email gateway
PUSH_EMAIL_TO = "bfxfnhvuie@pomail.net"

# =========================================================
# JSON HELPERS
# =========================================================
def load_json(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path: str, obj: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)


# =========================================================
# TIME HELPERS
# =========================================================
def _parse_iso(value: str) -> Optional[dt.datetime]:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        try:
            return dt.datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S")
        except Exception:
            return None


def _hhmm(value: str) -> Optional[str]:
    """
    Convert ISO datetime or string with time to HH:MM.
    Supports:
    - ISO strings: 2026-01-14T18:00:00Z
    - 24h: 18:00
    - 12h: 9:45 PM
    """
    d = _parse_iso(value)
    if d:
        return d.strftime("%H:%M")

    import re

    # 24-hour HH:MM
    m = re.search(r"\b([01]\d|2[0-3]):([0-5]\d)\b", value or "")
    if m:
        return f"{m.group(1)}:{m.group(2)}"

    # 12-hour h:MM AM/PM
    m = re.search(r"\b(\d{1,2}):([0-5]\d)\s*([AP]M)\b", (value or ""), re.I)
    if m:
        hh = int(m.group(1)) % 12
        if m.group(3).upper() == "PM":
            hh += 12
        return f"{hh:02d}:{m.group(2)}"
    return None


def _parse_time(value: str) -> Optional[dt.time]:
    if not value:
        return None
    try:
        return dt.datetime.strptime(value.strip(), "%H:%M").time()
    except Exception:
        return None


def _in_window(hhmm: str, start: str, end: str) -> bool:
    """Check if hhmm is inside [start, end], supports overnight."""
    if not (hhmm and start and end):
        return True
    tt = _parse_time(hhmm)
    ts = _parse_time(start)
    te = _parse_time(end)
    if not (tt and ts and te):
        return True
    if ts <= te:
        return ts <= tt <= te
    else:
        # overnight window (e.g. 22:00–01:00)
        return tt >= ts or tt <= te


# =========================================================
# DATE HELPERS (multi-date support)
# =========================================================
def _parse_one_date(value: str) -> Optional[str]:
    """
    Accepts:
    - YYYY-MM-DD
    - DD-MM-YYYY
    Returns normalized YYYY-MM-DD or None.
    """
    if not value:
        return None
    v = str(value).strip()
    if not v:
        return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y"):
        try:
            d = dt.datetime.strptime(v, fmt).date()
            return d.isoformat()
        except Exception:
            pass
    return None


def _get_search_dates(search: dict) -> List[str]:
    """
    Backwards compatible:
    - if 'dates' exists: list of dates
    - else use 'date'
    """
    out: List[str] = []
    dates_raw = search.get("dates", None)
    if isinstance(dates_raw, list):
        for x in dates_raw:
            d = _parse_one_date(x)
            if d:
                out.append(d)
    elif isinstance(dates_raw, str) and dates_raw.strip():
        for part in dates_raw.split(","):
            d = _parse_one_date(part)
            if d:
                out.append(d)
    else:
        d = _parse_one_date(search.get("date", ""))
        if d:
            out.append(d)
    return sorted(set(out))


# =========================================================
# NOTIFICATION SENDERS (Email only; Push is email to Pushover gateway)
# =========================================================
def send_email(to_email: str, subject: str, body: str, debug: bool = False) -> bool:
    user = os.environ.get("EMAIL_USER")
    pw = os.environ.get("EMAIL_PASS")
    if not (user and pw and to_email):
        if debug:
            print("[email] missing EMAIL_USER/EMAIL_PASS or to_email")
        return False

    msg = EmailMessage()
    msg.set_content(body)
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to_email

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(user, pw)
            s.send_message(msg)
        return True
    except Exception as e:
        if debug:
            print(f"[email] error: {e}")
        return False


# =========================================================
# AVAILABILITY FETCHERS (SevenRooms only)
# =========================================================
def is_bookable_time(t: dict) -> bool:
    """
    Robust bookability:
    - If is_available is present: must be True
    - Else: exclude requestable/waitlist
    - Requires some time field
    """
    if "is_available" in t:
        return t.get("is_available") is True
    if t.get("is_requestable") is True:
        return False
    if t.get("is_waitlist") is True:
        return False
    return bool(t.get("time_iso") or t.get("date_time") or t.get("time"))


def fetch_sevenrooms_slots(
    venue: str,
    date_yyyy_mm_dd: str,
    party: int,
    channel: str,
    lang: str = "en",
    halo_size_interval: int = 64,
    debug: bool = False,
) -> List[Tuple[str, str]]:
    """
    Returns list of tuples: (slot_date_YYYY_MM_DD, slot_iso_string)

    NOTE:
    - num_days has been removed entirely.
    - We still read the API response by date key so labels/dedupe remain correct.
    """
    try:
        d_sr = dt.datetime.strptime(date_yyyy_mm_dd, "%Y-%m-%d").strftime("%m-%d-%Y")
    except Exception:
        return []

    url = (
        "https://www.sevenrooms.com/api-yoa/availability/widget/range"
        f"?venue={venue}"
        f"&party_size={party}"
        f"&start_date={d_sr}"
        f"&channel={channel}"
        f"&selected_lang_code={lang}"
        f"&halo_size_interval={halo_size_interval}"
    )
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json,text/plain,*/*"}

    try:
        r = requests.get(url, headers=headers, timeout=25)
    except Exception as e:
        if debug:
            print(f"[sevenrooms] request error {venue}: {e}")
        return []

    if debug:
        print(f"[sevenrooms] {venue} HTTP {r.status_code} url={url}")

    if not r.ok:
        if debug:
            print(f"[sevenrooms] {venue} non-OK HTTP {r.status_code} body={r.text[:200]}")
        return []

    ct = (r.headers.get("Content-Type") or "").lower()
    if "json" not in ct:
        if debug:
            print(f"[sevenrooms] {venue} non-JSON content-type={ct} first200={r.text[:200]}")
        return []

    try:
        j = r.json()
    except Exception as e:
        if debug:
            print(f"[sevenrooms] {venue} JSON parse error: {e} first200={r.text[:200]}")
        return []

    avail = (j.get("data", {}) or {}).get("availability", {}) or {}
    out: List[Tuple[str, str]] = []

    for day_key, day_blocks in avail.items():
        # day_key is typically "MM-DD-YYYY"
        try:
            slot_day = dt.datetime.strptime(str(day_key), "%m-%d-%Y").date().isoformat()
        except Exception:
            slot_day = date_yyyy_mm_dd

        if not isinstance(day_blocks, list):
            continue

        for block in day_blocks:
            if not isinstance(block, dict):
                continue
            if block.get("is_closed") is True:
                continue

            for t in block.get("times", []) or []:
                if not isinstance(t, dict):
                    continue
                if not is_bookable_time(t):
                    continue

                iso = t.get("time_iso") or t.get("date_time") or t.get("time")
                if iso:
                    out.append((slot_day, str(iso)))

    return out


# =========================================================
# MAIN SCHEDULER LOGIC
# =========================================================
def main() -> None:
    config = load_json("config.json", {"searches": []})
    state = load_json("state.json", {"notified": []})
    notified = set(state.get("notified", []))

    global_cfg = config.get("global", {}) or {}
    channel = global_cfg.get("channel", "SEVENROOMS_WIDGET")
    lang = global_cfg.get("lang", "en")
    delay = float(global_cfg.get("delay_between_venues_sec", 0.5))
    halo = int(global_cfg.get("halo_size_interval", 64))
    debug = bool(global_cfg.get("debug", False))

    for search in config.get("searches", []):
        sid = search.get("id") or "Unnamed"
        platform = (search.get("platform") or "sevenrooms").lower()
        if platform != "sevenrooms":
            if debug:
                print(f"[{sid}] skipping unsupported platform={platform}")
            continue

        venues = search.get("venues") or []
        party = int(search.get("party_size") or 2)

        # multi-date
        dates = _get_search_dates(search)
        if not dates:
            if debug:
                print(f"[{sid}] no valid date(s) found; skipping")
            continue

        time_slot = (search.get("time_slot") or "").strip()
        window_start = (search.get("window_start") or "").strip()
        window_end = (search.get("window_end") or "").strip()

        notify_mode = (search.get("notify") or "both").lower()
        email_to = (search.get("email_to") or "").strip()
        salt = str(search.get("salt") or "")

        candidates: List[Tuple[str, str]] = []

        for date in dates:
            for v in venues:
                v = str(v).strip()
                if not v:
                    continue

                slot_rows = fetch_sevenrooms_slots(
                    v,
                    date,
                    party,
                    channel=channel,
                    lang=lang,
                    halo_size_interval=halo,
                    debug=debug,
                )

                if debug:
                    print(f"[{sid}] venue={v} date={date} raw_slots={len(slot_rows)}")

                for slot_day, iso in slot_rows:
                    hh = _hhmm(iso) or iso

                    if time_slot:
                        if (_hhmm(iso) or "") != time_slot:
                            continue
                    else:
                        if not _in_window((_hhmm(iso) or ""), window_start, window_end):
                            continue

                    fp = hashlib.sha256(
                        f"{sid}\n{platform}\n{v}\n{slot_day}\n{iso}\n{salt}".encode()
                    ).hexdigest()

                    if fp in notified:
                        continue

                    candidates.append((fp, f"{slot_day} — {v} @ {hh}"))

                if delay:
                    time.sleep(delay)

        if candidates and notify_mode != "none":
            summary = [f"Dates: {', '.join(dates)}", f"Party: {party}"]
            if time_slot:
                summary.append(f"Time: {time_slot}")
            else:
                summary.append(f"Window: {window_start or '?'}–{window_end or '?'}")

            found_lines = [label for _, label in candidates]
            msg = f"{sid} — " + "\n".join(summary) + "\n" + "\n".join(found_lines)

            push_ok = False
            email_ok = False

            # Push = email to Pushover gateway
            if notify_mode in ("push", "both"):
                push_ok = send_email(
                    PUSH_EMAIL_TO,
                    f"Table Alert: {sid}",
                    msg,
                    debug=debug
                )

            # Email = email to per-search email_to
            if notify_mode in ("email", "both") and email_to:
                email_ok = send_email(email_to, f"Table Alert: {sid}", msg, debug=debug)

            # Only mark notified if at least one delivery succeeded
            if push_ok or email_ok:
                for fp, _ in candidates:
                    notified.add(fp)
            else:
                if debug:
                    print(f"[notify] FAILED (not marking notified) sid={sid}")

    save_json("state.json", {"notified": list(notified)[-2000:]})


if __name__ == "__main__":
    main()
