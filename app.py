import streamlit as st
import json
import time
import datetime
import requests
import re
from bs4 import BeautifulSoup
from github import Github

# --- CONFIGURATION ---
REPO_NAME = "Timobaaij/sevenrooms-notifier"  # <--- UPDATE THIS
CONFIG_FILE_PATH = "config.json"

st.set_page_config(page_title="Reservation Manager", page_icon="🍽️", layout="wide")

# --- CUSTOM CSS ---
st.markdown("""
<style>
    .stButton button { width: 100%; }
    div[data-testid="stMetricValue"] { font-size: 1.2rem; }
    .big-font { font-size: 1.1rem; font-weight: 500; }
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

def opentable_id_search(query):
    """
    Search OpenTable's API directly for the ID.
    Much more reliable than scraping the website.
    """
    # This endpoint is used by their search bar
    url = "https://www.opentable.com/d/api/v1/autocomplete"
    
    params = {
        "term": query,
        "latitude": "51.5074", # Default to London/UK lat/long to bias results
        "longitude": "-0.1278",
        "language": "en-GB"
    }
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json"
    }
    
    try:
        r = requests.get(url, params=params, headers=headers, timeout=5)
        if r.status_code == 200:
            data = r.json()
            # The API returns a list of results. We look for 'restaurants'.
            candidates = []
            
            # Autocomplete often returns mixed results, we filter for restaurants
            if "autocomplete_results" in data:
                for item in data["autocomplete_results"]:
                    # We are looking for items that have an ID
                    if "id" in item and "name" in item:
                        candidates.append((item["name"], item["id"], item.get("display_line_2", "")))
            
            return candidates
    except Exception as e:
        print(f"API Search Error: {e}")
        
    return []

# --- MAIN LAYOUT ---
col_main, col_tools = st.columns([3, 1.3], gap="medium")

# ==========================================
# LEFT: DASHBOARD (Your Searches)
# ==========================================
with col_main:
    st.title("🍽️ Active Searches")
    searches = config_data.get("searches", [])
    
    if not searches: st.info("No active searches.")
    else:
        grid = st.columns(2)
        for i, s in enumerate(searches):
            with grid[i % 2]:
                with st.container(border=True):
                    c_title, c_badge = st.columns([3, 1])
                    c_title.subheader(s.get("id", "Unnamed"))
                    
                    plat = s.get("platform", "sevenrooms")
                    if plat == "opentable":
                        c_badge.markdown(":red[**OpenTable**]")
                    else:
                        c_badge.markdown(":blue[**7Rooms**]")
                    
                    m1, m2 = st.columns(2)
                    m1.metric("Date", s.get("date"))
                    m2.metric("Venues", len(s.get("venues", [])))
                    
                    with st.expander("Details"):
                        st.write(f"**IDs/Slugs:** {', '.join(s.get('venues', []))}")
                        if st.button("🗑️ Delete Search", key=f"del_{i}"):
                            searches.pop(i)
                            config_data["searches"] = searches
                            save_config(config_data)

# ==========================================
# RIGHT: SMART ADD TOOL
# ==========================================
with col_tools:
    st.header("➕ Add New Search")
    
    with st.container(border=True):
        platform = st.radio("Choose Platform", ["SevenRooms", "OpenTable"], horizontal=True)
        is_ot = (platform == "OpenTable")
        
        # 1. FINDER TOOL
        found_venues = [] # Store IDs/Slugs here
        
        if is_ot:
            st.info("🔎 **Search OpenTable Database:**")
            ot_query = st.text_input("Restaurant Name", placeholder="e.g. Gymkhana")
            
            if ot_query:
                with st.spinner("Searching OpenTable API..."):
                    results = opentable_id_search(ot_query)
                    
                if results:
                    st.success(f"Found {len(results)} matches:")
                    # Create a selectbox so user can pick the right one
                    options = {f"{name} ({loc})": rid for name, rid, loc in results}
                    selected_label = st.selectbox("Select Restaurant", options.keys())
                    
                    if selected_label:
                        selected_id = options[selected_label]
                        st.code(selected_id, language="text")
                        found_venues = [str(selected_id)] # Auto-select this ID
                else:
                    st.warning("No matches found via API. Try exact spelling.")
                    st.markdown("""
                    **Manual Fallback:**
                    1. Go to restaurant page on OpenTable.
                    2. Right Click -> **View Page Source**.
                    3. Search (Ctrl+F) for `"restaurantId"`.
                    4. Copy the number next to it.
                    """)
                    
        else:
            # SevenRooms Logic
            st.info("💡 **Slug Tip:** It's the last part of the URL (sevenrooms.com/reservations/**slug**)")
            sr_input = st.text_input("Venue Slug", placeholder="e.g. sexyfishlondon")
            if sr_input:
                found_venues = [x.strip() for x in sr_input.split(",") if x.strip()]

        st.divider()
        
        # 2. CONFIGURATION FORM
        with st.form("add_final"):
            st.write("### Search Config")
            # If we found an ID above, pre-fill it. 
            # If not, let user type manually (Fallback).
            default_val = found_venues[0] if found_venues else ""
            
            n_venues = st.text_input("Venue ID / Slug", value=default_val, help="The ID we found above")
            n_id = st.text_input("Search Name", placeholder="My Dinner")
            
            n_date = st.date_input("Date")
            c1, c2 = st.columns(2)
            n_start = c1.time_input("Start", datetime.time(18,0))
            n_end = c2.time_input("End", datetime.time(21,0))
            
            c3, c4 = st.columns(2)
            n_party = c3.number_input("Party", 2)
            n_days = c4.number_input("Flexibility (Days)", 1)
            
            n_email = st.text_input("Email", placeholder="me@gmail.com")
            
            if st.form_submit_button("🚀 Start Searching", type="primary"):
                if not n_venues:
                    st.error("Missing Venue ID/Slug")
                else:
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
