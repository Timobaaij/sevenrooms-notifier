
import streamlit as st
import json
import time
import datetime as dt
import requests
import re
from github import Github
from bs4 import BeautifulSoup

# =========================================================
# CONFIG
# =========================================================
REPO_NAME = "timobaaij/sevenrooms-notifier"
CONFIG_FILE_PATH = "config.json"
STATE_FILE_PATH = "state.json"
st.set_page_config(page_title="Reservation Manager", page_icon="🍽️", layout="wide")

# =========================================================
# AUTH (GitHub)
# =========================================================
try:
    token = st.secrets["GITHUB_TOKEN"]
    g = Github(token)
    repo = g.get_repo(REPO_NAME)
    cfg_contents = repo.get_contents(CONFIG_FILE_PATH)
    config_data = json.loads(cfg_contents.decoded_content.decode("utf-8"))
except Exception as e:
    st.error(f"❌ Connection Error: {e}")
    st.stop()

# =========================================================
# HELPERS
# =========================================================
def _read_json_from_repo(path: str, default: dict):
    try:
        c = repo.get_contents(path)
        return c, json.loads(c.decoded_content.decode("utf-8"))
    except Exception:
        return None, default

def _update_file_json(path: str, message: str, data: dict):
    c = repo.get_contents(path)
    repo.update_file(c.path, message, json.dumps(data, indent=2, sort_keys=True), c.sha)

def save_config(new_data: dict):
    """Safe update of config.json, then refresh UI."""
    try:
        _update_file_json(CONFIG_FILE_PATH, "Update via Web App", new_data)
        st.toast("✅ Saved!", icon="💾")
        time.sleep(0.15)
        st.cache_data.clear()
        st.rerun()
    except Exception as e:
        st.error(f"Save Failed: {e}")

def reset_state():
    """Clear state.json so notifications can fire again."""
    try:
        _update_file_json(STATE_FILE_PATH, "Reset notifier state (clear notified cache)", {"notified": []})
        st.toast("🔄 State reset — notifications will fire again", icon="🔔")
        time.sleep(0.15)
        st.rerun()
    except Exception as e:
        st.error(f"Reset failed: {e}")

# =========================================================
# FINDERS (Advanced tools)
# =========================================================
def parse_opentable_ids_from_url(text: str):
    if not text:
        return []
    # Extract rid=12345 from URLs or raw query strings
    return re.findall(r"[?&]rid=(\d{2,})", text)

def get_ot_ids(url: str):
    """Get ALL OpenTable restaurant IDs from page source (and URL).
    Filters out null/0/1-digit; returns de-duped list.
    """
    if not url:
        return []

    ids = parse_opentable_ids_from_url(url)
    headers = {"User-Agent": "Mozilla/5.0"}

    try:
        r = requests.get(url, headers=headers, timeout=12)
        html = r.text or ""
    except Exception:
        html = ""

    if html:
        soup = BeautifulSoup(html, "html.parser")
        script_blob = "\n".join(s.get_text(" ", strip=True) for s in soup.find_all("script"))
        combined = script_blob + "\n" + html

        # Pull numeric restaurantId values from embedded JSON blobs/scripts
        raw = re.findall(r"\"restaurantId\"\s*:\s*\"?(\d+)\"?", combined, flags=re.IGNORECASE)

        cleaned = []
        for v in raw:
            v = str(v).strip()
            if v in ("0", ""):
                continue
            if not v.isdigit() or len(v) < 2:
                continue
            cleaned.append(v)

        ids = ids + cleaned

    seen, uniq = set(), []
    for x in ids:
        if x not in seen:
            uniq.append(x)
            seen.add(x)
    return uniq

def get_sevenrooms_slug(text: str):
    if not text:
        return None
    m = re.search(r"[?&]venue=([a-zA-Z0-9_\-]+)", text)
    if m:
        return m.group(1)
    m = re.search(r"/reservations/([a-zA-Z0-9_\-]+)", text)
    if m:
        return m.group(1)
    if re.fullmatch(r"[a-zA-Z0-9_\-]{3,}", text.strip()):
        return text.strip()
    return None

# =========================================================
# AVAILABILITY LOADERS (for the picker UI only)
# =========================================================
def fetch_sevenrooms_times(venue: str, date_yyyy_mm_dd: str, party: int, channel: str, num_days: int = 1, lang: str = "en"):
    try:
        d_sr = dt.datetime.strptime(date_yyyy_mm_dd, "%Y-%m-%d").strftime("%m-%d-%Y")
    except Exception:
        return []

    url = (
        "https://www.sevenrooms.com/api-yoa/availability/widget/range"
        f"?venue={venue}&party_size={party}&start_date={d_sr}&num_days={num_days}"
        f"&channel={channel}&lang={lang}"
    )
    r = requests.get(url, timeout=15)
    if not r.ok:
        return []
    j = r.json()
    out = []
    availability = (j.get("data", {}) or {}).get("availability", {}) or {}

    for _, day in availability.items():
        if not isinstance(day, list):
            continue
        for block in day:
            for t in (block or {}).get("times", []):
                if not isinstance(t, dict):
                    continue
                is_avail = bool(t.get("is_available"))
                is_req = bool(t.get("is_requestable"))
                if not (is_avail or is_req):
                    continue
                iso = t.get("time_iso") or t.get("date_time") or t.get("time")
                if not iso:
                    continue
                try:
                    hhmm = dt.datetime.fromisoformat(str(iso).replace("Z", "+00:00")).strftime("%H:%M")
                except Exception:
                    m = re.search(r"\b([01]\d|2[0-3]):([0-5]\d)\b", str(iso))
                    hhmm = f"{m.group(1)}:{m.group(2)}" if m else str(iso)
                label = hhmm + (" (REQUEST)" if (is_req and not is_avail) else "")
                out.append(label)

    # de-dup
    seen, uniq = set(), []
    for x in out:
        if x not in seen:
            uniq.append(x)
            seen.add(x)
    return uniq


# -----------------------------
# OpenTable (UPDATED for UI picker)
# -----------------------------
def fetch_opentable_times(rid: str, date_yyyy_mm_dd: str, party: int, time_hints=None):
    # OpenTable availability can be served from different marketplace hosts.
    # Also, responses are anchored around the provided dateTime.
    hints = [h for h in (time_hints or []) if h]
    if not hints:
        hints = ["19:00", "12:00", "21:00"]

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

    def _dt_candidates(hhmm: str):
        m = re.search(r"\b([01]\d|2[0-3]):([0-5]\d)\b", hhmm or "")
        hhmm = f"{m.group(1)}:{m.group(2)}" if m else "19:00"
        base = f"{date_yyyy_mm_dd}T{hhmm}:00"
        # Try with and without explicit UTC offset
        return [base, base + "+00:00"]

    slots = []

    def walk(x):
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
                slots.append(str(x.get(dt_key)))

            for v in x.values():
                walk(v)

        elif isinstance(x, list):
            for v in x:
                walk(v)

    for host in hosts:
        for h in hints:
            for dt_value in _dt_candidates(h):
                url = host.rstrip("/") + "/api/v2/reservation/availability"
                params = {"rid": str(rid), "partySize": int(party), "dateTime": dt_value}
                r = requests.get(url, params=params, headers=headers, timeout=15)
                if not r.ok:
                    continue
                if "json" not in (r.headers.get("Content-Type") or "").lower():
                    continue
                try:
                    j = r.json()
                except Exception:
                    continue
                walk(j)

    # de-dup but preserve order
    seen, uniq = set(), []
    for s in slots:
        if s not in seen:
            uniq.append(s)
            seen.add(s)

    out = []
    for iso in uniq:
        try:
            out.append(dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%H:%M"))
        except Exception:
            m = re.search(r"\b([01]\d|2[0-3]):([0-5]\d)\b", iso)
            if m:
                out.append(f"{m.group(1)}:{m.group(2)}")

    seen2, uniq2 = set(), []
    for x in out:
        if x not in seen2:
            uniq2.append(x)
            seen2.add(x)
    return uniq2


# =========================================================
# DEFAULTS & MAPPINGS
# =========================================================
config_data.setdefault("global", {"channel": "SEVENROOMS_WIDGET", "delay_between_venues_sec": 0.5, "lang": "en"})
config_data.setdefault("ntfy_default", {"server": "https://ntfy.sh", "topic": "", "priority": "urgent", "tags": "rotating_light"})
config_data.setdefault("searches", [])

PLATFORM_LABELS = ["SevenRooms", "OpenTable"]
PLATFORM_MAP = {"SevenRooms": "sevenrooms", "OpenTable": "opentable"}

NOTIFY_LABELS = ["Push", "Email", "Both", "None"]
NOTIFY_MAP = {"Push": "push", "Email": "email", "Both": "both", "None": "none"}

# =========================================================
# DASHBOARD HEADER
# =========================================================
st.title("🍽️ Reservation Dashboard")
searches = config_data.get("searches", [])
if not searches:
    st.info("No active searches yet.")

# Local toggle helper
def _toggle(key: str):
    st.session_state[key] = not st.session_state.get(key, False)

# =========================================================
# DASHBOARD ROWS (no custom HTML wrappers → no white bar)
# =========================================================
for i, s in enumerate(searches):
    # Derive labels
    p_val = s.get("platform", "sevenrooms")
    p_label = "SevenRooms" if p_val == "sevenrooms" else "OpenTable"
    date_txt = s.get("date", "")
    party_txt = str(s.get("party_size", ""))
    time_slot = (s.get("time_slot") or "").strip()
    window_txt = f"{s.get('window_start','')}–{s.get('window_end','')}"
    time_txt = time_slot if time_slot else window_txt
    notify_txt = (s.get("notify") or "both").title()

    with st.container(border=True):
        # Header line
        header_cols = st.columns([0.65, 0.35])
        with header_cols[0]:
            st.markdown(f"**📍 {s.get('id','Unnamed')} ({p_label})**")
            st.caption(f"🗓 {date_txt} · 👥 {party_txt} · ⏱ {time_txt} · 🔔 {notify_txt}")

        with header_cols[1]:
            # ✅ changed from 2 → 3 columns to add Delete
            action_cols = st.columns(3)
            with action_cols[0]:
                if st.button("See details", key=f"btn_details_{i}"):
                    _toggle(f"show_details_{i}")
            with action_cols[1]:
                if st.button("Edit", key=f"btn_edit_{i}"):
                    _toggle(f"show_edit_{i}")
            with action_cols[2]:
                # ---- DELETE BUTTON (two-step confirm) ----
                if st.button("🗑️ Delete", key=f"btn_delete_{i}"):
                    st.session_state[f"confirm_delete_{i}"] = True

        # ✅ Confirmation UI (only shows if delete clicked)
        if st.session_state.get(f"confirm_delete_{i}", False):
            st.divider()
            st.warning(f"Delete **{s.get('id','Unnamed')}**? This will remove the search from `config.json`.", icon="⚠️")
            cdel1, cdel2 = st.columns(2)
            with cdel1:
                if st.button("✅ Confirm delete", type="primary", key=f"btn_confirm_delete_{i}"):
                    # Remove the search and save
                    searches.pop(i)
                    config_data["searches"] = searches
                    st.session_state[f"confirm_delete_{i}"] = False
                    save_config(config_data)
            with cdel2:
                if st.button("Cancel", key=f"btn_cancel_delete_{i}"):
                    st.session_state[f"confirm_delete_{i}"] = False

        # Details (view-only) section
        if st.session_state.get(f"show_details_{i}", False):
            st.divider()
            st.write(f"**Venues**: {', '.join(s.get('venues', [])) or '—'}")
            st.write(f"**Num Days**: {int(s.get('num_days', 1))}")
            email_to = s.get("email_to") or "—"
            st.write(f"**Email**: {email_to}")
            notes = (s.get("notes") or "").strip()
            if notes:
                st.write(f"**Notes**: {notes}")

        # Edit form (compact, toggled separately)
        if st.session_state.get(f"show_edit_{i}", False):
            st.divider()
            with st.form(f"edit_form_{i}"):
                current_label = "SevenRooms" if (s.get("platform", "sevenrooms") == "sevenrooms") else "OpenTable"
                e_platform_label = st.selectbox("Platform", PLATFORM_LABELS, index=PLATFORM_LABELS.index(current_label))
                e_platform = PLATFORM_MAP[e_platform_label]

                e_name = st.text_input("Name", s.get("id", ""))
                e_venues = st.text_input("Venues (IDs/Slugs, comma separated)", ", ".join(s.get("venues", [])))
                e_date = st.date_input(
                    "Date",
                    dt.datetime.strptime(s.get("date"), "%Y-%m-%d").date() if s.get("date") else dt.date.today()
                )
                e_party = st.number_input("Party", 1, 20, value=int(s.get("party_size", 2)))
                e_num_days = st.number_input("Num Days", 1, 7, value=int(s.get("num_days", 1)))

                e_time_slot = st.text_input("Exact time (HH:MM) — leave blank for window", s.get("time_slot", ""))
                e_window_start = st.text_input("Window start (HH:MM)", s.get("window_start", "18:00"))
                e_window_end = st.text_input("Window end (HH:MM)", s.get("window_end", "22:00"))

                e_notify_label = st.selectbox(
                    "Notification method",
                    NOTIFY_LABELS,
                    index=NOTIFY_LABELS.index((s.get("notify") or "both").title())
                )
                e_notify = NOTIFY_MAP[e_notify_label]

                e_email = st.text_input("Email alert to (optional)", s.get("email_to", ""))
                e_notes = st.text_area("Notes (optional)", s.get("notes", ""), height=80)

                col_submit = st.columns(2)
                with col_submit[0]:
                    launched = st.form_submit_button("💾 Save", type="primary")
                with col_submit[1]:
                    cancel = st.form_submit_button("Cancel")

                if launched:
                    searches[i].update(
                        {
                            "id": e_name.strip() or "Unnamed",
                            "platform": e_platform,
                            "venues": [v.strip() for v in e_venues.split(",") if v.strip()],
                            "date": str(e_date),
                            "party_size": int(e_party),
                            "num_days": int(e_num_days),
                            "time_slot": e_time_slot.strip(),
                            "window_start": e_window_start.strip(),
                            "window_end": e_window_end.strip(),
                            "notify": e_notify,
                            "email_to": e_email.strip(),
                            "notes": e_notes.strip(),
                            "salt": str(time.time()),
                        }
                    )
                    config_data["searches"] = searches
                    save_config(config_data)
                elif cancel:
                    st.session_state[f"show_edit_{i}"] = False

# =========================================================
# ADD NEW SEARCH (top area)
# =========================================================
st.subheader("➕ Add New Search")

add_cols = st.columns([0.5, 0.5])
with add_cols[0]:
    plat_label = st.selectbox("Platform", PLATFORM_LABELS, index=0, key="new_platform_label")
    plat = PLATFORM_MAP[plat_label]
    default_venue = st.session_state.get("last_ot_id", "") if plat == "opentable" else st.session_state.get("last_sr_slug", "")
    n_venue = st.text_input("Venue ID/Slug (comma separated supported)", value=default_venue, key="new_venue")
    n_id = st.text_input("Search name", key="new_name")
    n_date = st.date_input("Date", key="new_date")
    n_party = st.number_input("Party", 1, 20, 2, key="new_party")
    n_num_days = st.number_input("Num Days", 1, 7, 1, key="new_num_days")

with add_cols[1]:
    n_notify_label = st.selectbox("Notification method", NOTIFY_LABELS, index=2, key="new_notify_label")  # default Both
    n_notify = NOTIFY_MAP[n_notify_label]
    n_email = st.text_input("Email alert to (optional)", key="new_email")

    st.caption("Time")
    any_time = st.checkbox("Any time in a window", value=True, key="new_any_time")

    gbl = config_data.get("global", {})
    channel = gbl.get("channel", "SEVENROOMS_WIDGET")
    lang = gbl.get("lang", "en")

    if st.button("🔄 Load available times", key="load_times"):
        venue_first = (n_venue.split(",")[0].strip() if n_venue else "")
        if venue_first:
            with st.spinner("Fetching times…"):
                if plat == "opentable":
                    # Use window anchors when available to capture all times (no UI change)
                    ot_hints = []
                    if any_time and st.session_state.get("new_wstart") and st.session_state.get("new_wend"):
                        ot_hints = [st.session_state.get("new_wstart"), st.session_state.get("new_wend"), "19:00"]
                    times_list = fetch_opentable_times(venue_first, str(n_date), int(n_party), time_hints=ot_hints)
                else:
                    times_list = fetch_sevenrooms_times(
                        venue_first, str(n_date), int(n_party),
                        channel=channel, num_days=int(n_num_days), lang=lang
                    )
            st.session_state["loaded_times"] = times_list
        else:
            st.session_state["loaded_times"] = []

loaded = st.session_state.get("loaded_times", [])

if any_time:
    n_window_start = st.text_input("Window start (HH:MM)", value="18:00", key="new_wstart")
    n_window_end = st.text_input("Window end (HH:MM)", value="22:00", key="new_wend")
    n_time_slot = ""
else:
    if loaded:
        choice = st.selectbox("Pick a time", loaded, key="new_time_pick")
        n_time_slot = choice.split(" ")[0]
    else:
        n_time_slot = st.text_input("Exact time (HH:MM)", value="19:00", key="new_time_manual")
    n_window_start, n_window_end = "", ""

# Launch creation
if st.button("🚀 Launch search", type="primary", key="launch"):
    new_s = {
        "id": n_id.strip() or "Unnamed",
        "platform": plat,
        "venues": [v.strip() for v in n_venue.split(",") if v.strip()],
        "party_size": int(n_party),
        "date": str(n_date),
        "time_slot": n_time_slot.strip(),
        "window_start": n_window_start.strip(),
        "window_end": n_window_end.strip(),
        "num_days": int(n_num_days),
        "notify": n_notify,
        "email_to": n_email.strip(),
        "notes": "",
        "salt": str(time.time()),
    }
    config_data.setdefault("searches", []).append(new_s)
    save_config(config_data)

# =========================================================
# ADVANCED (collapsed; less in sight)
# =========================================================
with st.expander("⚙️ Advanced", expanded=False):
    st.caption("Tools for maintenance, ID/Slug finders, and push settings.")

    st.subheader("Maintenance")
    _, state_data = _read_json_from_repo(STATE_FILE_PATH, {"notified": []})
    st.caption(f"Current dedupe cache entries: {len(state_data.get('notified', []) or [])}")
    if st.button("🔄 Reset state (get notifications again)"):
        reset_state()

    st.divider()
    st.subheader("Quick ID / Slug Finder")
    st.caption("OpenTable — extracts ALL valid IDs (filters 0/null, requires 2+ digits)")

    ot_url = st.text_input("Paste OpenTable link", key="ot_url_adv")
    if st.button("Extract OpenTable IDs", key="btn_ot_ids_adv"):
        ids = get_ot_ids(ot_url)
        st.session_state["ot_ids_found"] = ids

    ids_found = st.session_state.get("ot_ids_found", [])
    if ids_found:
        st.success(f"Found {len(ids_found)} IDs: {', '.join(ids_found)}")
        chosen = st.selectbox("Use this ID", ids_found, key="ot_id_choice_adv")
        st.session_state["last_ot_id"] = chosen

    st.caption("SevenRooms")
    sr_text = st.text_input("Paste SevenRooms link (or type slug)", key="sr_url_adv")
    if st.button("Extract SevenRooms slug", key="btn_sr_slug_adv"):
        slug = get_sevenrooms_slug(sr_text)
        if slug:
            st.success(f"Slug: {slug}")
            st.session_state["last_sr_slug"] = slug
        else:
            st.error("Couldn’t find a slug in that text.")

    st.divider()
    st.subheader("Push notification settings (ntfy)")
    nt = config_data.get("ntfy_default", {})
    server = st.text_input("Server", nt.get("server", "https://ntfy.sh"))
    topic = st.text_input("Topic", nt.get("topic", ""))
    priority = st.text_input("Priority", nt.get("priority", "urgent"))
    tags = st.text_input("Tags", nt.get("tags", "rotating_light"))

    c1, c2 = st.columns(2)
    with c1:
        if st.button("💾 Save push settings", key="save_push_settings"):
            config_data["ntfy_default"] = {
                "server": server.strip(),
                "topic": topic.strip(),
                "priority": priority.strip(),
                "tags": tags.strip(),
            }
            save_config(config_data)

    with c2:
        def post_test_push(server: str, topic: str, title: str, msg: str, priority: str = "", tags: str = ""):
            if not (server and topic):
                return False, "Missing server/topic"
            headers = {"Title": title}
            if priority:
                headers["Priority"] = priority
            if tags:
                headers["Tags"] = tags
            url = f"{server.rstrip('/')}/{topic}"
            r = requests.post(url, data=msg.encode("utf-8"), headers=headers, timeout=12)
            return r.ok, f"HTTP {r.status_code}"

        if st.button("🧪 Send test push", key="test_push"):
            ok, info = post_test_push(
                server.strip(),
                topic.strip(),
                "Test: Reservation Manager",
                "If you see this, push is working.",
                priority.strip(),
                tags.strip(),
            )
            st.success("Sent ✅") if ok else st.error(f"Failed: {info}")
