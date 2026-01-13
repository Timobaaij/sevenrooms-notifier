import streamlit as st
import json
import time
import datetime
from github import Github

# --- CONFIGURATION ---
# CRITICAL: UPDATE THIS TO YOUR REPO NAME (e.g., "username/repo")
REPO_NAME = "Timobaaij/sevenrooms-notifier" 
CONFIG_FILE_PATH = "config.json"

# --- PAGE SETUP ---
st.set_page_config(
    page_title="Reservation Manager", 
    page_icon="🍽️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- CUSTOM CSS (Fixed for Dark/Light Mode Visibility) ---
st.markdown("""
<style>
    /* Fixed Help Box: Transparent background with visible border */
    .help-box { 
        background-color: transparent !important; 
        padding: 20px; 
        border-radius: 10px; 
        border: 2px solid #555;
        margin-bottom: 25px;
        color: inherit;
    }
    /* Sidebar lock & Width */
    [data-testid="stSidebar"] { min-width: 450px !important; }
    [data-testid="stSidebarCollapseButton"] { display: none; }
    [data-testid="stSidebarCollapsedControl"] { display: none; }
    
    /* UI Buttons */
    .stButton button { width: 100%; font-weight: 600; }
    div[data-testid="stMetricValue"] { font-size: 1.1rem; }
</style>
""", unsafe_allow_html=True)

# --- GITHUB AUTHENTICATION ---
try:
    token = st.secrets["GITHUB_TOKEN"]
    g = Github(token)
    repo = g.get_repo(REPO_NAME)
    contents = repo.get_contents(CONFIG_FILE_PATH)
    config_data = json.loads(contents.decoded_content.decode("utf-8"))
except Exception as e:
    st.error(f"❌ Connection Error: {e}")
    st.stop()

# --- HELPER FUNCTIONS ---
def save_config(new_data):
    try:
        repo.update_file(
            path=contents.path,
            message="Update via Web App",
            content=json.dumps(new_data, indent=2, sort_keys=True),
            sha=contents.sha
        )
        st.toast("✅ Saved to GitHub!", icon="💾")
        time.sleep(1)
        st.cache_data.clear()
        st.rerun()
    except Exception as e: st.error(f"Save Failed: {e}")

# --- SIDEBAR: ADD SEARCH & FINDER TOOLS ---
with st.sidebar:
    st.title("➕ Add Search")
    
    # 1. Platform Toggle
    platform = st.radio("Platform", ["SevenRooms", "OpenTable"], horizontal=True)
    is_ot = (platform == "OpenTable")
    
    with st.form("add_form"):
        st.write("### 1. Venue")
        n_id = st.text_input("Friendly Name", placeholder="e.g. Anniversary Dinner")
        
        if is_ot:
            n_venues = st.text_input("Restaurant ID", placeholder="e.g. 109283")
        else:
            n_venues = st.text_input("Venue Slug", placeholder="e.g. gymkhanalondon")

        st.write("### 2. Time & Date")
        n_date = st.date_input("Date")
        c1, c2 = st.columns(2)
        n_start = c1.time_input("Start", datetime.time(19,0))
        n_end = c2.time_input("End", datetime.time(21,0))
        
        st.write("### 3. Settings")
        c3, c4 = st.columns(2)
        n_party = c3.number_input("Guests", 2)
        n_days = c4.number_input("Duration (Days)", 1, help="Checks the date + X following days")
        
        n_email = st.text_input("Email Alert To", placeholder="me@gmail.com")
        
        if st.form_submit_button("🚀 Add Search", type="primary"):
            if not n_venues or not n_id:
                st.error("Name and Venue ID/Slug are required.")
            else:
                searches = config_data.get("searches", [])
                searches.append({
                    "id": n_id,
                    "platform": "opentable" if is_ot else "sevenrooms",
                    "venues": [v.strip() for v in n_venues.split(",") if v.strip()],
                    "party_size": n_party,
                    "date": str(n_date),
                    "window_start": n_start.strftime("%H:%M"),
                    "window_end": n_end.strftime("%H:%M"),
                    "num_days": n_days,
                    "email_to": n_email,
                    "salt": str(time.time())
                })
                config_data["searches"] = searches
                save_config(config_data)

    st.divider()
    
    # 2. FINDER TOOLS (Fixed Visibility)
    st.subheader("🕵️‍♀️ Finder Tools")
    if is_ot:
        st.markdown(f"""
        <div class="help-box">
        <b>Finding an OpenTable ID:</b><br>
        1. Open the restaurant's OpenTable page.<br>
        2. Right-click blank space → <b>View Page Source</b>.<br>
        3. Search (Ctrl+F) for: <code>"restaurantId"</code>.<br>
        4. Enter the number next to it (e.g. <code>12345</code>).
        </div>
        """, unsafe_allow_html=True)
    else:
        st.markdown(f"""
        <div class="help-box">
        <b>Finding a SevenRooms Slug:</b><br>
        1. Find the restaurant's SevenRooms booking URL.<br>
        2. Copy the name AFTER <code>/reservations/</code>.<br><br>
        <i>Example: sevenrooms.com/reservations/<b>gymkhanalondon</b></i>
        </div>
        """, unsafe_allow_html=True)

# --- MAIN DASHBOARD ---
st.title("🍽️ Active Searches")
searches = config_data.get("searches", [])

if not searches:
    st.info("No active searches. Use the panel on the left to add one.")

grid = st.columns(2)
for i, s in enumerate(searches):
    with grid[i % 2]:
        with st.container(border=True):
            # Header
            c_title, c_badge = st.columns([3, 1])
            c_title.subheader(s.get("id", "Unnamed"))
            
            plat = s.get("platform", "sevenrooms")
            if plat == "opentable":
                c_badge.error("OpenTable", icon="🔴")
            else:
                c_badge.info("7Rooms", icon="🔵")
            
            # Quick Stats
            st.write(f"📅 **{s.get('date')}** | 👥 **{s.get('party_size')} ppl**")
            st.write(f"🕑 **{s.get('window_start')} - {s.get('window_end')}**")

            # Edit & Delete
            with st.expander("⚙️ Details / Edit"):
                with st.form(key=f"edit_form_{i}"):
                    e_id = st.text_input("Name", s.get("id"))
                    e_venues = st.text_input("ID / Slug", ", ".join(s.get("venues", [])))
                    
                    ec1, ec2 = st.columns(2)
                    e_date = ec1.date_input("Date", datetime.datetime.strptime(s.get("date"), "%Y-%m-%d").date())
                    e_days = ec2.number_input("Days", 1, value=int(s.get("num_days", 1)))
                    
                    ec3, ec4 = st.columns(2)
                    e_start = ec3.time_input("Start", datetime.datetime.strptime(s.get("window_start"), "%H:%M").time())
                    e_end = ec4.time_input("End", datetime.datetime.strptime(s.get("window_end"), "%H:%M").time())
                    
                    e_email = st.text_input("Email Alert", s.get("email_to", ""))
                    
                    if st.form_submit_button("💾 Save Changes"):
                        searches[i].update({
                            "id": e_id,
                            "venues": [v.strip() for v in e_venues.split(",") if v.strip()],
                            "date": str(e_date),
                            "window_start": e_start.strftime("%H:%M"),
                            "window_end": e_end.strftime("%H:%M"),
                            "num_days": e_days,
                            "email_to": e_email,
                            "salt": str(time.time())
                        })
                        config_data["searches"] = searches
                        save_config(config_data)

            if st.button("🗑️ Delete Search", key=f"del_{i}"):
                searches.pop(i)
                config_data["searches"] = searches
                save_config(config_data)
