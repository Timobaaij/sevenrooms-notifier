
import os
import json
import time
import datetime as dt
import requests
import re
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

CONFIG_FILE_PATH = "config.json"
STATE_FILE_PATH = "state.json"


# ----------------------------
# Utilities
# ----------------------------
def load_json(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path: str, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)


def normalize_iso_dates(values):
    out = []
    for v in values or []:
        if isinstance(v, dt.date):
            out.append(v.isoformat())
        else:
            s = str(v).strip()
            if not s:
                continue
            parsed = None
            for fmt in ("%Y-%m-%d", "%d-%m-%Y"):
                try:
                    parsed = dt.datetime.strptime(s, fmt).date().isoformat()
                    break
                except Exception:
                    pass
            if parsed:
                out.append(parsed)
    return sorted(set(out))


def get_dates_from_search(s: dict):
    if isinstance(s.get("dates"), list) and s["dates"]:
        d = normalize_iso_dates(s["dates"])
        if d:
            return d
    if s.get("date"):
        d = normalize_iso_dates([s["date"]])
        if d:
            return d
    return [dt.date.today().isoformat()]


def hhmm_to_minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def in_window(hhmm: str, start_hhmm: str, end_hhmm: str) -> bool:
    # inclusive window
    t = hhmm_to_minutes(hhmm)
    s = hhmm_to_minutes(start_hhmm)
    e = hhmm_to_minutes(end_hhmm)
    return s <= t <= e


# ----------------------------
# SevenRooms availability fetch
# ----------------------------
def fetch_sevenrooms_availability(venue: str, date_yyyy_mm_dd: str, party: int, channel: str, num_days: int, lang: str):
    """
    Uses the same endpoint pattern your Streamlit UI uses for time loading,
    but returns raw time objects to decide availability + requestability.
    """
    try:
        d_sr = dt.datetime.strptime(date_yyyy_mm_dd, "%Y-%m-%d").strftime("%m-%d-%Y")
    except Exception:
        return []

    url = (
        "https://www.sevenrooms.com/api-yoa/availability/widget/range"
        f"?venue={venue}&party_size={party}&start_date={d_sr}&num_days={num_days}"
        f"&channel={channel}&lang={lang}"
    )

    try:
        r = requests.get(url, timeout=20)
    except Exception:
        return []

    if not r.ok:
        return []

    try:
        j = r.json()
    except Exception:
        return []

    availability = (j.get("data", {}) or {}).get("availability", {}) or {}
    out = []

    for _, day in availability.items():
        if not isinstance(day, list):
            continue
        for block in day:
            block = block or {}
            for t in block.get("times", []) or []:
                if not isinstance(t, dict):
                    continue
                iso = t.get("time_iso") or t.get("date_time") or t.get("time")
                if not iso:
                    continue

                hhmm = None
                try:
                    hhmm = dt.datetime.fromisoformat(str(iso).replace("Z", "+00:00")).strftime("%H:%M")
                except Exception:
                    m = re.search(r"\b([01]\d|2[0-3]):([0-5]\d)\b", str(iso))
                    hhmm = f"{m.group(1)}:{m.group(2)}" if m else None

                if not hhmm:
                    continue

                out.append({
                    "hhmm": hhmm,
                    "is_available": bool(t.get("is_available")),
                    "is_requestable": bool(t.get("is_requestable")),
                    "raw": t
                })

    # unique by hhmm+flags
    seen = set()
    uniq = []
    for item in out:
        key = (item["hhmm"], item["is_available"], item["is_requestable"])
        if key not in seen:
            uniq.append(item)
            seen.add(key)
    return uniq


# ----------------------------
# Telegram notification
# ----------------------------
def send_telegram(bot_token: str, chat_id: str, text: str, parse_mode: str = "HTML", disable_web_page_preview: bool = True) -> (bool, str):
    if not bot_token or not chat_id:
        return False, "Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID"

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": disable_web_page_preview
    }

    try:
        r = requests.post(url, json=payload, timeout=20)
        if r.ok:
            return True, "OK"
        return False, f"HTTP {r.status_code}: {r.text[:200]}"
    except Exception as e:
        return False, str(e)


# ----------------------------
# Email notification (optional)
# ----------------------------
def send_email(to_addr: str, subject: str, body: str) -> (bool, str):
    if not to_addr:
        return False, "Missing recipient"

    user = os.getenv("EMAIL_USER", "").strip()
    pw = os.getenv("EMAIL_PASS", "").strip()
    host = os.getenv("SMTP_HOST", "smtp.gmail.com").strip()
    port = int(os.getenv("SMTP_PORT", "587").strip())

    if not user or not pw:
        return False, "Missing EMAIL_USER / EMAIL_PASS"

    msg = MIMEMultipart()
    msg["From"] = user
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    try:
        with smtplib.SMTP(host, port, timeout=20) as server:
            server.starttls()
            server.login(user, pw)
            server.sendmail(user, to_addr, msg.as_string())
        return True, "OK"
    except Exception as e:
        return False, str(e)


# ----------------------------
# Main run
# ----------------------------
def main():
    cfg = load_json(CONFIG_FILE_PATH, {})
    state = load_json(STATE_FILE_PATH, {"notified": []})
    state.setdefault("notified", [])

    gbl = cfg.get("global", {}) or {}
    channel = gbl.get("channel", "SEVENROOMS_WIDGET")
    lang = gbl.get("lang", "en")
    delay_between_venues_sec = float(gbl.get("delay_between_venues_sec", 0.5) or 0.5)

    tg = cfg.get("telegram_default", {}) or {}
    tg_token = (os.getenv("TELEGRAM_BOT_TOKEN") or tg.get("bot_token") or "").strip()
    tg_chat = (os.getenv("TELEGRAM_CHAT_ID") or tg.get("chat_id") or "").strip()
    tg_parse = (tg.get("parse_mode") or "HTML").strip()
    tg_no_preview = bool(tg.get("disable_web_page_preview", True))

    searches = cfg.get("searches", []) or []
    if not searches:
        print("No searches configured.")
        return

    notified_set = set(state.get("notified") or [])

    fired_any = False

    for s in searches:
        platform = (s.get("platform") or "sevenrooms").lower()
        if platform != "sevenrooms":
            continue

        search_id = (s.get("id") or "Unnamed").strip()
        venues = [v.strip() for v in (s.get("venues") or []) if str(v).strip()]
        party = int(s.get("party_size", 2) or 2)
        num_days = int(s.get("num_days", 1) or 1)
        notify = (s.get("notify") or "both").lower().strip()
        email_to = (s.get("email_to") or "").strip()

        time_slot = (s.get("time_slot") or "").strip()
        window_start = (s.get("window_start") or "").strip()
        window_end = (s.get("window_end") or "").strip()

        dates = get_dates_from_search(s)

        for date_iso in dates:
            for venue in venues:
                times = fetch_sevenrooms_availability(
                    venue=venue,
                    date_yyyy_mm_dd=date_iso,
                    party=party,
                    channel=channel,
                    num_days=num_days,
                    lang=lang
                )

                for t in times:
                    hhmm = t["hhmm"]
                    is_avail = t["is_available"]
                    is_req = t["is_requestable"]

                    # Only trigger on actual availability OR requestable
                    if not (is_avail or is_req):
                        continue

                    # Filter by exact time or window
                    ok_time = False
                    if time_slot:
                        ok_time = (hhmm == time_slot)
                    else:
                        if window_start and window_end:
                            try:
                                ok_time = in_window(hhmm, window_start, window_end)
                            except Exception:
                                ok_time = False
                        else:
                            # if no time criteria at all, accept any
                            ok_time = True

                    if not ok_time:
                        continue

                    # Dedupe key
                    key = f"{search_id}|{venue}|{date_iso}|{hhmm}|party={party}|avail={int(is_avail)}|req={int(is_req)}"
                    if key in notified_set:
                        continue

                    fired_any = True
                    notified_set.add(key)

                    status = "✅ AVAILABLE" if is_avail else "🟠 REQUESTABLE"
                    msg = (
                        f"<b>{status}</b>\n"
                        f"<b>{search_id}</b>\n"
                        f"Venue: <code>{venue}</code>\n"
                        f"Date: <code>{date_iso}</code>\n"
                        f"Time: <code>{hhmm}</code>\n"
                        f"Party: <code>{party}</code>\n"
                    )

                    # Telegram
                    if notify in ("push", "both"):
                        ok, info = send_telegram(
                            bot_token=tg_token,
                            chat_id=tg_chat,
                            text=msg,
                            parse_mode=tg_parse,
                            disable_web_page_preview=tg_no_preview
                        )
                        print(f"Telegram: {ok} ({info})")

                    # Email
                    if notify in ("email", "both") and email_to:
                        subj = f"Reservation slot found: {search_id} {date_iso} {hhmm}"
                        body = f"{status}\n{search_id}\nVenue: {venue}\nDate: {date_iso}\nTime: {hhmm}\nParty: {party}\n"
                        ok, info = send_email(email_to, subj, body)
                        print(f"Email: {ok} ({info})")

                time.sleep(delay_between_venues_sec)

    # Persist state (trim to keep file small)
    state["notified"] = list(notified_set)[-2000:]
    save_json(STATE_FILE_PATH, state)

    if not fired_any:
        print("No matching availability found.")


if __name__ == "__main__":
    main()
