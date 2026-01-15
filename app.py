
import streamlit as st
import json
import time
import datetime as dt
import requests
import re
from github import Github

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
# HELPERS (Repo JSON I/O)
# =========================================================
def _read_json_from_repo(path: str, default: dict):
    try:
        c = repo.get_contents(path)
        return c, json.loads(c.decoded_content.decode("utf-8"))
    except Exception:
        return None, default

def _update_file_json(path: str, message: str, data: dict):
    c = repo.get_contents(path)
    repo.update_file(
        c.path,
        message,
        json.dumps(data, indent=2, sort_keys=True),
        c.sha,
    )

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
        _update_file_json(
            STATE_FILE_PATH,
            "Reset notifier state (clear notified cache)",
            {"notified": []},
        )
        st.toast("🔄 State reset — notifications will fire again", icon="🔔")
        time.sleep(0.15)
        st.rerun()
    except Exception as e:
        st.error(f"Reset failed: {e}")

# =========================================================
# SevenRooms helpers (Slug finder + Time loader for UI)
# =========================================================
def get_sevenrooms_slug(text: str):
    if not text:
        return None
    m = re.search(r"[?&]venue=([a-zA-Z0-9_\-\\]+)", text)
    if m:
        return m.group(1)
    m = re.search(r"/reservations/([a-zA-Z0-9_\-\\]+)", text)
    if m:
        return m.group(1)
    if re.fullmatch(r"[a-zA-Z0-9_\-\\]{3,}", text.strip()):
        return text.strip()
    return None

def fetch_sevenrooms_times(
    venue: str,
    date_yyyy_mm_dd: str,
    party: int,
    channel: str,
    num_days: int = 1,
    lang: str = "en",
):
    """For the UI picker only: returns HH:MM labels including requestable slots."""
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
        r = requests.get(url, timeout=15)
    except Exception:
        return []
    if not r.ok:
        return []
    try:
        j = r.json()
    except Exception:
        return []
    out = []
    availability = (j.get("data", {}) or {}).get("availability", {}) or {}
    for _, day in availability.items():
        if not isinstance(day, list):
            continue
        for block in day:
            block = block or {}
            for t in block.get("times", []) or []:
                if not isinstance(t, dict):
                    continue
                is_avail = bool(t.get("is_available"))
                is_req = bool(t.get("is_requestable"))
                if not (is_avail or is_req):
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
                label = hhmm + (" (REQUEST)" if (is_req and not is_avail) else "")
                out.append(label)

    # de-dup preserve order
    seen, uniq = set(), []
    for x in out:
        if x not in seen:
            uniq.append(x)
            seen.add(x)
    return uniq

# =========================================================
# DATE HELPERS + NATIVE INSTANT-ADD MULTI-DATE SELECTOR
# =========================================================
def _normalize_iso_dates(values):
    """Normalize a list of dates/strings to sorted unique YYYY-MM-DD strings."""
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

def _get_dates_from_search(s: dict):
    """Backwards compatible: use s['dates'] if present else s['date'].""" 
    if isinstance(s.get("dates"), list) and s["dates"]:
        dates = _normalize_iso_dates(s["dates"])
        if dates:
            return dates
    if s.get("date"):
        dates = _normalize_iso_dates([s["date"]])
        if dates:
            return dates
    return [dt.date.today().isoformat()]

def _dates_display(s: dict) -> str:
    if isinstance(s.get("dates"), list) and s["dates"]:
        return ", ".join(s["dates"])
    return s.get("date", "") or ""

def native_multi_date_selector(state_key: str, label: str):
    """
    Native, fast multi-date selector:
      - selecting a date in the calendar immediately adds it to Selected dates
      - remove by unselecting in the multiselect
      - stores ISO strings in st.session_state[state_key]
    """
    if state_key not in st.session_state:
        st.session_state[state_key] = [dt.date.today().isoformat()]

    st.session_state[state_key] = _normalize_iso_dates(st.session_state[state_key])

    picker_key = f"{state_key}_picker"
    chosen_key = f"{state_key}_chosen"

    # Ensure chosen state exists so the multiselect updates smoothly
    if chosen_key not in st.session_state:
        st.session_state[chosen_key] = list(st.session_state[state_key])

    def _on_pick():
        picked = st.session_state.get(picker_key)
        if isinstance(picked, dt.date):
            iso = picked.isoformat()
            cur = _normalize_iso_dates(st.session_state.get(state_key, []))
            if iso not in cur:
                cur = _normalize_iso_dates(cur + [iso])
                st.session_state[state_key] = cur
                st.session_state[chosen_key] = list(cur)

    st.markdown(f"**{label}**")

    top = st.columns([0.65, 0.35])
    with top[0]:
        # Selecting in the calendar triggers _on_pick -> updates list immediately
        st.date_input("Select a date", key=picker_key, on_change=_on_pick)
    with top[1]:
        if st.button("Clear all", key=f"{state_key}_clear_all"):
            st.session_state[state_key] = []
            st.session_state[chosen_key] = []
            st.rerun()

    cur = _normalize_iso_dates(st.session_state.get(state_key, []))
    if not cur:
        st.info("No dates selected.")
        return []

    # This now updates immediately after date selection because we set chosen_key in _on_pick
    chosen = st.multiselect(
        "Selected dates (unselect to remove)",
        options=cur,
        default=cur,
        key=chosen_key,
    )
    chosen = _normalize_iso_dates(chosen)
    if chosen != cur:
        st.session_state[state_key] = chosen
        st.session_state[chosen_key] = chosen
        st.rerun()

    st.caption(f"Selected: {', '.join(chosen)}")
    return chosen

# =========================================================
# DEFAULTS
# =========================================================
config_data.setdefault(
    "global",
    {"channel": "SEVENROOMS_WIDGET", "delay_between_venues_sec": 0.5, "lang": "en"},
)
config_data.setdefault(
    "ntfy_default",
    {"server": "https://ntfy.sh", "topic": "", "priority": "urgent", "tags": "rotating_light"},
)
config_data.setdefault("searches", [])
NOTIFY_LABELS = ["Push", "Email", "Both", "None"]
NOTIFY_MAP = {"Push": "push", "Email": "email", "Both": "both", "None": "none"}

# =========================================================
# UI HEADER
# =========================================================
st.title("🍽️ Reservation Dashboard")
st.caption("Platform: SevenRooms only (OpenTable removed).")

searches_all = config_data.get("searches", []) or []
if not searches_all:
    st.info("No active searches yet.")

def _toggle(key: str):
    st.session_state[key] = not st.session_state.get(key, False)

# =========================================================
# DASHBOARD LIST
# =========================================================
for idx, s in enumerate(searches_all):
    platform = (s.get("platform") or "sevenrooms").lower()
    is_supported = (platform == "sevenrooms")

    date_txt = _dates_display(s)
    party_txt = str(s.get("party_size", ""))
    time_slot = (s.get("time_slot") or "").strip()
    window_txt = f"{s.get('window_start','')}–{s.get('window_end','')}"
    time_txt = time_slot if time_slot else window_txt
    notify_txt = (s.get("notify") or "both").title()

    with st.container(border=True):
        header_cols = st.columns([0.68, 0.32])

        with header_cols[0]:
            title = s.get("id", "Unnamed")
            st.markdown(
                f"**📍 {title} (SevenRooms)**"
                if is_supported
                else f"**📍 {title} (Unsupported platform entry)**"
            )
            st.caption(f"🗓 {date_txt} · 👥 {party_txt} · ⏱ {time_txt} · 🔔 {notify_txt}")
            if not is_supported:
                st.warning(
                    "This entry uses an unsupported platform and will be ignored by the scheduler. Edit + save to convert it to SevenRooms, or delete it.",
                    icon="⚠️",
                )

        with header_cols[1]:
            action_cols = st.columns(3)
            with action_cols[0]:
                if st.button("See details", key=f"btn_details_{idx}"):
                    _toggle(f"show_details_{idx}")
            with action_cols[1]:
                if st.button("Edit", key=f"btn_edit_{idx}"):
                    _toggle(f"show_edit_{idx}")
            with action_cols[2]:
                if st.button("🗑️ Delete", key=f"btn_delete_{idx}"):
                    st.session_state[f"confirm_delete_{idx}"] = True

        # Delete confirmation
        if st.session_state.get(f"confirm_delete_{idx}", False):
            st.divider()
            st.warning(f"Delete **{s.get('id','Unnamed')}**? This removes the entry from `config.json`.", icon="⚠️")
            c1, c2 = st.columns(2)
            with c1:
                if st.button("✅ Confirm delete", type="primary", key=f"btn_confirm_delete_{idx}"):
                    searches_all.pop(idx)
                    config_data["searches"] = searches_all
                    st.session_state[f"confirm_delete_{idx}"] = False
                    save_config(config_data)
            with c2:
                if st.button("Cancel", key=f"btn_cancel_delete_{idx}"):
                    st.session_state[f"confirm_delete_{idx}"] = False

        # Details view
        if st.session_state.get(f"show_details_{idx}", False):
            st.divider()
            st.write(f"**Venues**: {', '.join(s.get('venues', [])) or '—'}")
            st.write(f"**Dates**: {date_txt or '—'}")
            st.write(f"**Num Days**: {int(s.get('num_days', 1))}")
            st.write(f"**Email**: {s.get('email_to') or '—'}")
            notes = (s.get("notes") or "").strip()
            if notes:
                st.write(f"**Notes**: {notes}")

        # Edit view
        if st.session_state.get(f"show_edit_{idx}", False):
            st.divider()
            st.caption("Platform is forced to SevenRooms on save.")

            dates_key = f"edit_dates_{idx}"
            if dates_key not in st.session_state:
                st.session_state[dates_key] = _get_dates_from_search(s)

            selected_dates = native_multi_date_selector(dates_key, "Dates")

            with st.form(f"edit_form_{idx}"):
                e_name = st.text_input("Name", s.get("id", ""))
                e_venues = st.text_input("Venues (slugs, comma separated)", ", ".join(s.get("venues", [])))
                e_party = st.number_input("Party", 1, 20, value=int(s.get("party_size", 2)))
                e_num_days = st.number_input("Num Days", 1, 7, value=int(s.get("num_days", 1)))
                e_time_slot = st.text_input("Exact time (HH:MM) — leave blank for window", s.get("time_slot", ""))
                e_window_start = st.text_input("Window start (HH:MM)", s.get("window_start", "18:00"))
                e_window_end = st.text_input("Window end (HH:MM)", s.get("window_end", "22:00"))

                e_notify_label = st.selectbox(
                    "Notification method",
                    NOTIFY_LABELS,
                    index=NOTIFY_LABELS.index((s.get("notify") or "both").title()),
                )
                e_notify = NOTIFY_MAP[e_notify_label]
                e_email = st.text_input("Email alert to (optional)", s.get("email_to", ""))
                e_notes = st.text_area("Notes (optional)", s.get("notes", ""), height=80)

                bcols = st.columns(2)
                submitted = bcols[0].form_submit_button("💾 Save", type="primary")
                cancelled = bcols[1].form_submit_button("Cancel")

                if submitted:
                    dates_list = _normalize_iso_dates(selected_dates)
                    if not dates_list:
                        dates_list = [dt.date.today().isoformat()]

                    config_data["searches"][idx].update(
                        {
                            "id": e_name.strip() or "Unnamed",
                            "platform": "sevenrooms",
                            "venues": [v.strip() for v in e_venues.split(",") if v.strip()],
                            "date": dates_list[0],
                            "dates": dates_list,
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
                    save_config(config_data)

                if cancelled:
                    st.session_state[f"show_edit_{idx}"] = False
                    st.rerun()

# =========================================================
# ADD NEW SEARCH
# =========================================================
st.subheader("➕ Add New Search")
st.caption("SevenRooms only.")

add_cols = st.columns([0.5, 0.5])
with add_cols[0]:
    default_venue = st.session_state.get("last_sr_slug", "")
    n_venue = st.text_input("Venue slug(s) (comma separated supported)", value=default_venue, key="new_venue")
    n_id = st.text_input("Search name", key="new_name")
    n_party = st.number_input("Party", 1, 20, 2, key="new_party")
    n_num_days = st.number_input("Num Days", 1, 7, 1, key="new_num_days")

with add_cols[1]:
    n_notify_label = st.selectbox("Notification method", NOTIFY_LABELS, index=2, key="new_notify_label")
    n_notify = NOTIFY_MAP[n_notify_label]
    n_email = st.text_input("Email alert to (optional)", key="new_email")
    st.caption("Time")
    any_time = st.checkbox("Any time in a window", value=True, key="new_any_time")

new_dates_key = "new_dates_list"
if new_dates_key not in st.session_state:
    st.session_state[new_dates_key] = [dt.date.today().isoformat()]

new_dates = native_multi_date_selector(new_dates_key, "Dates")

gbl = config_data.get("global", {})
channel = gbl.get("channel", "SEVENROOMS_WIDGET")
lang = gbl.get("lang", "en")

if st.button("🔄 Load available times", key="load_times"):
    venue_first = (n_venue.split(",")[0].strip() if n_venue else "")
    date_for_times = (new_dates[0] if new_dates else dt.date.today().isoformat())
    if venue_first:
        with st.spinner("Fetching times…"):
            times_list = fetch_sevenrooms_times(
                venue_first,
                str(date_for_times),
                int(n_party),
                channel=channel,
                num_days=int(n_num_days),
                lang=lang,
            )
        st.session_state["loaded_times"] = times_list
    else:
        st.session_state["loaded_times"] = []

loaded_times = st.session_state.get("loaded_times", [])

if any_time:
    n_window_start = st.text_input("Window start (HH:MM)", value="18:00", key="new_wstart")
    n_window_end = st.text_input("Window end (HH:MM)", value="22:00", key="new_wend")
    n_time_slot = ""
else:
    if loaded_times:
        choice = st.selectbox("Pick a time", loaded_times, key="new_time_pick")
        n_time_slot = choice.split(" ")[0]
    else:
        n_time_slot = st.text_input("Exact time (HH:MM)", value="19:00", key="new_time_manual")
    n_window_start, n_window_end = "", ""

if st.button("🚀 Launch search", type="primary", key="launch"):
    dates_list = _normalize_iso_dates(new_dates)
    if not dates_list:
        dates_list = [dt.date.today().isoformat()]

    new_s = {
        "id": n_id.strip() or "Unnamed",
        "platform": "sevenrooms",
        "venues": [v.strip() for v in n_venue.split(",") if v.strip()],
        "party_size": int(n_party),
        "date": dates_list[0],
        "dates": dates_list,
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
# ADVANCED (collapsed)
# =========================================================
with st.expander("⚙️ Advanced", expanded=False):
    st.caption("Maintenance, SevenRooms slug finder, and push settings.")
    st.subheader("Maintenance")
    _, state_data = _read_json_from_repo(STATE_FILE_PATH, {"notified": []})
    st.caption(f"Current dedupe cache entries: {len(state_data.get('notified', []) or [])}")
    if st.button("🔄 Reset state (get notifications again)"):
        reset_state()

    st.divider()
    st.subheader("Quick Slug Finder (SevenRooms)")
    st.caption("Paste a SevenRooms link (or type a slug).")
    sr_text = st.text_input("SevenRooms link / slug", key="sr_url_adv")
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
