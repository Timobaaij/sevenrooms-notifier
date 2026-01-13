
import os
import json
import hashlib
import datetime as dt
import time
import requests
import smtplib
from email.message import EmailMessage
from typing import Any, List, Optional, Tuple


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
    """Convert ISO datetime or string with time to HH:MM."""
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
        return tt >= ts or tt <= te


# =========================================================
# NOTIFICATION SENDERS
# =========================================================
def send_push(server: str, topic: str, title: str, message: str,
              priority: str = "", tags: str = "") -> None:
    if not (server and topic):
        return
    headers = {"Title": title or "Reservation Alert"}
    if priority:
        headers["Priority"] = str(priority)
    if tags:
        headers["Tags"] = str(tags)

    url = f"{server.rstrip('/')}/{topic}"
    try:
        requests.post(url, data=message.encode("utf-8"), headers=headers, timeout=20)
    except Exception:
        pass


def send_email(to_email: str, subject: str, body: str) -> None:
    user = os.environ.get("EMAIL_USER")
    pw = os.environ.get("EMAIL_PASS")
    if not (user and pw and to_email):
        return

    msg = EmailMessage()
    msg.set_content(body)
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to_email

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(user, pw)
            s.send_message(msg)
    except Exception:
        pass


# =========================================================
# AVAILABILITY FETCHERS
# =========================================================
def fetch_sevenrooms_slots(
    venue: str,
    date_yyyy_mm_dd: str,
    party: int,
    channel: str,
    num_days: int = 1,
    lang: str = "en"
) -> List[str]:
    """
    Returns list of actual *bookable* slots (NOT requestable).
    """
    try:
        d_sr = dt.datetime.strptime(date_yyyy_mm_dd, "%Y-%m-%d").strftime("%m-%d-%Y")
    except Exception:
        return []

    url = (
        "https://www.sevenrooms.com/api-yoa/availability/widget/range"
        f"?venue={venue}&party_size={party}&start_date={d_sr}"
        f"&num_days={num_days}&channel={channel}&lang={lang}"
    )

    try:
        r = requests.get(url, timeout=25)
        j = r.json() if r.ok else {}
    except Exception:
        return []

    out = []
    avail = (j.get("data", {}) or {}).get("availability", {}) or {}

    for _, day_blocks in avail.items():
        if not isinstance(day_blocks, list):
            continue
        for block in day_blocks:
            for t in block.get("times", []):
                if not isinstance(t, dict):
                    continue

                # strict: only notify on real availability
                if not bool(t.get("is_available")):
                    continue

                iso = (
                    t.get("time_iso")
                    or t.get("date_time")
                    or t.get("time")
                )
                if iso:
                    out.append(str(iso))

    return out


def fetch_opentable_slots(rid: str, date_yyyy_mm_dd: str, party: int) -> List[str]:
    """
    Returns bookable ISO datetimes from OpenTable.
    """
    url = (
        "https://www.opentable.com/api/v2/reservation/availability"
        f"?rid={rid}&partySize={party}&dateTime={date_yyyy_mm_dd}T19:00"
    )
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, headers=headers, timeout=25)
        j = r.json() if r.ok else {}
    except Exception:
        return []

    slots = []

    def walk(x):
        if isinstance(x, dict):
            if "dateTime" in x and x.get("isAvailable") is True:
                slots.append(str(x["dateTime"]))
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(j)

    # de-dupe but preserve order
    seen, uniq = set(), []
    for s in slots:
        if s not in seen:
            uniq.append(s)
            seen.add(s)
    return uniq


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

    ntfy_default = config.get("ntfy_default", {}) or {}
    d_server = ntfy_default.get("server", "")
    d_topic = ntfy_default.get("topic", "")
    d_priority = ntfy_default.get("priority", "")
    d_tags = ntfy_default.get("tags", "")

    for search in config.get("searches", []):
        sid = search.get("id") or "Unnamed"
        platform = (search.get("platform") or "sevenrooms").lower()
        venues = search.get("venues") or []
        date = search.get("date")
        party = int(search.get("party_size") or 2)
        num_days = int(search.get("num_days") or 1)

        time_slot = (search.get("time_slot") or "").strip()
        window_start = (search.get("window_start") or "").strip()
        window_end = (search.get("window_end") or "").strip()

        notify_mode = (search.get("notify") or "both").lower()
        email_to = search.get("email_to")

        salt = str(search.get("salt") or "")

        # notifier override
        ntfy = search.get("ntfy", {}) or {}
        server = ntfy.get("server") or d_server
        topic = ntfy.get("topic") or d_topic
        priority = ntfy.get("priority") or d_priority
        tags = ntfy.get("tags") or d_tags

        found = []

        for v in venues:
            v = str(v).strip()
            if not v:
                continue

            if platform == "opentable":
                iso_slots = fetch_opentable_slots(v, date, party)
            else:
                iso_slots = fetch_sevenrooms_slots(
                    v, date, party, channel=channel, num_days=num_days, lang=lang
                )

            for iso in iso_slots:
                hh = _hhmm(iso) or iso

                # time check
                if time_slot:
                    if (_hhmm(iso) or "") != time_slot:
                        continue
                else:
                    if not _in_window(_hhmm(iso) or "", window_start, window_end):
                        continue

                # dedupe
                fp = hashlib.sha256(
                    f"{sid}|{platform}|{v}|{iso}|{salt}".encode()
                ).hexdigest()

                if fp in notified:
                    continue

                notified.add(fp)
                found.append(f"{v} @ {hh}")

            if delay:
                time.sleep(delay)

        if found and notify_mode != "none":
            summary = [f"Date: {date}", f"Party: {party}"]
            if time_slot:
                summary.append(f"Time: {time_slot}")
            else:
                summary.append(f"Window: {window_start or '?'}–{window_end or '?'}")

            msg = (
                f"{sid} — " + " | ".join(summary) +
                "\n" + "\n".join(found)
            )

            if notify_mode in ("push", "both") and topic:
                send_push(server, topic, f"Table Alert: {sid}", msg, priority, tags)

            if notify_mode in ("email", "both") and email_to:
                send_email(email_to, f"Table Alert: {sid}", msg)

    save_json("state.json", {"notified": list(notified)[-2000:]})


if __name__ == "__main__":
    main()
``
