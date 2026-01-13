import streamlit as st
import json
import time
import datetime
from github import Github

# --- CONFIGURATION ---
REPO_NAME = "Timobaaij/sevenrooms-notifier" # <--- CHECK THIS
CONFIG_FILE_PATH = "config.json"

st.set_page_config(page_title="Reservation Manager", page_icon="🍽️", layout="wide")

# --- CUSTOM CSS ---
st.markdown("""
<style>
    .stButton button { width: 100%; font-weight: 600; }
    div[data-testid="stMetricValue"] { font-size: 1.1rem; }
    [data-testid="stSidebarCollapseButton"] { display: none; }
    .help-box { 
        background-color: #f0f2f6; 
        padding: 15px; 
        border-radius: 8px; 
        border: 1px solid #d6d6d6;
        margin-bottom: 20px;
    }
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

# --- HELPERS ---
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

# --- LAYOUT ---
col_main, col_add = st.columns([3, 1.2], gap="medium")

# ==========================================
# RIGHT COLUMN: ADD SEARCH & HELPERS
# ==========================================
with col_add:
    st.header("➕ Add Search")
    
    with st.container(border=True):
        platform = st.radio("Platform", ["SevenRooms", "OpenTable"], horizontal=True)
        is_ot = (platform == "OpenTable")
        
        with st.form("add_form"):
            st.write("### 1. Venue Details")
            n_id = st.text_input("Friendly Name", placeholder="e.g. Anniversary Dinner")
            
            if is_ot:
                n_venues = st.text_input("Restaurant ID", placeholder="e.g. 109283")
                st.caption("See 'How to find ID' below")
            else:
                n_venues = st.text_input("Venue Slug", placeholder="e.g. gymkhanalondon")
                st.caption("See 'How to find Slug' below")

            st.write("### 2. Time & Date")
            n_date = st.date_input("Date")
            c1, c2 = st.columns(2)
            n_start = c1.time_input("Start", datetime.time(19,0))
            n_end = c2.time_input("End", datetime.time(21,0))
            
            st.write("### 3. Settings")
            c3, c4 = st.columns(2)
            n_party = c3.number_input("Guests", 2)
            n_days = c4.number_input("Days", 1, help="Check X days starting from the date")
            
            n_email = st.text_input("Email Alert", placeholder="me@gmail.com")
            n_img = st.text_input("Image URL (Optional)")
            
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
                        "image_url": n_img,
                        "salt": str(time.time())
                    })
                    config_data["searches"] = searches
                    save_config(config_data)

    st.write("")
    st.markdown("### 🕵️‍♀️ Finder Tools")
    
    # --- MANUAL HELPERS ---
    if not is_ot:
        # SEVENROOMS HELPER
        st.markdown("""
        <div class="help-box">
        <b>Finding a SevenRooms Slug:</b><br>
        1. Click the button below to find the booking page.<br>
        2. Look at the URL bar.<br>
        3. Copy the text AFTER <code>/reservations/</code>.<br>
        <br>
        <i>Example: sevenrooms.com/reservations/<b>gymkhanalondon</b></i>
        </div>
        """, unsafe_allow_html=True)
        
        sr_name = st.text_input("Restaurant Name", placeholder="e.g. Gymkhana")
        if sr_name:
            url = f"https://www.google.com/search?q={sr_name.replace(' ', '+')}+sevenrooms+reservations"
            st.link_button(f"🔍 Search for '{sr_name}'", url)
            
    else:
        # OPENTABLE HELPER
        st.markdown("""
        <div class="help-box">
        <b>Finding an OpenTable ID:</b><br>
        1. Go to the restaurant's OpenTable page.<br>
        2. Right-click blank space -> <b>View Page Source</b>.<br>
        3. Search (Ctrl+F) for: <code>"restaurantId"</code>.<br>
        4. Copy the number next to it (e.g. <code>12345</code>).
        </div>
        """, unsafe_allow_html=True)
        st.link_button("Go to OpenTable.com", "https://www.opentable.com")

# ==========================================
# LEFT COLUMN: DASHBOARD
# ==========================================
with col_main:
    st.title("🍽️ Dashboard")
    
    searches = config_data.get("searches", [])
    
    if not searches:
        st.info("No active searches. Use the form on the right.")
    
    grid = st.columns(2)
    
    for i, s in enumerate(searches):
        with grid[i % 2]:
            with st.container(border=True):
                # IMAGE
                img = s.get("image_url")
                if not img: img = "https://images.unsplash.com/photo-1517248135467-4c7edcad34c4?auto=format&fit=crop&w=800&q=80"
                st.image(img, use_container_width=True, clamp=True)
                
                # HEADER
                c_title, c_badge = st.columns([3, 1])
                c_title.subheader(s.get("id", "Unnamed"))
                
                plat = s.get("platform", "sevenrooms")
                if plat == "opentable":
                    c_badge.error("OpenTable", icon="🔴")
                else:
                    c_badge.info("7Rooms", icon="🔵")
                
                # STATS
                m1, m2, m3 = st.columns(3)
                m1.caption(f"📅 {s.get('date')}")
                m2.caption(f"👥 {s.get('party_size')} ppl")
                m3.caption(f"🕑 {s.get('window_start')}")

                # EDIT / DELETE
                with st.expander("⚙️ Edit / Delete"):
                    with st.form(key=f"edit_form_{i}"):
                        e_id = st.text_input("Name", s.get("id"))
                        e_venues = st.text_input("ID / Slug", ", ".join(s.get("venues", [])))
                        
                        ec1, ec2 = st.columns(2)
                        e_date = ec1.date_input("Date", datetime.datetime.strptime(s.get("date"), "%Y-%m-%d").date())
                        e_days = ec2.number_input("Days", 1, value=int(s.get("num_days", 1)))
                        
                        ec3, ec4 = st.columns(2)
                        e_start = ec3.time_input("Start", datetime.datetime.strptime(s.get("window_start"), "%H:%M").time())
                        e_end = ec4.time_input("End", datetime.datetime.strptime(s.get("window_end"), "%H:%M").time())
                        
                        e_email = st.text_input("Email", s.get("email_to", ""))
                        e_img = st.text_input("Image", s.get("image_url", ""))
                        
                        if st.form_submit_button("💾 Save Changes"):
                            searches[i].update({
                                "id": e_id,
                                "venues": [v.strip() for v in e_venues.split(",") if v.strip()],
                                "date": str(e_date),
                                "window_start": e_start.strftime("%H:%M"),
                                "window_end": e_end.strftime("%H:%M"),
                                "num_days": e_days,
                                "email_to": e_email,
                                "image_url": e_img,
                                "salt": str(time.time())
                            })
                            config_data["searches"] = searches
                            save_config(config_data)

                    if st.button("🗑️ Delete Search", key=f"del_{i}"):
                        searches.pop(i)
                        config_data["searches"] = searches
                        save_config(config_data)
