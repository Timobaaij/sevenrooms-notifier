# =========================
# main.py
# =========================
import os
import json
import hashlib
import datetime as dt
import time
import requests
import re
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
    if not value: return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        try:
            return dt.datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S")
        except Exception:
            return None

def _hhmm(value: str) -> Optional[str]:
    d = _parse_iso(value)
    if d: return d.strftime("%H:%M")
    m = re.search(r"\b([01]\d|2[0-3]):([0-5]\d)\b", value or "")
    if m: return f"{m.group(1)}:{m.group(2)}"
    m = re.search(r"\b(\d{1,2}):([0-5]\d)\s*([AP]M)\b", (value or ""), re.I)
    if m:
        hh = int(m.group(1)) % 12
        if m.group(3).upper() == "PM": hh += 12
        return f"{hh:02d}:{m.group(2)}"
    return None

def _parse_time(value: str) -> Optional[dt.time]:
    if not value: return None
    try:
        return dt.datetime.strptime(value.strip(), "%H:%M").time()
    except Exception:
        return None

def _in_window(hhmm: str, start: str, end: str) -> bool:
    if not (hhmm and start and end): return True
    tt = _parse_time(hhmm)
    ts = _parse_time(start)
    te = _parse_time(end)
    if not (tt and ts and te): return True
    if ts <= te:
        return ts <= tt <= te
    else:
        return tt >= ts or tt <= te

# =========================================================
# DATE HELPERS
# =========================================================
def _parse_one_date(value: str) -> Optional[str]:
    if not value: return None
    v = str(value).strip()
    if not v: return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y"):
        try:
            d = dt.datetime.strptime(v, fmt).date()
            return d.isoformat()
        except Exception: pass
    return None

def _get_search_dates(search: dict) -> List[str]:
    out: List[str] = []
    dates_raw = search.get("dates", None)
    if isinstance(dates_raw, list):
        for x in dates_raw:
            d = _parse_one_date(x)
            if d: out.append(d)
    elif isinstance(dates_raw, str) and dates_raw.strip():
        for part in dates_raw.split(","):
            d = _parse_one_date(part)
            if d: out.append(d)
    else:
        d = _parse_one_date(search.get("date", ""))
        if d: out.append(d)
    return sorted(set(out))

# =========================================================
# NOTIFICATION SENDERS
# =========================================================
def send_email(to_email: str, subject: str, body: str, debug: bool = False) -> bool:
    user = os.environ.get("EMAIL_USER")
    pw = os.environ.get("EMAIL_PASS")
    if not (user and pw and to_email): return False
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
    except Exception: return False

# =========================================================
# AVAILABILITY FETCHERS
# =========================================================
def is_bookable_time(t: dict) -> bool:
    if t.get("is_requestable") is True: return False
    if t.get("is_waitlist") is True: return False
    if "is_available" in t: return t.get("is_available") is True
    return bool(t.get("access_persistent_id"))

def _num_days_chunks(num_days: int) -> List[int]:
    """Split a day range into sizes the widget API actually accepts.

    SevenRooms only honours num_days of 1 or 3; anything else comes back as
    400 "invalid num_days", which this client cannot tell apart from "no
    tables". A 7-day search therefore used to alert on nothing at all. Walk
    the window in 3s with 1s for the remainder instead.
    """
    n = max(1, int(num_days))
    return [3] * (n // 3) + [1] * (n % 3)


def fetch_sevenrooms_slots(
    venue: str,
    date_yyyy_mm_dd: str,
    party: int,
    channel: str,
    num_days: int = 1,
    lang: str = "en",
    halo_size_interval: int = 64,
    debug: bool = False,
) -> List[Tuple[str, str]]:
    try:
        start = dt.datetime.strptime(date_yyyy_mm_dd, "%Y-%m-%d")
    except Exception: return []

    out: List[Tuple[str, str]] = []
    seen: set = set()
    offset = 0
    for chunk in _num_days_chunks(num_days):
        day = (start + dt.timedelta(days=offset)).strftime("%Y-%m-%d")
        offset += chunk
        for slot in _fetch_slots_once(
            venue, day, party, channel, chunk, lang, halo_size_interval, debug
        ):
            if slot in seen: continue
            seen.add(slot); out.append(slot)
    return out


def _fetch_slots_once(
    venue: str,
    date_yyyy_mm_dd: str,
    party: int,
    channel: str,
    num_days: int,
    lang: str,
    halo_size_interval: int,
    debug: bool,
) -> List[Tuple[str, str]]:
    try:
        d_sr = dt.datetime.strptime(date_yyyy_mm_dd, "%Y-%m-%d").strftime("%m-%d-%Y")
    except Exception: return []

    url = (
        "https://www.sevenrooms.com/api-yoa/availability/widget/range"
        f"?venue={venue}&party_size={party}&start_date={d_sr}&num_days={num_days}"
        f"&channel={channel}&selected_lang_code={lang}&halo_size_interval={halo_size_interval}"
    )
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json,text/plain,*/*"}

    try:
        r = requests.get(url, headers=headers, timeout=25)
    except Exception: return []

    if not r.ok: return []
    try: j = r.json()
    except Exception: return []

    avail = (j.get("data", {}) or {}).get("availability", {}) or {}
    out: List[Tuple[str, str]] = []
    for _, day_blocks in avail.items():
        if not isinstance(day_blocks, list): continue
        for block in day_blocks:
            if not isinstance(block, dict) or block.get("is_closed") is True: continue
            for t in block.get("times", []) or []:
                if not isinstance(t, dict) or not is_bookable_time(t): continue
                iso = t.get("time_iso") or t.get("date_time") or t.get("time")
                # seating area / room, e.g. "Restaurant Terrace", "Restaurant - Indoors"
                area = str(t.get("public_time_slot_description") or "").strip()
                if iso: out.append((str(iso), area))
    return out


# =========================================================
# OPENTABLE
# =========================================================
# Same philosophy as the SevenRooms client above: ask the public availability
# API the restaurant's own booking page asks, read the open times out of the
# JSON, hand back (iso, seating-area) pairs. Nothing is booked, nothing is held.
#
# Two differences OpenTable forces on us:
#   1. Venues are numeric restaurant ids ("rid"), not slugs. A slug or a pasted
#      URL is resolved to its rid once and cached for the run.
#   2. The web app sends its availability query as an Apollo *persisted query*
#      (a sha256 of the document, no document). That hash changes whenever
#      OpenTable redeploys, so it is configuration — OPENTABLE_AVAILABILITY_HASH
#      or global.opentable.availability_hash — not code. With no hash we send
#      the document itself, which is exactly what Apollo does when the server
#      answers PersistedQueryNotFound.
OPENTABLE_HOST = "https://www.opentable.com"
OPENTABLE_GQL = OPENTABLE_HOST + "/dapi/fe/gql?optype=query&opname=RestaurantsAvailability"
OPENTABLE_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
OPENTABLE_QUERY = """query RestaurantsAvailability($restaurantIds: [Int]!, $date: String!, $time: String!, $partySize: Int!, $forwardDays: Int, $databaseRegion: DatabaseRegion, $requireTimes: Boolean) {
  availability(restaurantIds: $restaurantIds, date: $date, time: $time, partySize: $partySize, forwardDays: $forwardDays, databaseRegion: $databaseRegion, requireTimes: $requireTimes) {
    restaurantId
    availabilityDays {
      date
      slots {
        __typename
        ... on AvailableSlot { isAvailable dayOffset timeOffsetMinutes type attributes slotHash }
      }
    }
  }
}"""

# Per-run scratch: a CSRF token is good for the whole poll, and a slug only ever
# needs resolving to its rid once.
_OT: dict = {"csrf": "", "csrf_at": 0.0, "rid": {}}

# OpenTable labels a slot's seating with terse attribute keys; the alert reads
# better with the words the booking page shows.
OPENTABLE_AREAS = {
    "default": "", "standard": "", "any": "",
    "bar": "Bar", "counter": "Counter", "highTop": "High top", "hightop": "High top",
    "outdoor": "Outdoor", "patio": "Patio", "terrace": "Terrace",
    "indoor": "Indoors", "communal": "Communal", "booth": "Booth",
    "window": "Window", "chefsTable": "Chef's table", "privateDining": "Private dining",
}


def _opentable_get(path: str, accept: str) -> str:
    try:
        r = requests.get(
            OPENTABLE_HOST + path,
            headers={"User-Agent": OPENTABLE_UA, "Accept": accept},
            timeout=25,
        )
        return r.text if r.ok else ""
    except Exception:
        return ""


def _opentable_csrf() -> str:
    """The token OpenTable's own pages send with every /dapi call."""
    now = time.time()
    if _OT["csrf"] and (now - _OT["csrf_at"]) < 1800: return _OT["csrf"]
    html = _opentable_get("/", "text/html")
    m = (re.search(r'"csrfToken"\s*:\s*"([^"]{8,})"', html)
         or re.search(r'__CSRF_TOKEN__\s*=\s*"([^"]{8,})"', html))
    _OT["csrf"] = m.group(1) if m else ""
    _OT["csrf_at"] = now
    return _OT["csrf"]


def _opentable_slug(venue: str) -> str:
    m = re.search(r"/r/([a-zA-Z0-9\-]+)", venue)
    if m: return m.group(1).lower()
    if re.fullmatch(r"[a-zA-Z0-9\-]{3,}", venue or ""): return venue.strip().lower()
    return ""


def _opentable_rid(venue: str) -> str:
    """A bare rid, an id inside a pasted OpenTable link, or a slug we look up."""
    v = str(venue or "").strip()
    if v.isdigit(): return v
    for pat in (r"[?&](?:rid|restaurantId|restRef|restref)=(\d+)", r"/restaurant/profile/(\d+)"):
        m = re.search(pat, v, re.I)
        if m: return m.group(1)

    slug = _opentable_slug(v)
    if not slug: return ""
    if slug in _OT["rid"]: return _OT["rid"][slug]
    html = _opentable_get("/r/" + slug, "text/html")
    m = re.search(r'"(?:restaurantId|rid)"\s*:\s*"?(\d{3,})"?', html)
    _OT["rid"][slug] = m.group(1) if m else ""
    return _OT["rid"][slug]


def _json_blobs(html: str) -> List[Any]:
    """Every embedded JSON island in an OpenTable page (state + <script> data)."""
    out: List[Any] = []
    for m in re.finditer(r'<script[^>]+type="application/json"[^>]*>(.*?)</script>', html, re.S):
        try: out.append(json.loads(m.group(1)))
        except Exception: pass
    for marker in ('window.__INITIAL_STATE__', '"__INITIAL_STATE__"'):
        i = html.find(marker)
        if i < 0: continue
        start = html.find("{", i + len(marker))
        if start < 0: continue
        depth, in_str, esc = 0, False, False
        for j in range(start, len(html)):
            c = html[j]
            if in_str:
                if esc: esc = False
                elif c == "\\": esc = True
                elif c == '"': in_str = False
                continue
            if c == '"': in_str = True
            elif c == "{": depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try: out.append(json.loads(html[start:j + 1]))
                    except Exception: pass
                    break
    return out


def _ot_area(slot: dict) -> str:
    attrs = slot.get("attributes")
    if isinstance(attrs, str): attrs = [attrs]
    names = []
    for a in (attrs or []):
        label = OPENTABLE_AREAS.get(str(a), str(a).strip())
        if label and label not in names: names.append(label)
    if names: return " · ".join(names)
    for key in ("seatingArea", "tableAttribute", "experienceName", "offerName"):
        v = str(slot.get(key) or "").strip()
        if v: return v
    return ""


def _ot_bookable(slot: dict) -> bool:
    tn = str(slot.get("__typename") or "")
    if "Unavailable" in tn: return False
    if slot.get("isAvailable") is False or slot.get("available") is False: return False
    if slot.get("isWaitlist") is True or slot.get("waitlist") is True: return False
    if str(slot.get("type") or "").upper() in ("WAITLIST", "REQUEST", "NOTIFY"): return False
    return True


def _ot_looks_like_slot(d: dict) -> bool:
    if not ("dateTime" in d or "timeOffsetMinutes" in d): return False
    marks = ("slotHash", "slotAvailabilityToken", "isAvailable", "available", "attributes", "timeOffsetMinutes")
    return "Slot" in str(d.get("__typename") or "") or any(k in d for k in marks)


def _opentable_normalize(payload: Any, date_yyyy_mm_dd: str, anchor_hhmm: str) -> List[Tuple[str, str]]:
    """Pull slots out of any OpenTable availability payload.

    The GraphQL answer gives offsets from the time we asked about
    (dayOffset/timeOffsetMinutes); page state and the mobile API give absolute
    dateTimes. Walking the whole payload for slot-shaped objects handles all of
    them, and keeps working when OpenTable moves a field one level deeper.
    """
    try:
        base = dt.datetime.strptime(f"{date_yyyy_mm_dd} {anchor_hhmm}", "%Y-%m-%d %H:%M")
    except Exception:
        return []

    out: List[Tuple[str, str]] = []
    seen: set = set()
    stack: List[Any] = [payload]
    while stack:
        node = stack.pop()
        if isinstance(node, list):
            stack.extend(node); continue
        if not isinstance(node, dict): continue
        stack.extend(node.values())
        if not _ot_looks_like_slot(node) or not _ot_bookable(node): continue

        iso = ""
        raw = node.get("dateTime") or node.get("time_iso") or node.get("time")
        if isinstance(raw, str) and _hhmm(raw):
            iso = raw
        elif isinstance(node.get("timeOffsetMinutes"), (int, float)):
            when = base + dt.timedelta(
                days=int(node.get("dayOffset") or 0),
                minutes=int(node["timeOffsetMinutes"]),
            )
            iso = when.strftime("%Y-%m-%dT%H:%M:%S")
        if not iso: continue

        slot = (iso, _ot_area(node))
        if slot in seen: continue
        seen.add(slot); out.append(slot)
    return out


def _opentable_gql(
    rid: str, date_yyyy_mm_dd: str, anchor_hhmm: str, party: int, num_days: int,
    region: str, availability_hash: str, debug: bool,
) -> List[Tuple[str, str]]:
    variables = {
        "restaurantIds": [int(rid)],
        "date": date_yyyy_mm_dd,
        "time": anchor_hhmm,
        "partySize": int(party),
        "forwardDays": max(0, int(num_days) - 1),
        "requireTimes": False,
    }
    if region: variables["databaseRegion"] = region

    headers = {
        "User-Agent": OPENTABLE_UA,
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": OPENTABLE_HOST,
        "Referer": OPENTABLE_HOST + "/",
    }
    csrf = _opentable_csrf()
    if csrf: headers["x-csrf-token"] = csrf

    # Hash first when we have one (that is what the browser sends); fall back to
    # the document if the server no longer knows the hash, or if we have none.
    attempts = []
    if availability_hash:
        attempts.append({"extensions": {"persistedQuery": {"version": 1, "sha256Hash": availability_hash}}})
    attempts.append({"query": OPENTABLE_QUERY})

    for extra in attempts:
        body = {"operationName": "RestaurantsAvailability", "variables": variables}
        body.update(extra)
        try:
            r = requests.post(OPENTABLE_GQL, headers=headers, json=body, timeout=25)
        except Exception:
            continue
        if not r.ok:
            if debug: print(f"[opentable] rid={rid} HTTP {r.status_code}")
            continue
        try: j = r.json()
        except Exception: continue
        errors = j.get("errors") if isinstance(j, dict) else None
        if errors:
            if debug: print(f"[opentable] rid={rid} errors: {json.dumps(errors)[:200]}")
            continue
        slots = _opentable_normalize(j, date_yyyy_mm_dd, anchor_hhmm)
        if slots: return slots
    return []


def _opentable_page_slots(
    venue: str, rid: str, date_yyyy_mm_dd: str, anchor_hhmm: str, party: int
) -> List[Tuple[str, str]]:
    """Fallback: the booking page itself, which ships its slots as page state."""
    q = f"?dateTime={date_yyyy_mm_dd}T{anchor_hhmm}&covers={int(party)}"
    slug = _opentable_slug(venue)
    paths = []
    if slug and not slug.isdigit(): paths.append("/r/" + slug + q)
    if rid: paths.append(f"/restaurant/profile/{rid}" + q)
    for path in paths:
        html = _opentable_get(path, "text/html")
        if not html: continue
        for blob in _json_blobs(html):
            slots = _opentable_normalize(blob, date_yyyy_mm_dd, anchor_hhmm)
            if slots: return slots
    return []


def fetch_opentable_slots(
    venue: str,
    date_yyyy_mm_dd: str,
    party: int,
    num_days: int = 1,
    anchor_hhmm: str = "19:00",
    region: str = "",
    availability_hash: str = "",
    debug: bool = False,
) -> List[Tuple[str, str]]:
    """Open tables at one OpenTable venue, as (iso, seating area) pairs.

    OpenTable answers around a requested time rather than returning the whole
    day, so `anchor_hhmm` is the middle of the search's window — the search's
    own window filter then does the final cut, exactly as for SevenRooms.
    """
    rid = _opentable_rid(venue)
    if not rid:
        if debug: print(f"[opentable] couldn't resolve a restaurant id for {venue!r}")
        return []

    slots = _opentable_gql(
        rid, date_yyyy_mm_dd, anchor_hhmm, party, num_days, region, availability_hash, debug
    )
    if slots: return slots
    return _opentable_page_slots(venue, rid, date_yyyy_mm_dd, anchor_hhmm, party)


def _anchor_time(search: dict) -> str:
    """The time an OpenTable lookup is centred on: the exact time, or the middle
    of the window, so the slots it returns straddle what we're watching for."""
    exact = _parse_time((search.get("time_slot") or "").strip())
    if exact: return exact.strftime("%H:%M")
    start = _parse_time((search.get("window_start") or "").strip())
    end = _parse_time((search.get("window_end") or "").strip())
    if start and end:
        s = start.hour * 60 + start.minute
        e = end.hour * 60 + end.minute
        if e < s: e += 24 * 60          # overnight window
        mid = ((s + e) // 2) % (24 * 60)
        return f"{mid // 60:02d}:{mid % 60:02d}"
    for t in (start, end):
        if t: return t.strftime("%H:%M")
    return "19:00"


# =========================================================
# MAIN SCHEDULER LOGIC
# =========================================================
def main() -> None:
    # Paths are configurable so the watcher can run anywhere (e.g. a VM with
    # state kept outside the git checkout). Defaults preserve existing behaviour.
    config_path = os.environ.get("CONFIG_PATH", "config.json")
    state_path = os.environ.get("STATE_PATH", "state.json")

    config = load_json(config_path, {"searches": []})
    state = load_json(state_path, {"notified": []})
    notified = set(state.get("notified", []))

    global_cfg = config.get("global", {}) or {}
    channel = global_cfg.get("channel", "SEVENROOMS_WIDGET")
    lang = global_cfg.get("lang", "en")
    halo = int(global_cfg.get("halo_size_interval", 64))
    delay = float(global_cfg.get("delay_between_venues_sec", 0.5))
    debug = bool(global_cfg.get("debug", False))

    # OpenTable knobs. The persisted-query hash rotates with OpenTable's own
    # deploys, so it lives in the environment (or config) where you can change it
    # without touching code; empty means "send the query document instead".
    ot_cfg = global_cfg.get("opentable", {}) or {}
    ot_hash = (os.environ.get("OPENTABLE_AVAILABILITY_HASH", "").strip()
               or str(ot_cfg.get("availability_hash", "") or "").strip())
    ot_region = (os.environ.get("OPENTABLE_REGION", "").strip()
                 or str(ot_cfg.get("region", "") or "").strip())

    # "Reset alerts" from the web app bumps global.reset_nonce. When it changes,
    # clear the dedupe set so previously-notified tables can alert again.
    reset_nonce = str(global_cfg.get("reset_nonce", "") or "")
    if reset_nonce and state.get("reset_nonce") != reset_nonce:
        notified = set()

    # Contact targets come from the environment so nothing personal is hardcoded.
    pushover_email = os.environ.get("PUSHOVER_EMAIL", "").strip() or "bfxfnhvuie@pomail.net"

    for search in config.get("searches", []):
        sid = search.get("id") or "Unnamed"
        platform = (search.get("platform") or "sevenrooms").lower()

        if platform not in ("sevenrooms", "opentable"): continue

        venues = search.get("venues") or []
        party = int(search.get("party_size") or 2)
        num_days = int(search.get("num_days") or 1)
        dates = _get_search_dates(search)
        
        if not dates: continue

        time_slot = (search.get("time_slot") or "").strip()
        window_start = (search.get("window_start") or "").strip()
        window_end = (search.get("window_end") or "").strip()
        notify_mode = (search.get("notify") or "both").lower()
        email_to = (search.get("email_to") or os.environ.get("EMAIL_TO", "")).strip()
        salt = str(search.get("salt") or "")
        # OpenTable venues are numeric ids; the app can pass names alongside them
        # so the alert reads "Sune" rather than "1234567".
        labels = search.get("venue_labels") or {}
        anchor = _anchor_time(search)

        candidates: List[Tuple[str, str]] = []

        for date in dates:
            for v in venues:
                v = str(v).strip()
                if not v: continue

                if platform == "opentable":
                    iso_slots = fetch_opentable_slots(
                        v, date, party, num_days=num_days, anchor_hhmm=anchor,
                        region=ot_region, availability_hash=ot_hash, debug=debug
                    )
                else:
                    iso_slots = fetch_sevenrooms_slots(
                        v, date, party, channel=channel, num_days=num_days, lang=lang, halo_size_interval=halo, debug=debug
                    )

                vlabel = str(labels.get(v) or "").strip() or v

                for iso, area in iso_slots:
                    hh = _hhmm(iso) or iso
                    if time_slot:
                        if (_hhmm(iso) or "") != time_slot: continue
                    else:
                        if not _in_window((_hhmm(iso) or ""), window_start, window_end): continue

                    fp = hashlib.sha256(
                        f"{sid}\n{platform}\n{v}\n{date}\n{iso}\n{area}\n{salt}".encode()
                    ).hexdigest()

                    if fp in notified: continue
                    # Multi-day scans hand back tables on later days too, so the
                    # alert names the table's own date, not the one we asked for.
                    d = _parse_iso(iso)
                    when = d.strftime("%Y-%m-%d") if d else date
                    label = f"{when} — {vlabel} @ {hh}" + (f" · {area}" if area else "")
                    candidates.append((fp, label))

                if delay: time.sleep(delay)

        if candidates and notify_mode != "none":
            plat_name = {"sevenrooms": "SevenRooms", "opentable": "OpenTable"}.get(platform, platform.title())
            summary = [f"Dates: {', '.join(dates)}", f"Party: {party}", f"Platform: {plat_name}"]
            if time_slot: summary.append(f"Time: {time_slot}")
            else: summary.append(f"Window: {window_start or '?'}–{window_end or '?'}")

            found_lines = [label for _, label in candidates]
            msg = f"{sid} — " + "\n".join(summary) + "\n" + "\n".join(found_lines)

            push_ok = False
            email_ok = False

            if notify_mode in ("push", "both") and pushover_email:
                push_ok = send_email(pushover_email, f"Table Alert: {sid}", msg, debug=debug)

            if notify_mode in ("email", "both") and email_to:
                email_ok = send_email(email_to, f"Table Alert: {sid}", msg, debug=debug)

            if push_ok or email_ok:
                for fp, _ in candidates: notified.add(fp)

    save_json(state_path, {"notified": list(notified)[-2000:], "reset_nonce": reset_nonce})

if __name__ == "__main__":
    main()
