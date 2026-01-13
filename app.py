import streamlit as st
import json
import time
import datetime as dt
import requests
import re
from github import Github

# --- CONFIGURATION ---
REPO_NAME = "timobaaij/sevenrooms-notifier"
CONFIG_FILE_PATH = "config.json"

st.set_page_config(page_title="Reservation Manager", page_icon="🍽️", layout="wide")

# --- AUTH ---
try:
    token = st.secrets["GITHUB_TOKEN"]
    g = Github(token)
    repo = g.get_repo(REPO_NAME)
    contents = repo.get_contents(CONFIG_FILE_PATH)
    config_data = json.loads(contents.decoded_content.decode("utf-8"))
except Exception as e:
    st.error(f"❌ Connection Error: {e}")
    st.stop()

# --- HELPERS ---

def refresh_config():
    c = repo.get_contents(CONFIG_FILE_PATH)
    return c, json.loads(c.decoded_content.decode("utf-8"))


def save_config(new_data: dict):
    try:
        c, _ = refresh_config()
        repo.update_file(
            c.path,
            "Update via Web App",
            json.dumps(new_data, indent=2, sort_keys=True),
            c.sha,
        )
        st.toast("✅ Saved!", icon="💾")
        time.sleep(0.5)
        st.cache_data.clear()
        st.rerun()
    except Exception as e:
        st.error(f"Save Failed: {e}")


def parse_opentable_id(text: str):
    if not text:
        return None
    # rid=12345
    m = re.search(r"[?&]rid=(\d+)", text)
    if m:
        return m.group(1)
    # trailing digits
    m = re.search(r"(\d{3,})\/?$", text.strip())
    if m:
        return m.group(1)
    return None


def get_ot_id(url: str):
    quick = parse_opentable_id(url)
    if quick:
        return quick
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, headers=headers, timeout=12)
        matches = re.findall(r'"restaurantId":\s*"?(\d+)"?', r.text)
        valid = [m for m in matches if len(m) > 1 and m != "0"]
        return valid[0] if valid else None
    except Exception:
        return None


def get_sevenrooms_slug(text: str):
    if not text:
        return None
    # ?venue=slug
    m = re.search(r"[?&]venue=([a-zA-Z0-9_-]+)", text)
    if m:
        return m.group(1)
    # /reservations/slug
    m = re.search(r"/reservations/([a-zA-Z0-9_-]+)", text)
    if m:
        return m.group(1)
    # just a slug
    if re.fullmatch(r"[a-zA-Z0-9_-]{3,}", text.strip()):
        return text.strip()
    return None


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
            times = (block or {}).get("times", [])
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
                # prefer HH:MM label; include REQUEST marker
                try:
                    hhmm = dt.datetime.fromisoformat(str(iso).replace("Z", "+00:00")).strftime("%H:%M")
                except Exception:
                    m = re.search(r"\b([01]\d|2[0-3]):([0-5]\d)\b", str(iso))
                    hhmm = f"{m.group(1)}:{m.group(2)}" if m else str(iso)
                label = hhmm + (" (REQUEST)" if (is_req and not is_avail) else "")
                out.append(label)
    # de-dup
    seen = set()
    uniq = []
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
            hhmm = dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%H:%M")
            out.append(hhmm)
        except Exception:
            m = re.search(r"\b([01]\d|2[0-3]):([0-5]\d)\b", iso)
            if m:
                out.append(f"{m.group(1)}:{m.group(2)}")

    # de-dup
    seen = set()
    uniq = []
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


# --- MAIN LAYOUT ---
col_main, col_tools = st.columns([2.5, 1.5], gap="large")

# Ensure required sections exist
config_data.setdefault("global", {"channel": "SEVENROOMS_WIDGET", "delay_between_venues_sec": 0.5, "lang": "en"})
config_data.setdefault("ntfy_default", {"server": "https://ntfy.sh", "topic": "", "priority": "urgent", "tags": "rotating_light"})
config_data.setdefault("searches", [])

with col_main:
    st.title("🍽️ My Active Searches")

    searches = config_data.get("searches", [])
    if not searches:
        st.info("No active searches yet.")

    for i, s in enumerate(searches):
        p_raw = s.get("platform", "sevenrooms")
        p_label = str(p_raw).upper()
        with st.container(border=True):
            # Image (optional)
            img_url = s.get("image_url")
            if img_url:
                st.image(img_url, use_container_width=True)

            # Header
            st.subheader(f"📍 {s.get('id', 'Unnamed')} ({p_label})")

            # Compact details
            c1, c2, c3 = st.columns([1.2, 1.2, 1.6])
            with c1:
                st.caption("DATE")
                st.write(s.get("date", ""))
                st.caption("PARTY")
                st.write(str(s.get("party_size", "")))
            with c2:
                st.caption("TIME")
                ts = s.get("time_slot")
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

            # Extra details (clean, optional)
            if s.get("notes"):
                st.caption("NOTES")
                st.write(s.get("notes"))

            # Action Buttons
            a1, a2 = st.columns(2)
            with a1:
                show_edit = st.checkbox("✏️ Edit", key=f"edit_check_{i}")
            with a2:
                if st.button("🗑️ Delete", key=f"del_btn_{i}"):
                    searches.pop(i)
                    config_data["searches"] = searches
                    save_config(config_data)

            if show_edit:
                st.divider()
                with st.form(f"edit_form_{i}"):
                    e_name = st.text_input("Name", s.get("id", ""))
                    e_platform = st.selectbox("Platform", ["sevenrooms", "opentable"], index=0 if (s.get("platform","sevenrooms")=="sevenrooms") else 1)
                    e_venues = st.text_input("Venues (IDs/Slugs, comma separated)", ", ".join(s.get("venues", [])))
                    e_date = st.date_input("Date", dt.datetime.strptime(s.get("date"), "%Y-%m-%d").date() if s.get("date") else dt.date.today())
                    e_party = st.number_input("Party", 1, 20, value=int(s.get("party_size", 2)))

                    e_num_days = st.number_input("Num Days", 1, 7, value=int(s.get("num_days", 1)))

                    # time targeting
                    e_time_slot = st.text_input("Exact time (HH:MM) — leave blank for window", s.get("time_slot", ""))
                    e_window_start = st.text_input("Window start (HH:MM)", s.get("window_start", "18:00"))
                    e_window_end = st.text_input("Window end (HH:MM)", s.get("window_end", "22:00"))

                    e_email = st.text_input("Email Alert To", s.get("email_to", ""))
                    e_img = st.text_input("Image URL", s.get("image_url", ""))
                    e_notes = st.text_area("Notes (optional)", s.get("notes", ""), height=80)

                    if st.form_submit_button("💾 Save Changes"):
                        searches[i].update(
                            {
                                "id": e_name,
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
                                # changing salt forces fresh notifications for updated search rules
                                "salt": str(time.time()),
                            }
                        )
                        config_data["searches"] = searches
                        save_config(config_data)

with col_tools:
    st.header("➕ Add / Tools")

    # --- Quick finders (keep simple) ---
    with st.expander("🔎 Quick ID / Slug Finder", expanded=False):
        st.caption("OpenTable")
        ot_url = st.text_input("Paste OpenTable link")
        if st.button("Extract OpenTable ID"):
            found = get_ot_id(ot_url)
            if found:
                st.success(f"ID: {found}")
                st.session_state["last_ot_id"] = found
            else:
                st.error("Couldn’t find an ID from that link.")

        st.divider()
        st.caption("SevenRooms")
        sr_text = st.text_input("Paste SevenRooms link (or type slug)")
        if st.button("Extract SevenRooms slug"):
            slug = get_sevenrooms_slug(sr_text)
            if slug:
                st.success(f"Slug: {slug}")
                st.session_state["last_sr_slug"] = slug
            else:
                st.error("Couldn’t find a slug in that text.")

    # --- Notification settings (push) ---
    with st.expander("🔔 Push notification settings (ntfy)", expanded=False):
        nt = config_data.get("ntfy_default", {})
        server = st.text_input("Server", nt.get("server", "https://ntfy.sh"))
        topic = st.text_input("Topic", nt.get("topic", ""))
        priority = st.text_input("Priority", nt.get("priority", "urgent"))
        tags = st.text_input("Tags", nt.get("tags", "rotating_light"))

        c1, c2 = st.columns(2)
        with c1:
            if st.button("💾 Save push settings"):
                config_data["ntfy_default"] = {"server": server.strip(), "topic": topic.strip(), "priority": priority.strip(), "tags": tags.strip()}
                save_config(config_data)
        with c2:
            if st.button("🧪 Send test push"):
                ok, info = post_test_push(server.strip(), topic.strip(), "Test: Reservation Manager", "If you see this, push is working.", priority.strip(), tags.strip())
                if ok:
                    st.success("Sent ✅")
                else:
                    st.error(f"Failed: {info}")

    # --- Add new search ---
    st.subheader("New search")

    # pick platform + venue
    plat = st.selectbox("Platform", ["sevenrooms", "opentable"], key="new_platform")
    default_venue = ""
    if plat == "opentable":
        default_venue = st.session_state.get("last_ot_id", "")
    else:
        default_venue = st.session_state.get("last_sr_slug", "")

    n_venue = st.text_input("Venue ID/Slug (comma separated supported)", value=default_venue, key="new_venue")
    n_id = st.text_input("Search name", key="new_name")
    n_date = st.date_input("Date", key="new_date")
    n_party = st.number_input("Party", 1, 20, 2, key="new_party")
    n_num_days = st.number_input("Num Days", 1, 7, 1, key="new_num_days")

    # pull globals
    gbl = config_data.get("global", {})
    channel = gbl.get("channel", "SEVENROOMS_WIDGET")
    lang = gbl.get("lang", "en")

    # Time selection (load times)
    st.caption("Time")
    any_time = st.checkbox("Any time in a window", value=True, key="new_any_time")

    # Load times button
    if st.button("🔄 Load available times", key="load_times"):
        venue_first = (n_venue.split(",")[0].strip() if n_venue else "")
        if venue_first:
            with st.spinner("Fetching times…"):
                if plat == "opentable":
                    times = fetch_opentable_times(venue_first, str(n_date), int(n_party))
                else:
                    times = fetch_sevenrooms_times(venue_first, str(n_date), int(n_party), channel=channel, num_days=int(n_num_days), lang=lang)
            st.session_state["loaded_times"] = times
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

    # Other details
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
