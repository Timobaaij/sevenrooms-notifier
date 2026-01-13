import streamlit as st
import json
import time
import datetime
from github import Github

# --- CONFIGURATION ---
# CRITICAL: UPDATE THIS TO YOUR REPO
REPO_NAME = "Timobaaij/sevenrooms-notifier" 
CONFIG_FILE_PATH = "config.json"

# --- PAGE SETUP ---
st.set_page_config(
    page_title="SevenRooms Manager", 
    page_icon="🍽️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- CUSTOM CSS ---
st.markdown("""
<style>
    /* 1. WIDEN THE SIDEBAR */
    [data-testid="stSidebar"] {
        min-width: 500px !important;
        max-width: 800px !important;
    }

    /* 2. Hide the close button (the 'X' or arrow) in the sidebar */
    [data-testid="stSidebarCollapseButton"] {
        display: none;
    }
    
    /* 3. Hide the sidebar toggle in the top left */
    [data-testid="stSidebarCollapsedControl"] {
        display: none;
    }
    
    /* 4. Make buttons full width for better UI */
    .stButton button {
        width: 100%;
    }
    
    /* 5. Make metrics text bigger */
    div[data-testid="stMetricValue"] {
        font-size: 1.2rem;
    }
</style>
""", unsafe_allow_html=True)

# --- AUTHENTICATION ---
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
            message="Update config via Web App",
            content=json.dumps(new_data, indent=2, sort_keys=True),
            sha=contents.sha
        )
        st.toast("✅ Saved to GitHub!", icon="💾")
        time.sleep(1)
        st.cache_data.clear()
        st.rerun()
    except Exception as e:
        st.error(f"Save Failed: {e}")

def parse_date(d):
    try: return datetime.datetime.strptime(str(d), "%Y-%m-%d").date()
    except: return datetime.date.today()

def parse_time(t):
    try: return datetime.datetime.strptime(str(t), "%H:%M").time()
    except: return datetime.time(19, 0)

# --- SIDEBAR: TOOLS ---
with st.sidebar:
    st.title("🛠️ Tools")
    
    # 1. SLUG HUNTER
    st.divider()
    st.subheader("🕵️‍♀️ Slug Hunter")
    st.info("SevenRooms 'slugs' are the last part of the booking URL. e.g. `sevenrooms.com/reservations/sexyfishlondon` -> `sexyfishlondon`")
    
    hunt_name = st.text_input("Restaurant Name + City", placeholder="e.g. Gymkhana London")
    if hunt_name:
        query = f"{hunt_name} SevenRooms reservations".replace(" ", "+")
        url = f"https://www.google.com/search?q={query}"
        st.link_button(f"🔍 Find '{hunt_name}' Slug", url)
    
    # 2. ADD NEW SEARCH
    st.divider()
    st.subheader("➕ Add New Search")
    with st.form("add_new_form"):
        n_id = st.text_input("Search Name", placeholder="Birthday Dinner")
        n_venues = st.text_input("Venue Slugs", placeholder="lidios, som-saa")
        n_date = st.date_input("Date")
        
        c1, c2 = st.columns(2)
        with c1:
            n_start = st.time_input("Start", datetime.time(18,0))
            n_party = st.number_input("Party", 1, value=2)
        with c2:
            n_end = st.time_input("End", datetime.time(21,0))
            n_days = st.number_input("Days", 1, value=1)
            
        n_email = st.text_input("Email Alert (Optional)", placeholder="me@gmail.com")
        n_img = st.text_input("Image URL (Optional)", placeholder="https://...")
        
        if st.form_submit_button("🚀 Launch Search", type="primary"):
            searches = config_data.get("searches", [])
            searches.append({
                "id": n_id,
                "venues": [v.strip() for v in n_venues.split(",") if v.strip()],
                "party_size": n_party,
                "date": str(n_date),
                "window_start": n_start.strftime("%H:%M"),
                "window_end": n_end.strftime("%H:%M"),
                "email_to": n_email.strip(),
                "num_days": n_days,
                "image_url": n_img.strip(),
                "salt": str(time.time())
            })
            config_data["searches"] = searches
            save_config(config_data)

# --- MAIN DASHBOARD ---
st.title("🍽️ Active Searches")

searches = config_data.get("searches", [])

if not searches:
    st.markdown("""
        <div style="text-align: center; padding: 50px; background: #f0f2f6; border-radius: 10px;">
            <h3>No active searches</h3>
            <p>Use the sidebar 👈 to add your first restaurant watch.</p>
        </div>
    """, unsafe_allow_html=True)
else:
    # GRID LAYOUT (3 Columns)
    cols = st.columns(3)
    
    for i, s in enumerate(searches):
        # Calculate which column to use
        col = cols[i % 3]
        
        with col:
            # CARD CONTAINER
            with st.container(border=True):
                # 1. IMAGE
                img_link = s.get("image_url")
                if not img_link:
                    img_link = "https://images.unsplash.com/photo-1517248135467-4c7edcad34c4?q=80&w=1000&auto=format&fit=crop"
                
                st.image(img_link, use_container_width=True, clamp=True)
                
                # 2. HEADER
                st.subheader(s.get("id", "Unnamed"))
                
                # 3. METRICS
                m1, m2 = st.columns(2)
                m1.metric("Date", s.get("date"))
                m2.metric("Venues", len(s.get("venues", [])))
                
                # 4. DETAILS (Collapsed)
                with st.expander("Show Details & Edit"):
                    with st.form(key=f"edit_{i}"):
                        e_id = st.text_input("Name", s.get("id"))
                        e_venues = st.text_input("Venues", ", ".join(s.get("venues", [])))
                        e_date = st.date_input("Date", parse_date(s.get("date")))
                        
                        ec1, ec2 = st.columns(2)
                        with ec1:
                            e_start = st.time_input("Start", parse_time(s.get("window_start")))
                            e_party = st.number_input("Party", 1, value=int(s.get("party_size", 2)))
                        with ec2:
                            e_end = st.time_input("End", parse_time(s.get("window_end")))
                            e_days = st.number_input("Days", 1, value=int(s.get("num_days", 1)))
                        
                        e_email = st.text_input("Email", s.get("email_to", ""))
                        e_img = st.text_input("Image URL", s.get("image_url", ""))
                        
                        if st.form_submit_button("💾 Save Changes"):
                            searches[i] = {
                                "id": e_id,
                                "venues": [v.strip() for v in e_venues.split(",") if v.strip()],
                                "party_size": e_party,
                                "date": str(e_date),
                                "window_start": e_start.strftime("%H:%M"),
                                "window_end": e_end.strftime("%H:%M"),
                                "email_to": e_email.strip(),
                                "num_days": e_days,
                                "image_url": e_img.strip(),
                                "salt": str(time.time()) # FORCE RESET
                            }
                            config_data["searches"] = searches
                            save_config(config_data)

                # 5. DELETE BUTTON
                if st.button("🗑️ Delete", key=f"del_{i}"):
                    searches.pop(i)
                    config_data["searches"] = searches
                    save_config(config_data)
