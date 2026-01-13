
import os
import json
import hashlib
import datetime as dt
import time
import requests
import smtplib
from email.message import EmailMessage
from typing import Any, List, Optional, Tuple


def load_json(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path: str, obj: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)


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
    d = _parse_iso(value)
    if d:
        return d.strftime("%H:%M")
    import re
    m = re.search(r"\b([01]\d|2[0-3]):([0-5]\d)\b", value or "")
    return f"{m.group(1)}:{m.group(2)}" if m else None


def _parse_time(value: str) -> Optional[dt.time]:
    if not value:
        return None
    try:
        return dt.datetime.strptime(value.strip(), "%H:%M").time()
    except Exception:
        return None


def _in_window(hhmm: str, start: str, end: str) -> bool:
    if not (hhmm and start and end):
        return True
    tt, ts, te = _parse_time(hhmm), _parse_time(start), _parse_time(end)
    if not (tt and ts and te):
        return True
    if ts <= te:
        return ts <= tt <= te
    return tt >= ts or tt <= te


def send_push(server: str, topic: str, title: str, message: str, priority: str = "", tags: str = "") -> None:
    if not (server and topic):
        return
    headers = {"Title": title or "Reservation Alert"}
    if priority:
        headers["Priority"] = str(priority)
    if tags:
        headers["Tags"] = str(tags)
    url = f"{server.rstrip('/')}/{topic}"
    requests.post(url, data=message.encode("utf-8"), headers=headers, timeout=20)


def send_email(to_email: str, subject: str, body: str) -> None:
    user, pw = os.environ.get("EMAIL_USER"), os.environ.get("EMAIL_PASS")
    if not (user and pw and to_email):
        return
    msg = EmailMessage()
    msg.set_content(body)
    msg["Subject"], msg["From"], msg["To"] = subject, user, to_email
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(user, pw)
        s.send_message(msg)


def fetch_sevenrooms_slots(venue: str, date_yyyy_mm_dd: str, party: int, channel: str, num_days: int = 1, lang: str = "en") -> List[Tuple[str, str]]:
    """Return list of (slot_iso, kind) where kind is AVAILABLE or REQUEST."""
    try:
        d_sr = dt.datetime.strptime(date_yyyy_mm_dd, "%Y-%m-%d").strftime("%m-%d-%Y")
    except Exception:
        return []

    url = (
        "https://www.sevenrooms.com/api-yoa/availability/widget/range"
        f"?venue={venue}&party_size={party}&start_date={d_sr}&num_days={num_days}"
        f"&channel={channel}&lang={lang}"
    )

    r = requests.get(url, timeout=25)
    j = r.json() if r.ok else {}

    out: List[Tuple[str, str]] = []
    availability = (j.get("data", {}) or {}).get("availability", {}) or {}
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
                iso = t.get("time_iso") or t.get("date_time") or t.get("time")
                if not iso:
                    continue
                out.append((str(iso), "AVAILABLE" if is_avail else "REQUEST"))

    return out


def fetch_opentable_slots(rid: str, date_yyyy_mm_dd: str, party: int) -> List[str]:
    url = (
        "https://www.opentable.com/api/v2/reservation/availability"
        f"?rid={rid}&partySize={party}&dateTime={date_yyyy_mm_dd}T19:00"
    )
    headers = {"User-Agent": "Mozilla/5.0"}
    r = requests.get(url, headers=headers, timeout=25)
    if not r.ok:
        return []

    j = r.json()
    slots: List[str] = []

    def walk(x: Any) -> None:
        if isinstance(x, dict):
            if "dateTime" in x and x.get("isAvailable") is True:
                slots.append(str(x.get("dateTime")))
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(j)

    seen = set()
    uniq = []
    for s in slots:
        if s not in seen:
            uniq.append(s)
            seen.add(s)
    return uniq


def main() -> None:
    config = load_json("config.json", {"searches": []})
    state = load_json("state.json", {"notified": []})
    notified = set(state.get("notified", []))

    global_cfg = config.get("global", {}) or {}
    channel = global_cfg.get("channel", "SEVENROOMS_WIDGET")
    lang = global_cfg.get("lang", "en")
    delay = float(global_cfg.get("delay_between_venues_sec", 0.5) or 0.5)

    ntfy_default = config.get("ntfy_default", {}) or {}
    d_server = ntfy_default.get("server", "https://ntfy.sh")
    d_topic = ntfy_default.get("topic", "")
    d_priority = ntfy_default.get("priority", "")
    d_tags = ntfy_default.get("tags", "")

    for s in config.get("searches", []):
        sid = s.get("id") or "Unnamed"
        platform = (s.get("platform") or "sevenrooms").lower()
        venues = s.get("venues") or []
        date = s.get("date")
        party = int(s.get("party_size") or 2)
        salt = str(s.get("salt", ""))

        time_slot = (s.get("time_slot") or "").strip()
        window_start = (s.get("window_start") or "").strip()
        window_end = (s.get("window_end") or "").strip()
        num_days = int(s.get("num_days") or 1)

        ntfy = s.get("ntfy", {}) or {}
        server = ntfy.get("server") or d_server
        topic = ntfy.get("topic") or d_topic
        priority = ntfy.get("priority") or d_priority
        tags = ntfy.get("tags") or d_tags
        title = ntfy.get("title") or f"Table Alert: {sid}"

        found: List[str] = []

        for v in venues:
            v = str(v).strip()
            if not v:
                continue

            if platform == "opentable":
                slots = [(iso, "AVAILABLE") for iso in fetch_opentable_slots(v, date, party)]
            else:
                slots = fetch_sevenrooms_slots(v, date, party, channel=channel, num_days=num_days, lang=lang)

            for slot_iso, kind in slots:
                hh = _hhmm(slot_iso) or slot_iso

                if time_slot:
                    if (_hhmm(slot_iso) or "") != time_slot:
                        continue
                else:
                    if not _in_window((_hhmm(slot_iso) or ""), window_start, window_end):
                        continue

                fp = hashlib.sha256(f"{sid}|{platform}|{v}|{slot_iso}|{salt}".encode()).hexdigest()
                if fp in notified:
                    continue
                notified.add(fp)

                line = f"{v} @ {hh}"
                if kind and kind != "AVAILABLE":
                    line += f" ({kind})"
                found.append(line)

            if delay:
                time.sleep(delay)

        if found:
            summary = [f"Date: {date}", f"Party: {party}"]
            if time_slot:
                summary.append(f"Time: {time_slot}")
            else:
                summary.append(f"Window: {window_start or '?'}–{window_end or '?'}")

            msg = f"{sid} — " + " | ".join(summary) + "\n" + "\n".join(found)

            if topic:
                send_push(server, topic, title, msg, priority=priority, tags=tags)
            if s.get("email_to"):
                send_email(s.get("email_to"), title, msg)

    save_json("state.json", {"notified": list(notified)[-2000:]})


if __name__ == "__main__":
    main()
