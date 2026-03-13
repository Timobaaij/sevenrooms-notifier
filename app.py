import streamlit as st
import json
import time
import datetime as dt
import re
import requests
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
    repo.update_file(c.path, message, json.dumps(data, indent=2, sort_keys=True), c.sha)

def save_config(new_data: dict):
    try:
        _update_file_json(CONFIG_FILE_PATH, "Update via Web App", new_data)
        st.toast("✅ Saved!", icon="💾")
        time.sleep(0.15)
        st.cache_data.clear()
        st.rerun()
    except Exception as e:
        st.error(f"Save Failed: {e}")

def reset_state():
    try:
        _update_file_json(STATE_FILE_PATH, "Reset notifier state", {"notified": []})
        st.toast("🔄 State reset — notifications will fire again", icon="🔔")
        time.sleep(0.15)
        st.rerun()
    except Exception as e:
        st.error(f"Reset failed: {e}")

# =========================================================
# Platform Helpers
# =========================================================
def get_sevenrooms_slug(text: str):
    if not text: return None
    m = re.search(r"[\?&]venue=([a-zA-Z0-9_\-\\]+)", text)
    if m: return m.group(1)
    m = re.search(r"/reservations/([a-zA-Z0-9_\-\\]+)", text)
    if m: return m.group(1)
    if re.fullmatch(r"[a-zA-Z0-9_\-\\]{3,}", text.strip()): return text.strip()
    return None

def get_opentable_slug(text: str):
    if not text: return None
    m = re.search(r"/r/([a-zA-Z0-9_\-\\]+)", text)
    if m: return m.group(1)
    if re.fullmatch(r"[a-zA-Z0-9_\-\\]{3,}", text.strip()): return text.strip()
    return None

def fetch_sevenrooms_times(venue: str, date_yyyy_mm_dd: str, party: int, channel: str, num_days: int = 1, lang: str = "en"):
    try: d_sr = dt.datetime.strptime(date_yyyy_mm_dd, "%Y-%m-%d").strftime("%m-%d-%Y")
    except Exception: return []
    url = (f"https://www.sevenrooms.com/api-yoa/availability/widget/range"
           f"?venue={venue}&party_size={party}&start_date={d_sr}&num_days={num_days}&channel={channel}&lang={lang}")
    try: r = requests.get(url, timeout=15)
    except Exception: return []
    if not r.ok: return []
    try: j = r.json()
    except Exception: return []

    out = []
    avail = (j.get("data", {}) or {}).get("availability", {}) or {}
    for _, day in avail.items():
        if not isinstance(day, list): continue
        for block in day:
            for t in (block or {}).get("times", []) or []:
                if not isinstance(t, dict): continue
                is_req = t.get("is_requestable") is True
                if t.get("is_waitlist") is True: continue
                is_avail = t.get("is_available") is True if "is_available" in t else bool(t.get("access_persistent_id"))
                if not (is_avail or is_req): continue
                iso = t.get("time_iso") or t.get("date_time") or t.get("time")
                if not iso: continue
                try: hhmm = dt.datetime.fromisoformat(str(iso).replace("Z", "+00:00")).strftime("%H:%M")
                except Exception:
                    m = re.search(r"\b([01]\d|2[0-3]):([0-5]\d)\b", str(iso))
                    hhmm = f"{m.group(1)}:{m.group(2)}" if m else None
                if hhmm: out.append(hhmm + (" (REQUEST)" if (is_req and not is_avail) else ""))
    
    seen, uniq = set(), []
    for x in out:
        if x not in seen:
            uniq.append(x)
            seen.add(x)
    return uniq

def fetch_opentable_times(slug: str, date_yyyy_mm_dd: str, party: int):
    from curl_cffi import requests as c_requests
    from bs4 import BeautifulSoup
    url = f"https://www.opentable.co.uk/r/{slug}?covers={party}&dateTime={date_yyyy_mm_dd}T19:00:00"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-GB,en;q=0.5",
    }
    try: r = c_requests.get(url, headers=headers, impersonate="chrome110", timeout=25)
    except Exception: return []

    out = []
    for match in re.finditer(r'"time"\s*:\s*"([^"]+)"[^{}]*?"isAvailable"\s*:\s*true', r.text):
        out.append(match.group(1))
    if not out:
        soup = BeautifulSoup(r.text, "html.parser")
        buttons = soup.find_all("a", href=re.compile(r"/book/|/restref/"))
        for b in buttons:
            txt = b.get_text(strip=True)
            if re.search(r"\d{1,2}:\d{2}", txt): out.append(txt)

    seen, uniq = set(), []
    for x in out:
        hhmm = x
        try:
            if "T" in x: hhmm = dt.datetime.fromisoformat(x.replace("Z", "+00:00")).strftime("%H:%M")
            else:
                m = re.search(r"\b([01]\d|2[0-3]):([0-5]\d)\b", x)
                if m: hhmm = f"{m.group(1)}:{m.group(2)}"
        except Exception: pass
        if hhmm and hhmm not in seen:
            seen.add(hhmm)
            uniq.append(hhmm)
    return sorted(uniq)

# =========================================================
# NATIVE MULTI-DATE SELECTOR
# =========================================================
def _normalize_iso_dates(values):
    out = []
    for v in values or []:
        if isinstance(v, dt.date): out.append(v.isoformat())
        else:
            s = str(v).strip()
            if not s: continue
            parsed = None
            for fmt in ("%Y-%m-%d", "%d-%m-%Y"):
                try:
                    parsed = dt.datetime.strptime(s, fmt).date().isoformat()
                    break
                except Exception: pass
            if parsed: out.append(parsed)
    return sorted(set(out))

def _get_dates_from_search(s: dict):
    if isinstance(s.get("dates"), list) and s["dates"]:
        dates = _normalize_iso_dates(s["dates"])
        if dates: return dates
    if s.get("date"):
        dates = _normalize_iso_dates([s["date"]])
        if dates: return dates
    return [dt.date.today().isoformat()]

def _dates_display(s: dict) -> str:
    if isinstance(s.get("dates"), list) and s["dates"]: return ", ".join(s["dates"])
    return s.get("date", "") or ""

def native_multi_date_selector(state_key: str, label: str, chips_per_row: int = 4):
    if state_key not in st.session_state: st.session_state[state_key] = [dt.date.today().isoformat()]
    st.session_state[state_key] = _normalize_iso_dates(st.session_state[state_key])

    picker_key = f"{state_key}__picker"
    def _on_pick():
        picked = st.session_state.get(picker_key)
        if isinstance(picked, dt.date):
            iso = picked.isoformat()
            cur = _normalize_iso_dates(st.session_state.get(state_key, []))
            if iso not in cur: st.session_state[state_key] = _normalize_iso_dates(cur + [iso])

    st.markdown(f"**{label}**")
    top = st.columns([0.70, 0.30])
    with top[0]: st.date_input("Select a date", key=picker_key, on_change=_on_pick)
    with top[1]:
        if st.button("Clear all", key=f"{state_key}__clear_all"):
            st.session_state[state_key] = []
            st.rerun()

    cur = _normalize_iso_dates(st.session_state.get(state_key, []))
    if not cur: return []

    st.caption("Click ✕ on a date to remove it.")
    for start in range(0, len(cur), chips_per_row):
        row = cur[start : start + chips_per_row]
        cols = st.columns([1] * len(row))
        for col, d in zip(cols, row):
            with col:
                if st.button(f"{d} ✕", key=f"{state_key}__rm__{d}"):
                    st.session_state[state_key] = [x for x in cur if x != d]
                    st.rerun()

    return cur

# =========================================================
# DEFAULTS & HEADER
# =========================================================
config_data.setdefault("global", {"channel": "SEVENROOMS_WIDGET", "delay_between_venues_sec": 0.5, "lang": "en"})
config_data.setdefault("searches", [])
NOTIFY_LABELS = ["Push", "Email", "Both", "None"]
NOTIFY_MAP = {"Push": "push", "Email": "email", "Both": "both", "None": "none"}

st.title("🍽️ Reservation Dashboard")
st.caption("Supports SevenRooms & OpenTable.")

searches_all = config_data.get("searches", []) or []
if not searches_all: st.info("No active searches yet.")

def _toggle(key: str):
    st.session_state[key] = not st.session_state.get(key, False)

# =========================================================
# DASHBOARD LIST
# =========================================================
for idx, s in enumerate(searches_all):
    platform = (s.get("platform") or "sevenrooms").lower()
    is_supported = platform in ["sevenrooms", "opentable"]
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
            plat_display = "SevenRooms" if platform == "sevenrooms" else "OpenTable"
            st.markdown(f"**📍 {title} ({plat_display})**" if is_supported else f"**📍 {title} (Unsupported)**")
            st.caption(f"🗓 {date_txt} · 👥 {party_txt} · ⏱ {time_txt} · 🔔 {notify_txt}")
        
        with header_cols[1]:
            action_cols = st.columns(3)
            with action_cols[0]:
                if st.button("See details", key=f"btn_details_{idx}"): _toggle(f"show_details_{idx}")
            with action_cols[1]:
                if st.button("Edit", key=f"btn_edit_{idx}"): _toggle(f"show_edit_{idx}")
            with action_cols[2]:
                if st.button("🗑️ Delete", key=f"btn_delete_{idx}"): st.session_state[f"confirm_delete_{idx}"] = True

        if st.session_state.get(f"confirm_delete_{idx}", False):
            st.warning(f"Delete **{s.get('id','Unnamed')}**?", icon="⚠️")
            c1, c2 = st.columns(2)
            with c1:
                if st.button("✅ Confirm delete", type="primary", key=f"btn_confirm_delete_{idx}"):
                    searches_all.pop(idx)
                    config_data["searches"] = searches_all
                    st.session_state[f"confirm_delete_{idx}"] = False
                    save_config(config_data)
            with c2:
                if st.button("Cancel", key=f"btn_cancel_delete_{idx}"): st.session_state[f"confirm_delete_{idx}"] = False

        if st.session_state.get(f"show_details_{idx}", False):
            st.divider()
            st.write(f"**Venues**: {', '.join(s.get('venues', [])) or '—'}")
            st.write(f"**Dates**: {date_txt or '—'}")
            st.write(f"**Num Days**: {int(s.get('num_days', 1))}")
            st.write(f"**Email**: {s.get('email_to') or '—'}")

        if st.session_state.get(f"show_edit_{idx}", False):
            st.divider()
            dates_key = f"edit_dates_{idx}"
            if dates_key not in st.session_state: st.session_state[dates_key] = _get_dates_from_search(s)
            selected_dates = native_multi_date_selector(dates_key, "Dates", chips_per_row=4)

            with st.form(f"edit_form_{idx}"):
                e_plat_idx = 0 if platform == "sevenrooms" else 1
                e_platform = st.radio("Platform", ["SevenRooms", "OpenTable"], index=e_plat_idx, horizontal=True)
                e_name = st.text_input("Name", s.get("id", ""))
                e_venues = st.text_input("Venues (slugs)", ", ".join(s.get("venues", [])))
                e_party = st.number_input("Party", 1, 20, value=int(s.get("party_size", 2)))
                e_num_days = st.number_input("Num Days", 1, 7, value=int(s.get("num_days", 1)))
                e_time_slot = st.text_input("Exact time (HH:MM)", s.get("time_slot", ""))
                e_window_start = st.text_input("Window start (HH:MM)", s.get("window_start", "18:00"))
                e_window_end = st.text_input("Window end (HH:MM)", s.get("window_end", "22:00"))
                e_notify_label = st.selectbox("Notification method", NOTIFY_LABELS, index=NOTIFY_LABELS.index((s.get("notify") or "both").title()))
                e_notify = NOTIFY_MAP[e_notify_label]
                e_email = st.text_input("Email alert to (optional)", s.get("email_to", ""))

                bcols = st.columns(2)
                submitted = bcols[0].form_submit_button("💾 Save", type="primary")
                cancelled = bcols[1].form_submit_button("Cancel")

                if submitted:
                    dates_list = _normalize_iso_dates(selected_dates)
                    if not dates_list: dates_list = [dt.date.today().isoformat()]
                    config_data["searches"][idx].update({
                        "id": e_name.strip() or "Unnamed",
                        "platform": e_platform.lower(),
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
                        "salt": str(time.time()),
                    })
                    save_config(config_data)

                if cancelled:
                    st.session_state[f"show_edit_{idx}"] = False
                    st.rerun()

# =========================================================
# ADD NEW SEARCH
# =========================================================
st.subheader("➕ Add New Search")

add_cols = st.columns([0.5, 0.5])
with add_cols[0]:
    n_platform = st.radio("Platform", ["SevenRooms", "OpenTable"], horizontal=True).lower()
    default_venue = st.session_state.get("last_sr_slug" if n_platform == "sevenrooms" else "last_ot_slug", "")
    n_venue = st.text_input("Venue slug(s) (comma separated)", value=default_venue, key="new_venue")
    n_id = st.text_input("Search name", key="new_name")
    n_party = st.number_input("Party", 1, 20, 2, key="new_party")
    n_num_days = st.number_input("Num Days", 1, 7, 1, key="new_num_days")

with add_cols[1]:
    n_notify_label = st.selectbox("Notification method", NOTIFY_LABELS, index=2, key="new_notify_label")
    n_notify = NOTIFY_MAP[n_notify_label]
    n_email = st.text_input("Email alert to (optional)", key="new_email")
    any_time = st.checkbox("Any time in a window", value=True, key="new_any_time")

new_dates_key = "new_dates_list"
if new_dates_key not in st.session_state: st.session_state[new_dates_key] = [dt.date.today().isoformat()]
new_dates = native_multi_date_selector(new_dates_key, "Dates", chips_per_row=4)

if st.button("🔄 Load available times", key="load_times"):
    venue_first = (n_venue.split(",")[0].strip() if n_venue else "")
    date_for_times = (new_dates[0] if new_dates else dt.date.today().isoformat())
    if venue_first:
        with st.spinner("Fetching times..."):
            if n_platform == "sevenrooms":
                gbl = config_data.get("global", {})
                times_list = fetch_sevenrooms_times(venue_first, str(date_for_times), int(n_party), channel=gbl.get("channel", "SEVENROOMS_WIDGET"), lang=gbl.get("lang", "en"), num_days=int(n_num_days))
            else:
                times_list = fetch_opentable_times(venue_first, str(date_for_times), int(n_party))
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
    if not dates_list: dates_list = [dt.date.today().isoformat()]
    new_s = {
        "id": n_id.strip() or "Unnamed",
        "platform": n_platform,
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
        "salt": str(time.time()),
    }
    config_data.setdefault("searches", []).append(new_s)
    save_config(config_data)

# =========================================================
# ADVANCED
# =========================================================
with st.expander("⚙️ Advanced", expanded=False):
    _, state_data = _read_json_from_repo(STATE_FILE_PATH, {"notified": []})
    st.caption(f"Current dedupe cache entries: {len(state_data.get('notified', []) or [])}")
    if st.button("🔄 Reset state"): reset_state()

    st.divider()
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Quick Slug Finder (SevenRooms)")
        sr_text = st.text_input("Link/Slug", key="sr_url_adv")
        if st.button("Extract SevenRooms slug", key="btn_sr_slug_adv"):
            slug = get_sevenrooms_slug(sr_text)
            if slug:
                st.success(f"Slug: {slug}")
                st.session_state["last_sr_slug"] = slug
            else: st.error("Couldn’t find a slug.")
            
    with c2:
        st.subheader("Quick Slug Finder (OpenTable)")
        ot_text = st.text_input("Link/Slug", key="ot_url_adv")
        if st.button("Extract OpenTable slug", key="btn_ot_slug_adv"):
            slug = get_opentable_slug(ot_text)
            if slug:
                st.success(f"Slug: {slug}")
                st.session_state["last_ot_slug"] = slug
            else: st.error("Couldn’t find a slug.")
