import streamlit as st
import json
import time
import datetime
import requests
import re
from github import Github

# --- CONFIGURATION ---
# Hardcoded as requested
REPO_NAME = "timobaaij/sevenrooms-notifier" 
CONFIG_FILE_PATH = "config.json"

# --- PAGE SETUP ---
st.set_page_config(page_title="Reservation Manager", page_icon="🍽️", layout="wide")

# --- CUSTOM CSS ---
st.markdown("""
<style>
    /* Fixed visibility for tool boxes in dark/light mode */
    .smart-tool-box { 
        background-color: rgba(255, 255, 255, 0.05); 
        padding: 20px; 
        border-radius: 10px; 
        border: 2px solid #ff4b4b; 
        margin-bottom: 25px;
        color: inherit;
    }
    /* Button Styling */
    .stButton button { width: 100%; font-weight: 700; height: 3em; }
    /* Dashboard Cards */
    [data-testid="stVerticalBlockBorderWrapper"] { border: 1px solid #444; border-radius: 10px; padding: 15px; }
</style>
""", unsafe_allow_html=True)

# --- AUTH ---
try:
    token = st.secrets["GITHUB_TOKEN"]
    g = Github(token)
    repo = g.get_repo(REPO_NAME)
    contents = repo.get_contents(CONFIG_FILE_PATH)
    config_data = json.loads(contents.decoded_content.decode("utf-8"))
except Exception as e:
    st.error(f"❌ Connection Error: {e}")
    st.info("Check your GITHUB_TOKEN in Streamlit Secrets.")
    st.stop()

# --- SMART EXTRACTORS ---
def extract_opentable_id(url):
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        # Regex: finds numeric ID, ignores 0/null, requires >1 digit
        matches = re.findall(r'"restaurantId":\s*"?(\d+)"?', response.text)
        valid_ids = [m for m in matches if m != "0" and len(m) > 1]
        return valid_ids[0] if valid_ids else None
    except: return None

def extract_sevenrooms_slug(url):
    if "sevenrooms.com/reservations/" in url:
        return url.split("/reservations/")[-1].split("?")[0].split("/")[0]
    return None

# --- HELPERS ---
def save_config(new_data):
    try:
        repo.update_file(contents.path, "Update via Web App", json.dumps(new_data, indent=2, sort_keys=True), contents.sha)
        st.toast("✅ Changes Saved!", icon="💾")
        time.sleep(1)
        st.cache_data.clear()
        st.rerun()
    except Exception as e: st.error(f"Save Failed: {e}")

# --- MAIN LAYOUT ---
col_main, col_tools = st.columns([2.5, 1.5], gap="large")

# ==========================================
# LEFT: DASHBOARD (MANAGE SEARCHES)
# ==========================================
with col_main:
    st.title("🍽️ My Active Searches")
    searches = config_data.get("searches", [])
    
    if not searches:
        st.info("No active searches yet. Use the panel on the right! 👉")
    
    for i, s in enumerate(searches):
        with st.container(border=True):
            # Header Row
            head_1, head_2, head_3 = st.columns([3, 1, 1])
            head_1.subheader(f"{s.get('id', 'Unnamed Search')}")
            
            plat = s.get("platform", "sevenrooms")
            if plat == "opentable": head_2.error("OpenTable")
            else: head_2.info("7Rooms")
            
            # Persistent Edit Checkbox
            show_edit = head_3.checkbox("✏️ Edit", key=f"edit_toggle_{i}")
            
            # Quick View Info
            st.write(f"📅 **Date:** {s.get('date')} | 👥 **Party:** {s.get('party_size')} | 📍 **Venues:** {', '.join(s.get('venues', []))}")
            
            if show_edit:
                st.divider()
                with st.form(key=f"edit_form_{i}"):
                    fe1, fe2 = st.columns(2)
                    e_id = fe1.text_input("Name", s.get("id"))
                    e_venues = fe2.text_input("ID/Slug (comma separated)", ", ".join(s.get("venues", [])))
                    
                    fe3, fe4, fe5 = st.columns(3)
                    e_date = fe3.date_input("Date", datetime.datetime.strptime(s.get("date"), "%Y-%m-%d").date())
                    e_party = fe4.number_input("Guests", 1, 20, value=int(s.get("party_size", 2)))
                    e_days = fe5.number_input("Days", 1, 14, value=int(s.get("num_days", 1)))
                    
                    e_email = st.text_input("Email Alert To", s.get("email_to", ""))
                    
                    if st.form_submit_button("💾 Save Changes", type="primary"):
                        s.update({
                            "id": e_id, 
                            "venues": [v.strip() for v in e_venues.split(",") if v.strip()],
                            "date": str(e_date),
                            "party_size": e_party,
                            "num_days": e_days,
                            "email_to": e_email,
                            "salt": str(time.time())
                        })
                        save_config(config_data)
                
                # Delete logic inside the edit view
                if st.button("🗑️ Permanently Delete", key=f"del_btn_{i}"):
                    searches.pop(i)
                    config_data["searches"] = searches
                    save_config(config_data)

# ==========================================
# RIGHT: TOOLS & ADD SEARCH
# ==========================================
with col_tools:
    st.header("➕ Create New Search")
    
    # --- SMART FINDER ---
    st.markdown('<div class="smart-tool-box">', unsafe_allow_html=True)
    st.subheader("🕵️‍♂️ ID / Slug Finder")
    platform = st.radio("Choose Platform", ["SevenRooms", "OpenTable"], horizontal=True)
    link_input = st.text_input("Paste Restaurant URL", placeholder="https://...")
    
    if st.button("Extract Identifier"):
        if not link_input:
            st.warning("Please paste a link first!")
        else:
            if platform == "OpenTable":
                found = extract_opentable_id(link_input)
            else:
                found = extract_sevenrooms_slug(link_input)
            
            if found:
                st.success(f"**Found: `{found}`**")
                st.session_state['last_found'] = found
            else:
                st.error("Could not find a valid ID/Slug. Check the link.")
    st.markdown('</div>', unsafe_allow_html=True)

    # --- ADD FORM ---
    with st.container(border=True):
        with st.form("add_new_final"):
            prefill = st.session_state.get('last_found', "")
            n_venues = st.text_input("Venue ID/Slug", value=prefill)
            n_id = st.text_input("Search Name (e.g. Birthday)")
            n_date = st.date_input("Start Date")
            
            c1, c2 = st.columns(2)
            n_party = c1.number_input("Guests", 1, 20, value=2)
            n_days = c2.number_input("Days to check", 1, 14, value=1)
            
            n_email = st.text_input("Email Alert To")
            
            if st.form_submit_button("🚀 Launch Search", type="primary"):
                if not n_venues or not n_id:
                    st.error("Name and Venue are required!")
                else:
                    new_search = {
                        "id": n_id,
                        "platform": platform.lower(),
                        "venues": [v.strip() for v in n_venues.split(",") if v.strip()],
                        "party_size": n_party,
                        "date": str(n_date),
                        "window_start": "18:00",
                        "window_end": "22:00",
                        "num_days": n_days,
                        "email_to": n_email,
                        "salt": str(time.time())
                    }
                    config_data.setdefault("searches", []).append(new_search)
                    if 'last_found' in st.session_state: del st.session_state['last_found']
                    save_config(config_data)
