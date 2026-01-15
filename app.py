
import streamlit as st
import json
import time
import datetime as dt
import requests
import re
from github import Github
from streamlit_calendar import calendar  # calendar component

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
# DATE HELPERS + MULTI-DATE CALENDAR (POPOVER)
# =========================================================
def _to_date(value):
    """Accepts dt.date or string YYYY-MM-DD / DD-MM-YYYY. Returns dt.date or None."""
    if value is None:
        return None
    if isinstance(value, dt.date):
        return value
    s = str(value).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except Exception:
            pass
    return None

def _to_iso(value):
    d = _to_date(value)
    return d.isoformat() if d else None

def _get_dates_list(search: dict):
    """Backwards compatible: read search['dates'] list if present, else use search['date'].""" 
    dates = []
    if isinstance(search.get("dates"), list) and search["dates"]:
        for x in search["dates"]:
            iso = _to_iso(x)
            if iso:
                dates.append(iso)
    else:
        iso = _to_iso(search.get("date"))
        if iso:
            dates.append(iso)
    dates = sorted(set(dates))
    if not dates:
        dates = [dt.date.today().isoformat()]
    return dates

def _dates_display(search: dict) -> str:
    """For dashboard: show multi-date list if present, else single date."""
    if isinstance(search.get("dates"), list) and search["dates"]:
        return ", ".join(search["dates"])
    return (search.get("date") or "")

def _build_background_events(selected_dates):
    """
    Highlight selected days with FullCalendar background events.
    end must be exclusive for all-day spans => date + 1 day.
    """
    events = []
    for iso in sorted(set(selected_dates or [])):
        d = _to_date(iso)
        if not d:
            continue
        end = (d + dt.timedelta(days=1)).isoformat()
        events.append(
            {
                "title": "",
                "start": d.isoformat(),
                "end": end,
                "allDay": True,
                "display": "background",
                "backgroundColor": "#4CAF5066",
                "borderColor": "#4CAF50",
            }
        )
    return events

def multi_date_calendar_picker(
    state_key: str,
    component_key: str,
    height: int = 280,
    button_label: str = "Select date(s)",
):
    """
    Multi-select calendar:
    - click dates to toggle selection
    - calendar is shown in a popover (or expander fallback)
    - stores ISO dates in st.session_state[state_key]
    """
    if state_key not in st.session_state:
        st.session_state[state_key] = [dt.date.today().isoformat()]

    # normalize / clean
    cleaned = []
    for x in st.session_state.get(state_key, []):
        iso = _to_iso(x)
        if iso:
            cleaned.append(iso)
    cleaned = sorted(set(cleaned))
    st.session_state[state_key] = cleaned

    # Show a tidy summary outside the popover
    if cleaned:
        st.caption(f"🗓 Selected: {', '.join(cleaned)}")
    else:
        st.caption("🗓 Selected: —")

    # FullCalendar options (height belongs HERE) 
    options = {
        "initialView": "dayGridMonth",
        "headerToolbar": {"left": "today prev,next", "center": "title", "right": ""},
        "dayMaxEvents": True,
        "selectable": True,
        "height": height,
        "fixedWeekCount": False,  # reduces empty rows for short months
    }

    # Highlight selected days
    events = _build_background_events(cleaned)

    # Compact CSS (supported by streamlit-calendar) 
    compact_css = """
    .fc .fc-toolbar { margin-bottom: 0.25rem !important; }
    .fc .fc-toolbar-title { font-size: 1.0rem !important; }
    .fc .fc-button { padding: 0.15rem 0.35rem !important; font-size: 0.8rem !important; }
    .fc .fc-daygrid-day-number { padding: 2px 4px !important; font-size: 0.85rem !important; }
    .fc .fc-daygrid-day-frame { padding: 1px !important; }
    """

    cal_value = None

    # Popover (best UX) [1](https://engage.cloud.microsoft/main/threads/eyJfdHlwZSI6IlRocmVhZCIsImlkIjoiMTA5MTEwNzk2ODMzNTg3MiJ9)
    if hasattr(st, "popover"):
        with st.popover(button_label, type="secondary", help="Click dates to toggle. Click again to remove."):
            cal_value = calendar(events=events, options=options, custom_css=compact_css, key=component_key)
            cols = st.columns([0.25, 0.75])
            with cols[0]:
                if st.button("🧹 Clear", key=f"{state_key}_clear"):
                    st.session_state[state_key] = []
                    st.rerun()
            with cols[1]:
                st.caption("Tip: Click a date again to remove it.")

    else:
        # Fallback for older Streamlit: collapsed expander
        with st.expander(button_label, expanded=False):
            cal_value = calendar(events=events, options=options, custom_css=compact_css, key=component_key)
            if st.button("🧹 Clear", key=f"{state_key}_clear"):
                st.session_state[state_key] = []
                st.rerun()

    # Handle date clicks (toggle)
    if isinstance(cal_value, dict) and cal_value.get("callback") == "dateClick":
        clicked = (cal_value.get("dateClick") or {}).get("date")
        if clicked:
            clicked_iso = str(clicked)[:10]  # YYYY-MM-DD
            if clicked_iso in cleaned:
                cleaned = [d for d in cleaned if d != clicked_iso]
            else:
                cleaned = sorted(set(cleaned + [clicked_iso]))
            st.session_state[state_key] = cleaned
            st.rerun()

    return cleaned

# =========================================================
# DEFAULTS (unchanged)
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

    date_txt = _dates_display(s)  # supports multi-date display
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
            st.warning(
                f"Delete **{s.get('id','Unnamed')}**? This removes the entry from `config.json`.",
                icon="⚠️",
            )
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

        # Edit form
        if st.session_state.get(f"show_edit_{idx}", False):
            st.divider()
            st.caption("Platform is forced to SevenRooms on save.")

            # seed dates into session_state once
            dates_key = f"edit_dates_{idx}"
            if dates_key not in st.session_state:
                st.session_state[dates_key] = _get_dates_list(s)

            # Date picker (popover calendar)
            selected_dates = multi_date_calendar_picker(
                dates_key,
                component_key=f"cal_edit_{idx}",
                height=280,
                button_label="Select date(s)",
            )

            # Everything else stays in the form as before
            with st.form(f"edit_form_{idx}"):
                st.caption("Everything else remains unchanged. Dates are saved as a list + first date for compatibility.")
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
                with bcols[0]:
                    submitted = st.form_submit_button("💾 Save", type="primary")
                with bcols[1]:
                    cancelled = st.form_submit_button("Cancel")

                if submitted:
                    dates_list = sorted(set(selected_dates or []))
                    if not dates_list:
                        dates_list = [dt.date.today().isoformat()]

                    config_data["searches"][idx].update(
                        {
                            "id": e_name.strip() or "Unnamed",
                            "platform": "sevenrooms",
                            "venues": [v.strip() for v in e_venues.split(",") if v.strip()],
                            "date": dates_list[0],   # backwards compatible
                            "dates": dates_list,     # multi-date
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
# ADD NEW SEARCH (SevenRooms only)
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

# Date picker (popover calendar)
new_dates_key = "new_dates"
if new_dates_key not in st.session_state:
    st.session_state[new_dates_key] = [dt.date.today().isoformat()]

new_dates = multi_date_calendar_picker(
    new_dates_key,
    component_key="cal_new",
    height=280,
    button_label="Select date(s)",
)

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
    dates_list = sorted(set(new_dates or []))
    if not dates_list:
        dates_list = [dt.date.today().isoformat()]

    new_s = {
        "id": n_id.strip() or "Unnamed",
        "platform": "sevenrooms",
        "venues": [v.strip() for v in n_venue.split(",") if v.strip()],
        "party_size": int(n_party),
        "date": dates_list[0],   # backwards compatible
        "dates": dates_list,     # multi-date
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
