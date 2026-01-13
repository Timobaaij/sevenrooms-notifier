
import streamlit as st
import json
import time
import datetime as dt
import requests
import re
from github import Github
from bs4 import BeautifulSoup

# --- CONFIGURATION ---
REPO_NAME = "timobaaij/sevenrooms-notifier"
CONFIG_FILE_PATH = "config.json"
STATE_FILE_PATH = "state.json"

st.set_page_config(page_title="Reservation Manager", page_icon="🍽️", layout="wide")

# --- LIGHTWEIGHT CSS FOR A SLEEK, MOBILE-FRIENDLY UI ---
st.markdown("""
<style>
/* Page padding & typography */
.block-container {padding-top: 1.2rem; padding-bottom: 3rem; max-width: 1100px;}
h1, h2, h3, h4 {letter-spacing: 0.2px}
small, .small {opacity: 0.85}

/* Cards */
.card {background: white; border-radius: 12px; padding: 12px 14px; box-shadow: 0 2px 18px rgba(0,0,0,0.06); margin-bottom: 16px;}
.card img {border-radius: 10px}

/* Compact labels */
.caption {font-size: 0.78rem; color: #6b7280; margin-bottom: 0.25rem}

/* Buttons */
.stButton>button {border-radius: 8px; padding: 0.5rem 0.9rem}

/* Mobile tweaks */
@media (max-width: 640px){
  .block-container {padding-left: 0.8rem; padding-right: 0.8rem;}
  .stColumn {padding: 0 !important;}
  .card {padding: 10px 12px;}
  .st-emotion-cache-16idsys, .st-emotion-cache-1emrehy {gap: 0.5rem !important;} /* columns gap */
}
</style>
""", unsafe_allow_html=True)

# --- AUTH ---
try:
    token = st.secrets["GITHUB_TOKEN"]
    g = Github(token)
    repo = g.get_repo(REPO_NAME)

    cfg_contents = repo.get_contents(CONFIG_FILE_PATH)
    config_data = json.loads(cfg_contents.decoded_content.decode("utf-8"))
except Exception as e:
    st.error(f"❌ Connection Error: {e}")
    st.stop()


# --- HELPERS ---
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
    try:
        _update_file_json(CONFIG_FILE_PATH, "Update via Web App", new_data)
        st.toast("✅ Saved!", icon="💾")
        time.sleep(0.2)
        st.cache_data.clear()
        st.rerun()
    except Exception as e:
        st.error(f"Save Failed: {e}")

def reset_state():
    """Clear state.json so notifications can fire again."""
    try:
        _update_file_json(STATE_FILE_PATH, "Reset notifier state (clear notified cache)", {"notified": []})
        st.toast("🔄 State reset — notifications will fire again", icon="🔔")
        time.sleep(0.2)
        st.rerun()
    except Exception as e:
        st.error(f"Reset failed: {e}")

# ---------- OpenTable ID Finder (ALL IDs + filtering) ----------
def parse_opentable_ids_from_url(text: str):
    if not text:
        return []
    return re.findall(r"[?&]rid=(\d{2,})", text)

def get_ot_ids(url: str):
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
        raw = re.findall(r'"restaurantId"\s*:\s*(null|"?\d+"?)', combined, flags=re.IGNORECASE)
        cleaned = []
        for val in raw:
            v = str(val).strip().strip('"').lower()
            if v in ("null", "0", ""):  # filter null/0/empty
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

# ---------- SevenRooms slug finder ----------
def get_sevenrooms_slug(text: str):
    if not text:
        return None
    m = re.search(r"[?&]venue=([a-zA-Z0-9_-]+)", text)
    if m:
        return m.group(1)
    m = re.search(r"/reservations/([a-zA-Z0-9_-]+)", text)
    if m:
        return m.group(1)
    if re.fullmatch(r"[a-zA-Z0-9_-]{3,}", text.strip()):
        return text.strip()
    return None

# ---------- Availability fetchers for time selection (UI only) ----------
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
                # UI loader can be looser; notifier already filters strictly
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
    seen, uniq = set(), []
    for x in out:
        if x not in seen:
            uniq.append(x)
            seen.add(x)
    return uniq

def fetch_opentable_times(rid: str, date_yyyy_mm_dd: str, party: int):
    url = (
        "https://www.opentable.com/api/v2/reservation/availability"
        f"?rid={rid}&partySize={party}&dateTime={date_yyyy_mm_dd}T19:00"
    )
    headers = {"User-Agent": "Mozilla/5.0"}
    r = requests.get(url, headers=headers, timeout=15)
    if not r.ok:
        return []
    j = r.json()
    slots = []
    def walk(x):
        if isinstance(x, dict):
            if "dateTime" in x and x.get("isAvailable") is True:
                slots.append(str(x.get("dateTime")))
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(j)
    out = []
    for iso in slots:
        try:
            out.append(dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%H:%M"))
        except Exception:
            m = re.search(r"\b([01]\d|2[0-3]):([0-5]\d)\b", iso)
            if m:
                out.append(f"{m.group(1)}:{m.group(2)}")
    seen, uniq = set(), []
    for x in out:
        if x not in seen:
            uniq.append(x)
            seen.add(x)
    return uniq

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

# --- Defaults (non-destructive) ---
config_data.setdefault("global", {"channel": "SEVENROOMS_WIDGET", "delay_between_venues_sec": 0.5, "lang": "en"})
config_data.setdefault("ntfy_default", {"server": "https://ntfy.sh", "topic": "", "priority": "urgent", "tags": "rotating_light"})
config_data.setdefault("searches", [])

# Mapping for capitalized platform labels <-> stored values
PLATFORM_LABELS = ["SevenRooms", "OpenTable"]
PLATFORM_MAP = {"SevenRooms": "sevenrooms", "OpenTable": "opentable"}

# Notification options
NOTIFY_LABELS = ["Push", "Email", "Both", "None"]
NOTIFY_MAP = {"Push": "push", "Email": "email", "Both": "both", "None": "none"}

# --- LAYOUT ---
col_main, col_tools = st.columns([2.6, 1.4], gap="large")

# =======================
# LEFT: Active searches (sleek cards)
# =======================
with col_main:
    st.title("🍽️ My Active Searches")
    searches = config_data.get("searches", [])
    if not searches:
        st.info("No active searches yet.")

    for i, s in enumerate(searches):
        p_val = s.get("platform", "sevenrooms")
        p_label = "SevenRooms" if p_val == "sevenrooms" else "OpenTable"

        with st.container():
            st.markdown('<div class="card">', unsafe_allow_html=True)

            img_url = s.get("image_url")
            if img_url:
                st.image(img_url, use_container_width=True)

            st.subheader(f"📍 {s.get('id', 'Unnamed')} ({p_label})")

            c1, c2, c3, c4 = st.columns([1.2, 1.2, 1.6, 1.2])
            with c1:
                st.markdown('<div class="caption">DATE</div>', unsafe_allow_html=True)
                st.write(s.get("date", ""))
                st.markdown('<div class="caption">PARTY</div>', unsafe_allow_html=True)
                st.write(str(s.get("party_size", "")))
            with c2:
                st.markdown('<div class="caption">TIME</div>', unsafe_allow_html=True)
                ts = (s.get("time_slot") or "").strip()
                st.write(ts if ts else f"{s.get('window_start','')}–{s.get('window_end','')}")
                st.markdown('<div class="caption">DAYS</div>', unsafe_allow_html=True)
                st.write(str(s.get("num_days", 1)))
            with c3:
                st.markdown('<div class="caption">VENUES</div>', unsafe_allow_html=True)
                st.write(", ".join(s.get("venues", [])))
                if s.get("notes"):
                    st.markdown('<div class="caption">NOTES</div>', unsafe_allow_html=True)
                    st.write(s.get("notes"))
            with c4:
                st.markdown('<div class="caption">NOTIFY</div>', unsafe_allow_html=True)
                st.write((s.get("notify") or "both").title())
                if s.get("email_to"):
                    st.markdown('<div class="caption">EMAIL</div>', unsafe_allow_html=True)
                    st.write(s.get("email_to"))

            a1, a2 = st.columns(2)
            with a1:
                show_edit = st.checkbox("✏️ Edit", key=f"edit_{i}")
            with a2:
                if st.button("🗑️ Delete", key=f"del_{i}"):
                    searches.pop(i)
                    config_data["searches"] = searches
                    save_config(config_data)

            if show_edit:
                st.divider()
                with st.form(f"edit_form_{i}"):
                    e_name = st.text_input("Name", s.get("id", ""))
                    # Capitalized labels in UI, normalized value in config
                    current_label = "SevenRooms" if (s.get("platform", "sevenrooms") == "sevenrooms") else "OpenTable"
                    e_platform_label = st.selectbox("Platform", PLATFORM_LABELS, index=PLATFORM_LABELS.index(current_label))
                    e_platform = PLATFORM_MAP[e_platform_label]

                    e_venues = st.text_input("Venues (IDs/Slugs, comma separated)", ", ".join(s.get("venues", [])))
                    e_date = st.date_input("Date", dt.datetime.strptime(s.get("date"), "%Y-%m-%d").date() if s.get("date") else dt.date.today())
                    e_party = st.number_input("Party", 1, 20, value=int(s.get("party_size", 2)))
                    e_num_days = st.number_input("Num Days", 1, 7, value=int(s.get("num_days", 1)))

                    e_time_slot = st.text_input("Exact time (HH:MM) — leave blank for window", s.get("time_slot", ""))
                    e_window_start = st.text_input("Window start (HH:MM)", s.get("window_start", "18:00"))
                    e_window_end = st.text_input("Window end (HH:MM)", s.get("window_end", "22:00"))

                    e_notify_label = st.selectbox("Notification method", NOTIFY_LABELS, index=NOTIFY_LABELS.index((s.get("notify") or "both").title()))
                    e_notify = NOTIFY_MAP[e_notify_label]

                    e_email = st.text_input("Email alert to (optional)", s.get("email_to", ""))
                    e_img = st.text_input("Image URL (optional)", s.get("image_url", ""))
                    e_notes = st.text_area("Notes (optional)", s.get("notes", ""), height=80)

                    if st.form_submit_button("💾 Save"):
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
                                "notify": e_notify,  # NEW
                                "email_to": e_email.strip(),
                                "image_url": e_img.strip(),
                                "notes": e_notes.strip(),
                                "salt": str(time.time()),
                            }
                        )
                        config_data["searches"] = searches
                        save_config(config_data)

            st.markdown("</div>", unsafe_allow_html=True)  # end .card

# =======================
# RIGHT: Add new (concise)
# =======================
with col_tools:
    st.header("Add Search")

    # Capitalized platform in UI, normalized value in config
    plat_label = st.selectbox("Platform", PLATFORM_LABELS, index=0, key="new_platform_label")
    plat = PLATFORM_MAP[plat_label]

    # Help carry over last found IDs
    default_venue = st.session_state.get("last_ot_id", "") if plat == "opentable" else st.session_state.get("last_sr_slug", "")
    n_venue = st.text_input("Venue ID/Slug (comma separated supported)", value=default_venue, key="new_venue")
    n_id = st.text_input("Search name", key="new_name")
    n_date = st.date_input("Date", key="new_date")
    n_party = st.number_input("Party", 1, 20, 2, key="new_party")
    n_num_days = st.number_input("Num Days", 1, 7, 1, key="new_num_days")

    # Notification method
    n_notify_label = st.selectbox("Notification method", NOTIFY_LABELS, index=2, key="new_notify_label")  # default Both
    n_notify = NOTIFY_MAP[n_notify_label]
    n_email = st.text_input("Email alert to (optional)", key="new_email")

    # Time selection (optional exact time vs window)
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
                    times_list = fetch_opentable_times(venue_first, str(n_date), int(n_party))
                else:
                    times_list = fetch_sevenrooms_times(venue_first, str(n_date), int(n_party), channel=channel, num_days=int(n_num_days), lang=lang)
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

    n_img = st.text_input("Image URL (optional)", key="new_img")
    n_notes = st.text_area("Notes (optional)", height=80, key="new_notes")

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
            "notify": n_notify,  # NEW
            "email_to": n_email.strip(),
            "image_url": n_img.strip(),
            "notes": n_notes.strip(),
            "salt": str(time.time()),
        }
        config_data.setdefault("searches", []).append(new_s)
        save_config(config_data)

# =======================
# FOOTER: Advanced (less in sight)
# =======================
with st.expander("⚙️ Advanced", expanded=False):
    st.caption("Tools for power users: Maintenance, ID/Slug finders, and Push settings.")

    # Maintenance / reset
    st.subheader("Maintenance")
    _, state_data = _read_json_from_repo(STATE_FILE_PATH, {"notified": []})
    st.caption(f"Current dedupe cache entries: {len(state_data.get('notified', []) or [])}")
    if st.button("🔄 Reset state (get notifications again)"):
        reset_state()

    st.divider()

    # Quick ID / Slug Finder
    st.subheader("Quick ID / Slug Finder")
    st.caption("OpenTable — extracts ALL valid IDs (filters 0/null, requires 2+ digits)")
    ot_url = st.text_input("Paste OpenTable link", key="ot_url_adv")
    if st.button("Extract OpenTable IDs", key="btn_ot_ids"):
        ids = get_ot_ids(ot_url)
        st.session_state["ot_ids_found"] = ids
    ids_found = st.session_state.get("ot_ids_found", [])
    if ids_found:
        st.success(f"Found {len(ids_found)} IDs: {', '.join(ids_found)}")
        chosen = st.selectbox("Use this ID", ids_found, key="ot_id_choice_adv")
        st.session_state["last_ot_id"] = chosen

    st.caption("SevenRooms")
    sr_text = st.text_input("Paste SevenRooms link (or type slug)", key="sr_url_adv")
    if st.button("Extract SevenRooms slug", key="btn_sr_slug"):
        slug = get_sevenrooms_slug(sr_text)
        if slug:
            st.success(f"Slug: {slug}")
            st.session_state["last_sr_slug"] = slug
        else:
            st.error("Couldn’t find a slug in that text.")

    st.divider()

    # Push settings + test (kept out of sight)
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
