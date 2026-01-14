
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
# NOTIFICATION SENDERS (return success)
# =========================================================
def send_push(
    server: str,
    topic: str,
    title: str,
    message: str,
    priority: str = "",
    tags: str = "",
    debug: bool = False,
) -> bool:
    if not (server and topic):
        return False

    headers = {"Title": title or "Reservation Alert"}
    if priority:
        headers["Priority"] = str(priority)
    if tags:
        headers["Tags"] = str(tags)

    url = f"{server.rstrip('/')}/{topic}"
    try:
        r = requests.post(url, data=message.encode("utf-8"), headers=headers, timeout=20)
        ok = 200 <= r.status_code < 300
        if debug and not ok:
            print(f"[push] HTTP {r.status_code} {r.text[:200]}")
        return ok
    except Exception as e:
        if debug:
            print(f"[push] error: {e}")
        return False


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
# AVAILABILITY FETCHERS
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
    num_days: int = 1,
    lang: str = "en",
    halo_size_interval: int = 64,
    debug: bool = False,
) -> List[str]:
    """
    Returns list of actual *bookable* slots (NOT requestable).
    Hardened against schema differences + non-JSON responses.
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
        f"&num_days={num_days}"
        f"&channel={channel}"
        f"&selected_lang_code={lang}"
        f"&halo_size_interval={halo_size_interval}"
    )

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json,text/plain,*/*",
    }

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

    if debug:
        try:
            print(f"[sevenrooms] {venue} availability_days={len(avail)}")
        except Exception:
            pass

    out: List[str] = []
    for _, day_blocks in avail.items():
        if not isinstance(day_blocks, list):
            continue
        for block in day_blocks:
            if not isinstance(block, dict):
                continue

            # block can be closed
            if block.get("is_closed") is True:
                continue

            for t in block.get("times", []) or []:
                if not isinstance(t, dict):
                    continue
                if not is_bookable_time(t):
                    continue

                iso = t.get("time_iso") or t.get("date_time") or t.get("time")
                if iso:
                    out.append(str(iso))

    return out


# -----------------------------
# OpenTable (UPDATED)
# -----------------------------
def fetch_opentable_slots(
    rid: str,
    date_yyyy_mm_dd: str,
    party: int,
    time_hints: Optional[List[str]] = None,
    debug: bool = False,
) -> List[str]:
    """
    Returns bookable ISO datetimes from OpenTable.

    Why it can return nothing even when the widget shows availability:
      - Marketplace host matters (UK often uses opentable.co.uk, not opentable.com).
      - Availability is anchored around the provided dateTime.
      - Some deployments are picky about timezone offsets.

    This implementation:
      - Tries multiple anchors via time_hints.
      - Tries multiple hosts (.co.uk and .com).
      - Tries dateTime both with and without +00:00.
      - Parses common schema variants (boolean flags + simple status strings).
    """
    hints = [h for h in (time_hints or []) if h]
    if not hints:
        hints = ["19:00"]

    hosts = [
        "https://www.opentable.co.uk",
        "https://www.opentable.com",
    ]

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "en-GB,en;q=0.9",
        "Referer": "https://www.opentable.com/",
        "Origin": "https://www.opentable.com",
        "X-Requested-With": "XMLHttpRequest",
    }

    def dt_candidates(hhmm: str) -> List[str]:
        hhmm = _hhmm(hhmm) or hhmm
        if not hhmm or len(hhmm) < 4:
            hhmm = "19:00"
        base = f"{date_yyyy_mm_dd}T{hhmm}:00"
        return [base, base + "+00:00"]

    slots: List[str] = []

    def walk_collect(x, out: List[str]) -> None:
        if isinstance(x, dict):
            dt_key = None
            for k in ("dateTime", "datetime", "date_time", "time"):
                if k in x:
                    dt_key = k
                    break

            avail = None
            for k in ("isAvailable", "is_available", "available"):
                if k in x:
                    avail = x.get(k)
                    break

            if avail is None and "status" in x:
                v = str(x.get("status") or "").lower()
                if v in ("available", "open"):
                    avail = True

            if dt_key and (avail is True):
                out.append(str(x.get(dt_key)))

            for v in x.values():
                walk_collect(v, out)

        elif isinstance(x, list):
            for v in x:
                walk_collect(v, out)

    for host in hosts:
        for h in hints:
            for dt_value in dt_candidates(h):
                url = host.rstrip("/") + "/api/v2/reservation/availability"
                params = {"rid": str(rid), "partySize": int(party), "dateTime": dt_value}

                try:
                    r = requests.get(url, params=params, headers=headers, timeout=25)
                except Exception as e:
                    if debug:
                        print(f"[opentable] request error {rid} host={host}: {e}")
                    continue

                if debug:
                    print(f"[opentable] {rid} host={host} HTTP {r.status_code} url={r.url}")

                if not r.ok:
                    if debug:
                        print(f"[opentable] non-OK HTTP {r.status_code} host={host} body={r.text[:400]}")
                    continue

                ct = (r.headers.get("Content-Type") or "").lower()
                if "json" not in ct:
                    if debug:
                        print(f"[opentable] non-JSON host={host} content-type={ct} first400={r.text[:400]}")
                    continue

                try:
                    j = r.json()
                except Exception as e:
                    if debug:
                        print(f"[opentable] JSON parse error host={host}: {e} first400={r.text[:400]}")
                    continue

                before = len(slots)
                walk_collect(j, slots)

                if debug and len(slots) == before:
                    try:
                        if isinstance(j, dict):
                            print(f"[opentable] no slots parsed host={host} keys={list(j.keys())[:25]}")
                    except Exception:
                        pass

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
    halo = int(global_cfg.get("halo_size_interval", 64))
    debug = bool(global_cfg.get("debug", False))

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

        # Collect candidates but do NOT mark notified until send succeeds
        candidates: List[Tuple[str, str]] = []  # (fp, label)

        for v in venues:
            v = str(v).strip()
            if not v:
                continue

            if platform == "opentable":
                # OpenTable availability is anchored around the provided dateTime.
                # Use a few anchors to reliably capture slots across the requested window.
                ot_hints: List[str] = []
                if time_slot:
                    ot_hints = [time_slot]
                elif window_start and window_end:
                    ot_hints = [window_start, window_end]
                    try:
                        ts = _parse_time(window_start)
                        te = _parse_time(window_end)
                        if ts and te:
                            base = dt.datetime(2000, 1, 1, ts.hour, ts.minute)
                            end = dt.datetime(2000, 1, 1, te.hour, te.minute)
                            if end < base:
                                end += dt.timedelta(days=1)
                            mid = base + (end - base) / 2
                            ot_hints.insert(1, mid.strftime("%H:%M"))
                    except Exception:
                        pass
                else:
                    ot_hints = ["19:00", "12:00", "21:00"]

                iso_slots = fetch_opentable_slots(v, date, party, time_hints=ot_hints, debug=debug)

            else:
                iso_slots = fetch_sevenrooms_slots(
                    v,
                    date,
                    party,
                    channel=channel,
                    num_days=num_days,
                    lang=lang,
                    halo_size_interval=halo,
                    debug=debug,
                )

            if debug:
                print(f"[{sid}] {platform} venue={v} raw_slots={len(iso_slots)}")

            for iso in iso_slots:
                hh = _hhmm(iso) or iso

                # time check
                if time_slot:
                    if (_hhmm(iso) or "") != time_slot:
                        continue
                else:
                    if not _in_window((_hhmm(iso) or ""), window_start, window_end):
                        continue

                fp = hashlib.sha256(
                    f"{sid}\n{platform}\n{v}\n{iso}\n{salt}".encode()
                ).hexdigest()

                if fp in notified:
                    continue

                candidates.append((fp, f"{v} @ {hh}"))

            if delay:
                time.sleep(delay)

        if candidates and notify_mode != "none":
            summary = [f"Date: {date}", f"Party: {party}"]
            if time_slot:
                summary.append(f"Time: {time_slot}")
            else:
                summary.append(f"Window: {window_start or '?'}–{window_end or '?'}")

            found_lines = [label for _, label in candidates]
            msg = (
                f"{sid} — " + " \n ".join(summary) +
                "\n" + "\n".join(found_lines)
            )

            push_ok = False
            email_ok = False

            if notify_mode in ("push", "both") and topic:
                push_ok = send_push(server, topic, f"Table Alert: {sid}", msg, priority, tags, debug=debug)

            if notify_mode in ("email", "both") and email_to:
                email_ok = send_email(email_to, f"Table Alert: {sid}", msg, debug=debug)

            # Only mark as notified if at least one channel succeeded
            if push_ok or email_ok:
                for fp, _ in candidates:
                    notified.add(fp)
            else:
                if debug:
                    print(f"[notify] FAILED (not marking notified) sid={sid}")

        save_json("state.json", {"notified": list(notified)[-2000:]})


if __name__ == "__main__":
    main()
