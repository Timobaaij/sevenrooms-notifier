
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


def save_config(new_data: dict):
    """Refetch latest SHA before updating to prevent SHA mismatch."""
    try:
        c = repo.get_contents(CONFIG_FILE_PATH)
        repo.update_file(
            c.path,
            "Update via Web App",
            json.dumps(new_data, indent=2, sort_keys=True),
            c.sha,
        )
        st.toast("✅ Saved!", icon="💾")
        time.sleep(0.3)
        st.cache_data.clear()
        st.rerun()
    except Exception as e:
        st.error(f"Save Failed: {e}")


def reset_state():
    """Clear state.json so notifications can fire again."""
    try:
        c = repo.get_contents(STATE_FILE_PATH)
        new_state = {"notified": []}
        repo.update_file(
            c.path,
            "Reset notifier state (clear notified cache)",
            json.dumps(new_state, indent=2),
            c.sha,
        )
        st.toast("🔄 State reset — notifications will fire again", icon="🔔")
        time.sleep(0.3)
        st.rerun()
    except Exception as e:
        st.error(f"Reset failed: {e}")


# ---------- OpenTable ID Finder (ALL IDs + filtering) ----------

def parse_opentable_ids_from_url(text: str):
    """If rid= is present in URL, return all 2+ digit rids."""
    if not text:
        return []
    return re.findall(r"[?&]rid=(\d{2,})", text)


def get_ot_ids(url: str):
    """Extract ALL restaurantId values from page source (scripts + html),
    filter out: 0, null, <2 digits. Return de-duped list preserving order.
    """
    if not url:
        return []

    # 1) Quick IDs from URL
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

        # capture null or digits (quoted or not)
        raw = re.findall(r'"restaurantId"\s*:\s*(null|"?\d+"?)', combined, flags=re.IGNORECASE)

        cleaned = []
        for val in raw:
            v = str(val).strip().strip('"').lower()
            if v in ("null", "0", ""):
                continue
            if not v.isdigit():
                continue
            if len(v) < 2:
                continue
            cleaned.append(v)

        ids = ids + cleaned

    # 4) De-dupe preserving order
    seen = set()
    uniq = []
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


# ---------- Availability fetchers for time selection ----------

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

                # label as HH:MM and mark requestable-only
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

col_main, col_tools = st.columns([2.5, 1.5], gap="large")

# =======================
# LEFT: Active searches (clean tiles)
# =======================
with col_main:
    st.title("🍽️ My Active Searches")

    searches = config_data.get("searches", [])
    if not searches:
        st.info("No active searches yet.")

    for i, s in enumerate(searches):
        p_label = str(s.get("platform", "sevenrooms")).upper()

        with st.container(border=True):
            img_url = s.get("image_url")
            if img_url:
                st.image(img_url, use_container_width=True)

            st.subheader(f"📍 {s.get('id', 'Unnamed')} ({p_label})")

            c1, c2, c3 = st.columns([1.2, 1.2, 1.6])
            with c1:
                st.caption("DATE")
                st.write(s.get("date", ""))
                st.caption("PARTY")
                st.write(str(s.get("party_size", "")))
            with c2:
                st.caption("TIME")
                ts = (s.get("time_slot") or "").strip()
                if ts:
                    st.write(ts)
                else:
                    st.write(f"{s.get('window_start','')}–{s.get('window_end','')}")
                st.caption("DAYS")
                st.write(str(s.get("num_days", 1)))
            with c3:
                st.caption("VENUES")
                st.write(", ".join(s.get("venues", [])))
                if s.get("email_to"):
                    st.caption("EMAIL")
                    st.write(s.get("email_to"))

            if s.get("notes"):
                st.caption("NOTES")
                st.write(s.get("notes"))

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
                    e_platform = st.selectbox(
                        "Platform",
                        ["sevenrooms", "opentable"],
                        index=0 if (s.get("platform", "sevenrooms") == "sevenrooms") else 1,
                    )
                    e_venues = st.text_input("Venues (IDs/Slugs, comma separated)", ", ".join(s.get("venues", [])))
                    e_date = st.date_input(
                        "Date",
                        dt.datetime.strptime(s.get("date"), "%Y-%m-%d").date() if s.get("date") else dt.date.today(),
                    )
                    e_party = st.number_input("Party", 1, 20, value=int(s.get("party_size", 2)))
                    e_num_days = st.number_input("Num Days", 1, 7, value=int(s.get("num_days", 1)))

                    e_time_slot = st.text_input("Exact time (HH:MM) — blank for window", s.get("time_slot", ""))
                    e_window_start = st.text_input("Window start (HH:MM)", s.get("window_start", "18:00"))
                    e_window_end = st.text_input("Window end (HH:MM)", s.get("window_end", "22:00"))

                    e_email = st.text_input("Email alert to", s.get("email_to", ""))
                    e_img = st.text_input("Image URL", s.get("image_url", ""))
                    e_notes = st.text_area("Notes", s.get("notes", ""), height=80)

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
                                "email_to": e_email.strip(),
                                "image_url": e_img.strip(),
                                "notes": e_notes.strip(),
                                # updating salt forces fresh alerts for this search going forward
                                "salt": str(time.time()),
                            }
                        )
                        config_data["searches"] = searches
                        save_config(config_data)


# =======================
# RIGHT: Tools + Add new
# =======================
with col_tools:
    st.header("➕ Add / Tools")

    # Maintenance / reset
    with st.expander("🧹 Maintenance", expanded=False):
        _, state_data = _read_json_from_repo(STATE_FILE_PATH, {"notified": []})
        notified_count = len(state_data.get("notified", []) or [])
        st.caption(f"Current dedupe cache entries: {notified_count}")
        st.warning("Resetting state clears the dedupe cache so alerts can fire again for previously-seen slots.")
        if st.button("🔄 Reset state (get notifications again)"):
            reset_state()

    # Quick finders
    with st.expander("🔎 Quick ID / Slug Finder", expanded=False):
        st.caption("OpenTable — extracts ALL valid IDs (filters 0/null, requires 2+ digits)")
        ot_url = st.text_input("Paste OpenTable link", key="ot_url")

        if st.button("Extract OpenTable IDs"):
            ids = get_ot_ids(ot_url)
            st.session_state["ot_ids_found"] = ids

        ids_found = st.session_state.get("ot_ids_found", [])
        if ids_found:
            st.success(f"Found {len(ids_found)} IDs: {', '.join(ids_found)}")
            chosen = st.selectbox("Use this ID", ids_found, key="ot_id_choice")
            st.session_state["last_ot_id"] = chosen
        else:
            st.caption("No IDs found yet (or none match the filter).")

        st.divider()
        st.caption("SevenRooms")
        sr_text = st.text_input("Paste SevenRooms link (or type slug)", key="sr_url")
        if st.button("Extract SevenRooms slug"):
            slug = get_sevenrooms_slug(sr_text)
            if slug:
                st.success(f"Slug: {slug}")
                st.session_state["last_sr_slug"] = slug
            else:
                st.error("Couldn’t find a slug in that text.")

    # Push settings + test
    with st.expander("🔔 Push notification settings (ntfy)", expanded=False):
        nt = config_data.get("ntfy_default", {})
        server = st.text_input("Server", nt.get("server", "https://ntfy.sh"))
        topic = st.text_input("Topic", nt.get("topic", ""))
        priority = st.text_input("Priority", nt.get("priority", "urgent"))
        tags = st.text_input("Tags", nt.get("tags", "rotating_light"))

        c1, c2 = st.columns(2)
        with c1:
            if st.button("💾 Save push settings"):
                config_data["ntfy_default"] = {
                    "server": server.strip(),
                    "topic": topic.strip(),
                    "priority": priority.strip(),
                    "tags": tags.strip(),
                }
                save_config(config_data)
        with c2:
            if st.button("🧪 Send test push"):
                ok, info = post_test_push(
                    server.strip(),
                    topic.strip(),
                    "Test: Reservation Manager",
                    "If you see this, push is working.",
                    priority.strip(),
                    tags.strip(),
                )
                st.success("Sent ✅") if ok else st.error(f"Failed: {info}")

    # Add new search
    st.subheader("New search")

    plat = st.selectbox("Platform", ["sevenrooms", "opentable"], key="new_platform")
    default_venue = st.session_state.get("last_ot_id", "") if plat == "opentable" else st.session_state.get("last_sr_slug", "")

    n_venue = st.text_input("Venue ID/Slug (comma separated supported)", value=default_venue, key="new_venue")
    n_id = st.text_input("Search name", key="new_name")
    n_date = st.date_input("Date", key="new_date")
    n_party = st.number_input("Party", 1, 20, 2, key="new_party")
    n_num_days = st.number_input("Num Days", 1, 7, 1, key="new_num_days")

    gbl = config_data.get("global", {})
    channel = gbl.get("channel", "SEVENROOMS_WIDGET")
    lang = gbl.get("lang", "en")

    st.caption("Time")
    any_time = st.checkbox("Any time in a window", value=True, key="new_any_time")

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

    n_email = st.text_input("Email alert to (optional)", key="new_email")
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
            "email_to": n_email.strip(),
            "image_url": n_img.strip(),
            "notes": n_notes.strip(),
            "salt": str(time.time()),
        }
        config_data.setdefault("searches", []).append(new_s)
        save_config(config_data)
