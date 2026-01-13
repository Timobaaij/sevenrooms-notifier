import os
import json
import hashlib
import datetime as dt
import requests
import smtplib
from email.message import EmailMessage
from typing import Any, Dict, List, Tuple, Optional


def load_json(path: str, default: Any):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path: str, obj: Any):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)


def _iso_to_dt(value: str) -> Optional[dt.datetime]:
    """Best-effort ISO parsing (handles Z)."""
    if not value:
        return None
    try:
        v = value.replace("Z", "+00:00")
        return dt.datetime.fromisoformat(v)
    except Exception:
        # Try common SevenRooms format: 2026-01-25T13:00:00
        try:
            return dt.datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S")
        except Exception:
            return None


def _hhmm_from_any(value: str) -> Optional[str]:
    if not value:
        return None
    d = _iso_to_dt(value)
    if d:
        return d.strftime("%H:%M")
    # fallback: find HH:MM
    import re
    m = re.search(r"\b([01]\d|2[0-3]):([0-5]\d)\b", value)
    return f"{m.group(1)}:{m.group(2)}" if m else None


def _parse_hhmm(value: str) -> Optional[dt.time]:
    if not value:
        return None
    try:
        return dt.datetime.strptime(value.strip(), "%H:%M").time()
    except Exception:
        return None


def _in_window(hhmm: str, start: Optional[str], end: Optional[str]) -> bool:
    if not hhmm:
        return False
    if not start or not end:
        return True
    t = _parse_hhmm(hhmm)
    s = _parse_hhmm(start)
    e = _parse_hhmm(end)
    if not t or not s or not e:
        return True
    # handle overnight windows defensively
    if s <= e:
        return s <= t <= e
    return t >= s or t <= e


def send_push(server: str, topic: str, title: str, message: str, priority: Optional[str] = None, tags: Optional[str] = None):
    if not (server and topic):
        return
    headers = {"Title": title or "Reservation Alert"}
    if priority:
        headers["Priority"] = str(priority)
    if tags:
        headers["Tags"] = str(tags)
    url = f"{server.rstrip('/')}/{topic}"
    requests.post(url, data=message.encode("utf-8"), headers=headers, timeout=15)


def send_email(to_email: str, subject: str, body: str):
    user, pw = os.environ.get("EMAIL_USER"), os.environ.get("EMAIL_PASS")
    if not user or not pw or not to_email:
        return
    msg = EmailMessage()
    msg.set_content(body)
    msg["Subject"], msg["From"], msg["To"] = subject, user, to_email
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(user, pw)
        s.send_message(msg)


def fetch_sevenrooms_slots(venue: str, date_yyyy_mm_dd: str, party: int, channel: str, num_days: int = 1, lang: str = "en") -> List[Tuple[str, str]]:
    """Returns list of (slot_iso, kind) where kind is AVAILABLE/REQUEST."""
    try:
        d_sr = dt.datetime.strptime(date_yyyy_mm_dd, "%Y-%m-%d").strftime("%m-%d-%Y")
    except Exception:
        return []

    url = (
        "https://www.sevenrooms.com/api-yoa/availability/widget/range"
        f"?venue={venue}&party_size={party}&start_date={d_sr}&num_days={num_days}"
        f"&channel={channel}&lang={lang}"
    )

    r = requests.get(url, timeout=20)
    data = r.json() if r.ok else {}

    out: List[Tuple[str, str]] = []
    availability = (data.get("data", {}) or {}).get("availability", {}) or {}

    # availability is usually dict keyed by date.
    for _, day in availability.items():
        if not isinstance(day, list):
            continue
        for block in day:
            times = (block or {}).get("times", [])
            if not isinstance(times, list):
                continue
            for t in times:
                if not isinstance(t, dict):
                    continue
                is_avail = bool(t.get("is_available"))
                is_req = bool(t.get("is_requestable"))
                if not (is_avail or is_req):
                    continue
                slot_iso = t.get("time_iso") or t.get("date_time") or t.get("time")
                if not slot_iso:
                    continue
                kind = "AVAILABLE" if is_avail else "REQUEST"
                out.append((slot_iso, kind))

    return out


def fetch_opentable_slots(rid: str, date_yyyy_mm_dd: str, party: int) -> List[str]:
    """Returns list of slot ISO strings from OpenTable API (best-effort)."""
    url = (
        "https://www.opentable.com/api/v2/reservation/availability"
        f"?rid={rid}&partySize={party}&dateTime={date_yyyy_mm_dd}T19:00"
    )
    headers = {"User-Agent": "Mozilla/5.0"}
    r = requests.get(url, headers=headers, timeout=20)
    if not r.ok:
        return []
    j = r.json()

    slots: List[str] = []

    def walk(x: Any):
        if isinstance(x, dict):
            # common pattern: {dateTime: ..., isAvailable: ...}
            if "dateTime" in x and x.get("isAvailable") is True:
                slots.append(str(x.get("dateTime")))
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(j)
    # de-dup while preserving order
    seen = set()
    uniq = []
    for s in slots:
        if s not in seen:
            uniq.append(s)
            seen.add(s)
    return uniq


def main():
    config = load_json("config.json", {"searches": []})
    state = load_json("state.json", {"notified": []})

    notified = set(state.get("notified", []))

    global_cfg = config.get("global", {}) or {}
    channel = global_cfg.get("channel", "SEVENROOMS_WIDGET")
    delay = float(global_cfg.get("delay_between_venues_sec", 0.5) or 0.5)
    lang = global_cfg.get("lang", "en")

    ntfy_default = config.get("ntfy_default", {}) or {}
    default_server = ntfy_default.get("server", "https://ntfy.sh")
    default_topic = ntfy_default.get("topic", "")
    default_priority = ntfy_default.get("priority", "")
    default_tags = ntfy_default.get("tags", "")

    for s in config.get("searches", []):
        sid = s.get("id") or "Unnamed"
        platform = (s.get("platform") or "sevenrooms").lower()
        venues = s.get("venues") or []
        date = s.get("date")
        party = int(s.get("party_size") or 2)
        salt = str(s.get("salt", ""))

        # Window / time filtering
        time_slot = (s.get("time_slot") or "").strip()
        window_start = (s.get("window_start") or "").strip()
        window_end = (s.get("window_end") or "").strip()
        num_days = int(s.get("num_days") or 1)

        # Notification overrides (per search)
        ntfy = s.get("ntfy", {}) or {}
        server = ntfy.get("server") or default_server
        topic = ntfy.get("topic") or default_topic
        priority = ntfy.get("priority") or default_priority
        tags = ntfy.get("tags") or default_tags
        title = ntfy.get("title") or f"Table Alert: {sid}"

        found_lines: List[str] = []

        for v in venues:
            v = str(v).strip()
            if not v:
                continue

            if platform == "opentable":
                slot_isos = fetch_opentable_slots(v, date, party)
                slots = [(iso, "AVAILABLE") for iso in slot_isos]
            else:
                slots = fetch_sevenrooms_slots(v, date, party, channel=channel, num_days=num_days, lang=lang)

            for slot_iso, kind in slots:
                hhmm = _hhmm_from_any(slot_iso)
                if time_slot:
                    if hhmm != time_slot:
                        continue
                else:
                    if not _in_window(hhmm or "", window_start, window_end):
                        continue

                fingerprint = hashlib.sha256(f"{sid}|{platform}|{v}|{slot_iso}|{salt}".encode()).hexdigest()
                if fingerprint in notified:
                    continue

                label = f"{v} @ {hhmm or slot_iso}"
                if kind and kind != "AVAILABLE":
                    label += f" ({kind})"
                found_lines.append(label)
                notified.add(fingerprint)

            # be gentle to endpoints
            if delay:
                import time
                time.sleep(delay)

        if found_lines:
            details = []
            if time_slot:
                details.append(f"Time: {time_slot}")
            else:
                details.append(f"Window: {window_start or '?'}–{window_end or '?'}")
            details.append(f"Date: {date}")
            details.append(f"Party: {party}")
            header = " | ".join(details)
            msg = f"{sid} — {header}\n" + "\n".join(found_lines)

            # Push + email
            if topic:
                send_push(server, topic, title, msg, priority=priority, tags=tags)
            if s.get("email_to"):
                send_email(s.get("email_to"), title, msg)

    # keep last 2000 fingerprints
    save_json("state.json", {"notified": list(notified)[-2000:]})


if __name__ == "__main__":
    main()
