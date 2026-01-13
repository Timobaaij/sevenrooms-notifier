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
    
    if not searches:
        st.info("No active searches yet.")

    for i, s in enumerate(searches):
        # FIX: Handle cases where 'platform' might be missing in old config data
        p_raw = s.get('platform', 'sevenrooms')
        p_label = str(p_raw).upper()
        
        with st.container(border=True):
            # Header with Image
            img_url = s.get("image_url")
            if img_url:
                st.image(img_url, use_container_width=True)
            
            st.subheader(f"📍 {s.get('id', 'Unnamed')} ({p_label})")
            st.write(f"📅 **Date:** {s.get('date')} | 👥 **Party:** {s.get('party_size')}")
            st.write(f"🔗 **Venues:** {', '.join(s.get('venues', []))}")
            
            # Action Buttons
            col1, col2 = st.columns(2)
            with col1:
                show_edit = st.checkbox("✏️ Edit Search", key=f"edit_check_{i}")
            with col2:
                if st.button("🗑️ Delete Search", key=f"del_btn_{i}"):
                    searches.pop(i)
                    config_data["searches"] = searches
                    save_config(config_data)

            if show_edit:
                st.divider()
                with st.form(f"edit_form_{i}"):
                    e_name = st.text_input("Name", s.get("id"))
                    e_venues = st.text_input("Venues (IDs/Slugs)", ", ".join(s.get("venues", [])))
                    e_date = st.date_input("Date", datetime.datetime.strptime(s.get("date"), "%Y-%m-%d").date())
                    e_party = st.number_input("Party", 1, 20, value=int(s.get("party_size", 2)))
                    e_email = st.text_input("Email Alert To", s.get("email_to", ""))
                    e_img = st.text_input("Image URL", s.get("image_url", ""))
                    
                    if st.form_submit_button("💾 Save Changes"):
                        searches[i].update({
                            "id": e_name,
                            "venues": [v.strip() for v in e_venues.split(",")],
                            "date": str(e_date),
                            "party_size": e_party,
                            "email_to": e_email,
                            "image_url": e_img,
                            "salt": str(time.time())
                        })
                        config_data["searches"] = searches
                        save_config(config_data)

with col_tools:
    st.header("➕ Add New Search")
    
    # Tool: ID Finder
    with st.expander("🕵️‍♀️ OpenTable ID Finder", expanded=False):
        ot_url = st.text_input("Paste OpenTable Link")
        if st.button("Extract ID"):
            found = get_ot_id(ot_url)
            if found:
                st.success(f"ID Found: {found}")
                st.session_state['last_id'] = found
            else:
                st.error("ID not found in page source.")

    # Form: Add Search
    with st.form("add_new"):
        plat = st.selectbox("Platform", ["sevenrooms", "opentable"])
        def_v = st.session_state.get('last_id', "") if plat == "opentable" else ""
        n_venues = st.text_input("Venue ID/Slug", value=def_v)
        n_id = st.text_input("Search Name")
        n_date = st.date_input("Date")
        n_party = st.number_input("Party", 1, 20, 2)
        n_email = st.text_input("Email Alert To")
        n_img = st.text_input("Image URL (Optional)")
        
        if st.form_submit_button("🚀 Launch Search", type="primary"):
            new_s = {
                "id": n_id, 
                "platform": plat, 
                "venues": [v.strip() for v in n_venues.split(",")],
                "party_size": n_party, 
                "date": str(n_date), 
                "window_start": "18:00",
                "window_end": "22:00", 
                "num_days": 1, 
                "email_to": n_email, 
                "image_url": n_img,
                "salt": str(time.time())
            }
            config_data.setdefault("searches", []).append(new_s)
            save_config(config_data)
