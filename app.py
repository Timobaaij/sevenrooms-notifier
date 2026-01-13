import streamlit as st
import json
import time
import datetime
from github import Github

# --- CONFIGURATION ---
REPO_NAME = "YOUR_GITHUB_USERNAME/YOUR_REPO_NAME" # <--- CHECK THIS
CONFIG_FILE_PATH = "config.json"

st.set_page_config(page_title="Reservation Manager", page_icon="🍽️", layout="wide")

# --- CUSTOM CSS (Visual Polish) ---
st.markdown("""
<style>
    /* Bigger Buttons */
    .stButton button { width: 100%; font-weight: bold; }
    /* Card Styling */
    [data-testid="stVerticalBlockBorderWrapper"] { background-color: white; }
    /* Metrics */
    div[data-testid="stMetricValue"] { font-size: 1.1rem; }
    /* Hide Sidebar Close Button */
    [data-testid="stSidebarCollapseButton"] { display: none; }
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
# Right Sidebar for Adding / Main Area for Managing
col_main, col_add = st.columns([3, 1.2], gap="medium")

# ==========================================
# RIGHT COLUMN: ADD SEARCH & TOOLS
# ==========================================
with col_add:
    st.header("➕ Add Search")
    
    with st.container(border=True):
        platform = st.radio("Platform", ["SevenRooms", "OpenTable"], horizontal=True)
        is_ot = (platform == "OpenTable")
        
        with st.form("add_form"):
            st.write("### 1. Venue")
            n_id = st.text_input("Name (e.g. Birthday)", placeholder="My Dinner")
            
            if is_ot:
                st.info("ℹ️ **OpenTable ID:** See guide below.")
                n_venues = st.text_input("Restaurant ID", placeholder="e.g. 2500")
            else:
                st.info("ℹ️ **Slug:** Part after /reservations/")
                n_venues = st.text_input("Venue Slug", placeholder="e.g. sexyfishlondon")

            st.write("### 2. Time")
            n_date = st.date_input("Date")
            c1, c2 = st.columns(2)
            n_start = c1.time_input("Start", datetime.time(19,0))
            n_end = c2.time_input("End", datetime.time(21,0))
            
            st.write("### 3. Config")
            c3, c4 = st.columns(2)
            n_party = c3.number_input("Guests", 2)
            n_days = c4.number_input("Days", 1, help="Check X days from date")
            
            n_email = st.text_input("Email Alert", placeholder="me@gmail.com")
            n_img = st.text_input("Image URL (Optional)", placeholder="https://...")
            
            if st.form_submit_button("🚀 Add Search", type="primary"):
                # Basic Validation
                if not n_venues or not n_id:
                    st.error("Name and Venue are required.")
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
    st.divider()
    
    # --- ID FINDER TOOLS ---
    with st.expander("🔎 How to find IDs/Slugs", expanded=True):
        if is_ot:
            st.markdown("""
            **How to find an OpenTable ID:**
            1. Go to the restaurant page on OpenTable.
            2. Right-click page -> **View Page Source**.
            3. Press `Ctrl+F` (or Cmd+F).
            4. Search for: `"restaurantId"`.
            5. Copy the number next to it (e.g. `12345`).
            """)
            st.link_button("Go to OpenTable", "https://www.opentable.com")
        else:
            st.markdown("""
            **How to find a SevenRooms Slug:**
            1. Find the booking link.
            2. Look at the URL:
            `sevenrooms.com/reservations/`**`this-is-the-slug`**
            """)
            slug_q = st.text_input("Google Search Helper", placeholder="Gymkhana")
            if slug_q:
                st.link_button("Search Google", f"https://www.google.com/search?q={slug_q}+SevenRooms+reservations")

# ==========================================
# LEFT COLUMN: ACTIVE SEARCHES
# ==========================================
with col_main:
    st.title("🍽️ Dashboard")
    
    searches = config_data.get("searches", [])
    
    if not searches:
        st.info("No active searches. Add one on the right.")
    
    # Grid Layout
    grid = st.columns(2)
    
    for i, s in enumerate(searches):
        with grid[i % 2]:
            with st.container(border=True):
                # 1. Image
                img = s.get("image_url")
                if not img: img = "https://images.unsplash.com/photo-1559339352-11d035aa65de?auto=format&fit=crop&w=800&q=80"
                st.image(img, use_container_width=True, clamp=True)
                
                # 2. Header
                c_title, c_badge = st.columns([3, 1])
                c_title.subheader(s.get("id", "Unnamed"))
                
                plat = s.get("platform", "sevenrooms")
                if plat == "opentable":
                    c_badge.error("OpenTable", icon="🔴")
                else:
                    c_badge.info("7Rooms", icon="🔵")
                
                # 3. Quick Stats
                m1, m2, m3 = st.columns(3)
                m1.caption(f"📅 {s.get('date')}")
                m2.caption(f"👥 {s.get('party_size')} ppl")
                m3.caption(f"🕑 {s.get('window_start')}")

                # 4. EDIT SECTION (Restored!)
                with st.expander("✏️ **Edit / Details**"):
                    with st.form(key=f"edit_form_{i}"):
                        e_id = st.text_input("Name", s.get("id"))
                        e_venues = st.text_input("Venue ID/Slug", ", ".join(s.get("venues", [])))
                        
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
                                "salt": str(time.time()) # Resets notification history
                            })
                            config_data["searches"] = searches
                            save_config(config_data)

                # 5. Delete
                if st.button("🗑️ Delete", key=f"del_{i}"):
                    searches.pop(i)
                    config_data["searches"] = searches
                    save_config(config_data)
