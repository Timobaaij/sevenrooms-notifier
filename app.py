import streamlit as st
import json
import time
import datetime
import requests
import re
from github import Github

# --- CONFIGURATION ---
REPO_NAME = "YOUR_GITHUB_USERNAME/YOUR_REPO_NAME" 
CONFIG_FILE_PATH = "config.json"

# --- PAGE SETUP ---
st.set_page_config(page_title="Reservation Manager", page_icon="🍽️", layout="wide")

# --- CUSTOM CSS (High Visibility Fix) ---
st.markdown("""
<style>
    .smart-tool-box { 
        background-color: rgba(255, 255, 255, 0.05); 
        padding: 20px; 
        border-radius: 10px; 
        border: 2px solid #ff4b4b; 
        margin-bottom: 25px;
    }
    .stButton button { width: 100%; font-weight: 600; }
    div[data-testid="stMetricValue"] { font-size: 1.1rem; }
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
    st.stop()

# --- SMART EXTRACTORS ---

def extract_opentable_id(url):
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code != 200: return None
        
        # Regex to find "restaurantId":12345 or "restaurantId":"12345"
        matches = re.findall(r'"restaurantId":\s*"?(\d+)"?', response.text)
        
        # RULE: Must be a number, not "0", and MUST HAVE MORE THAN 1 DIGIT
        valid_ids = [m for m in matches if m != "0" and len(m) > 1]
        
        if valid_ids:
            return valid_ids[0] # Return the first valid hit
    except: pass
    return None

def extract_sevenrooms_slug(url):
    # SevenRooms slugs are usually at the end of the URL
    # e.g., sevenrooms.com/reservations/restaurant-name
    if "sevenrooms.com/reservations/" in url:
        slug = url.split("/reservations/")[-1].split("?")[0].split("/")[0]
        return slug
    return None

# --- HELPERS ---
def save_config(new_data):
    try:
        repo.update_file(contents.path, "Update via Web App", json.dumps(new_data, indent=2, sort_keys=True), contents.sha)
        st.toast("✅ Saved!", icon="💾")
        time.sleep(1)
        st.cache_data.clear()
        st.rerun()
    except Exception as e: st.error(f"Save Failed: {e}")

# --- MAIN LAYOUT ---
col_main, col_tools = st.columns([3, 1.3], gap="large")

# ==========================================
# LEFT: DASHBOARD
# ==========================================
with col_main:
    st.title("🍽️ Active Searches")
    searches = config_data.get("searches", [])
    
    if not searches: st.info("No active searches. Use the tool on the right!")
    else:
        grid = st.columns(2)
        for i, s in enumerate(searches):
            with grid[i % 2]:
                with st.container(border=True):
                    c_title, c_badge = st.columns([3, 1])
                    c_title.subheader(s.get("id", "Unnamed"))
                    
                    plat = s.get("platform", "sevenrooms")
                    if plat == "opentable":
                        c_badge.error("OpenTable", icon="🔴")
                    else:
                        c_badge.info("7Rooms", icon="🔵")
                    
                    st.write(f"📅 **{s.get('date')}** | 👥 **{s.get('party_size')} ppl**")
                    
                    with st.expander("Edit / Delete"):
                        with st.form(key=f"edit_{i}"):
                            e_id = st.text_input("Name", s.get("id"))
                            e_venues = st.text_input("ID/Slug", ", ".join(s.get("venues", [])))
                            e_email = st.text_input("Email", s.get("email_to", ""))
                            if st.form_submit_button("Save Changes"):
                                s.update({"id": e_id, "venues": [v.strip() for v in e_venues.split(",")], "email_to": e_email, "salt": str(time.time())})
                                save_config(config_data)
                        if st.button("🗑️ Delete", key=f"del_{i}"):
                            searches.pop(i)
                            save_config(config_data)

# ==========================================
# RIGHT: SMART TOOLS
# ==========================================
with col_tools:
    st.header("➕ Add Search")
    platform = st.radio("Choose Platform", ["SevenRooms", "OpenTable"], horizontal=True)

    # --- THE SMART FINDER ---
    st.markdown('<div class="smart-tool-box">', unsafe_allow_html=True)
    st.subheader(f"🕵️‍♂️ {platform} Smart Finder")
    
    if platform == "OpenTable":
        st.write("Paste the restaurant URL. We'll find the ID (ignoring 0 or null).")
        input_link = st.text_input("OpenTable Link", placeholder="https://www.opentable.com/r/...")
        if st.button("Extract Real ID"):
            found = extract_opentable_id(input_link)
            if found:
                st.success(f"**Found Valid ID: `{found}`**")
                st.session_state['last_found'] = found
            else:
                st.error("No valid multi-digit ID found. Use 'View Source' as a backup.")
    else:
        st.write("Paste the booking link. We'll pull the slug automatically.")
        input_link = st.text_input("SevenRooms Link", placeholder="https://www.sevenrooms.com/reservations/...")
        if st.button("Extract Slug"):
            found = extract_sevenrooms_slug(input_link)
            if found:
                st.success(f"**Found Slug: `{found}`**")
                st.session_state['last_found'] = found
            else:
                st.error("Could not find a slug in that link.")
    st.markdown('</div>', unsafe_allow_html=True)

    # --- ADD FORM ---
    with st.container(border=True):
        with st.form("add_final"):
            # Automatically pull the ID/Slug if the finder above worked
            prefill = st.session_state.get('last_found', "")
            
            n_venues = st.text_input("Venue ID/Slug", value=prefill)
            n_id = st.text_input("Search Name (e.g. Birthday)")
            n_date = st.date_input("Date")
            
            c1, c2 = st.columns(2)
            n_party = c1.number_input("Guests", 1, value=2)
            n_days = c2.number_input("Days", 1, value=1)
            
            n_email = st.text_input("Email Alert To")
            
            if st.form_submit_button("🚀 Launch Search", type="primary"):
                if not n_venues or not n_id:
                    st.error("Please provide a Name and a Venue ID/Slug.")
                else:
                    searches = config_data.get("searches", [])
                    searches.append({
                        "id": n_id,
                        "platform": platform.lower(),
                        "venues": [v.strip() for v in n_venues.split(",") if v.strip()],
                        "party_size": n_party,
                        "date": str(n_date),
                        "window_start": "18:00", # Defaults
                        "window_end": "21:30",
                        "num_days": n_days,
                        "email_to": n_email,
                        "salt": str(time.time())
                    })
                    config_data["searches"] = searches
                    # Clear the finder memory after successful add
                    if 'last_found' in st.session_state: del st.session_state['last_found']
                    save_config(config_data)
