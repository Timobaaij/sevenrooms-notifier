import streamlit as st
import json
import time
import datetime
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
def save_config(new_data):
    try:
        repo.update_file(contents.path, "Update via Web App", json.dumps(new_data, indent=2, sort_keys=True), contents.sha)
        st.toast("✅ Saved!", icon="💾")
        time.sleep(1)
        st.cache_data.clear()
        st.rerun()
    except Exception as e: st.error(f"Save Failed: {e}")

def get_ot_id(url):
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        matches = re.findall(r'"restaurantId":\s*"?(\d+)"?', r.text)
        valid = [m for m in matches if len(m) > 1 and m != "0"]
        return valid[0] if valid else None
    except: return None

# --- MAIN LAYOUT ---
col_main, col_tools = st.columns([2.5, 1.5], gap="large")

with col_main:
    st.title("🍽️ My Active Searches")
    searches = config_data.get("searches", [])
    
    for i, s in enumerate(searches):
        with st.expander(f"📍 {s.get('id')} ({s.get('platform').upper()})", expanded=True):
            st.write(f"**Date:** {s.get('date')} | **Party:** {s.get('party_size')} | **Venues:** {', '.join(s.get('venues', []))}")
            
            with st.form(f"edit_{i}"):
                e_name = st.text_input("Edit Name", s.get("id"))
                e_venues = st.text_input("Edit Venues (IDs/Slugs)", ", ".join(s.get("venues", [])))
                e_date = st.date_input("Edit Date", datetime.datetime.strptime(s.get("date"), "%Y-%m-%d").date())
                e_party = st.number_input("Edit Party", 1, 20, value=int(s.get("party_size", 2)))
                e_email = st.text_input("Edit Email", s.get("email_to", ""))
                
                if st.form_submit_button("💾 Save Changes"):
                    s.update({
                        "id": e_name,
                        "venues": [v.strip() for v in e_venues.split(",")],
                        "date": str(e_date),
                        "party_size": e_party,
                        "email_to": e_email,
                        "salt": str(time.time()) # Resets push history
                    })
                    save_config(config_data)
            
            if st.button("🗑️ Delete This Search", key=f"del_{i}"):
                searches.pop(i)
                save_config(config_data)

with col_tools:
    st.header("➕ Add New Search")
    with st.container(border=True):
        st.subheader("🕵️‍♂️ ID Finder")
        ot_url = st.text_input("Paste OpenTable Link")
        if st.button("Extract ID"):
            found = get_ot_id(ot_url)
            if found:
                st.success(f"ID Found: {found}")
                st.session_state['last_id'] = found
            else:
                st.error("ID not found.")

    with st.form("add_new"):
        plat = st.selectbox("Platform", ["sevenrooms", "opentable"])
        def_v = st.session_state.get('last_id', "") if plat == "opentable" else ""
        n_venues = st.text_input("Venue ID/Slug", value=def_v)
        n_id = st.text_input("Search Name")
        n_date = st.date_input("Date")
        n_party = st.number_input("Party", 1, 20, 2)
        n_email = st.text_input("Email Alert To")
        if st.form_submit_button("Launch Search"):
            new_s = {
                "id": n_id, "platform": plat, "venues": [v.strip() for v in n_venues.split(",")],
                "party_size": n_party, "date": str(n_date), "window_start": "18:00",
                "window_end": "22:00", "num_days": 1, "email_to": n_email, "salt": str(time.time())
            }
            config_data.setdefault("searches", []).append(new_s)
            save_config(config_data)
