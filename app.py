import streamlit as st
import json
import time
import datetime
import requests
from bs4 import BeautifulSoup
from github import Github

# --- CONFIGURATION ---
REPO_NAME = "Timobaaij/sevenrooms-notifier" # <--- UPDATE THIS
CONFIG_FILE_PATH = "config.json"

st.set_page_config(page_title="Resy/SR/OT Manager", page_icon="🍽️", layout="wide")

# --- CUSTOM CSS ---
st.markdown("""
<style>
    .stButton button { width: 100%; }
    div[data-testid="stMetricValue"] { font-size: 1.2rem; }
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
        repo.update_file(contents.path, "Update config via Web App", json.dumps(new_data, indent=2, sort_keys=True), contents.sha)
        st.toast("✅ Saved!", icon="💾")
        time.sleep(1)
        st.cache_data.clear()
        st.rerun()
    except Exception as e: st.error(f"Save Failed: {e}")

def get_opentable_id(url):
    """Scrapes the Numeric ID from an OpenTable URL"""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(r.text, 'html.parser')
        # Look for the RID in meta tags or specific scripts
        # Method 1: Meta tag
        meta = soup.find("meta", {"name": "ot:restaurant_id"})
        if meta: return meta.get("content")
        # Method 2: Script regex (fallback)
        import re
        match = re.search(r'"restaurantId":\s*(\d+)', r.text)
        if match: return match.group(1)
        return None
    except: return None

# --- LAYOUT ---
col_main, col_tools = st.columns([3, 1.2], gap="medium")

with col_main:
    st.title("🍽️ Active Searches")
    searches = config_data.get("searches", [])
    
    if not searches: st.info("No active searches.")
    else:
        cols = st.columns(2)
        for i, s in enumerate(searches):
            with cols[i % 2]:
                with st.container(border=True):
                    # Badge for Platform
                    plat = s.get("platform", "sevenrooms").upper()
                    st.caption(f"🏷️ {plat}")
                    
                    st.subheader(s.get("id", "Unnamed"))
                    m1, m2 = st.columns(2)
                    m1.metric("Date", s.get("date"))
                    m2.metric("Venues", len(s.get("venues", [])))
                    
                    with st.expander("Edit"):
                        with st.form(key=f"edit_{i}"):
                            # Note: Platform is locked on edit for simplicity
                            e_id = st.text_input("Name", s.get("id"))
                            e_venues = st.text_input("IDs/Slugs", ", ".join(s.get("venues", [])))
                            e_date = st.date_input("Date", datetime.datetime.strptime(s.get("date"), "%Y-%m-%d").date())
                            c1, c2 = st.columns(2)
                            with c1: e_start = st.time_input("Start", datetime.datetime.strptime(s.get("window_start"), "%H:%M").time())
                            with c2: e_end = st.time_input("End", datetime.datetime.strptime(s.get("window_end"), "%H:%M").time())
                            e_email = st.text_input("Email", s.get("email_to", ""))
                            
                            if st.form_submit_button("💾 Save"):
                                searches[i].update({
                                    "id": e_id,
                                    "venues": [v.strip() for v in e_venues.split(",") if v.strip()],
                                    "date": str(e_date),
                                    "window_start": e_start.strftime("%H:%M"),
                                    "window_end": e_end.strftime("%H:%M"),
                                    "email_to": e_email,
                                    "salt": str(time.time())
                                })
                                config_data["searches"] = searches
                                save_config(config_data)

                    if st.button("Delete", key=f"del_{i}"):
                        searches.pop(i)
                        config_data["searches"] = searches
                        save_config(config_data)

# --- RIGHT PANEL (TOOLS) ---
with col_tools:
    st.header("🛠️ Tools")
    
    with st.container(border=True):
        st.subheader("Add Search")
        
        # 1. Choose Platform
        platform_choice = st.radio("Platform", ["SevenRooms", "OpenTable"], horizontal=True)
        platform_key = platform_choice.lower()
        
        with st.form("add"):
            n_id = st.text_input("Name", placeholder="Anniversary")
            
            # Helper text changes based on platform
            if platform_key == "opentable":
                n_venues = st.text_input("Restaurant IDs", placeholder="e.g. 12345 (Use ID Finder below)")
            else:
                n_venues = st.text_input("Restaurant Slugs", placeholder="e.g. sexyfishlondon")
                
            n_date = st.date_input("Date")
            c1, c2 = st.columns(2)
            with c1: n_start = st.time_input("Start", datetime.time(18,0))
            with c2: n_end = st.time_input("End", datetime.time(21,0))
            n_party = st.number_input("Party", 2)
            n_days = st.number_input("Days", 1)
            n_email = st.text_input("Email", placeholder="me@gmail.com")
            
            if st.form_submit_button("🚀 Launch"):
                searches.append({
                    "id": n_id,
                    "platform": platform_key,
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

    st.write("")
    
    # DYNAMIC TOOL SECTION
    with st.container(border=True):
        if platform_key == "opentable":
            st.subheader("🆔 OpenTable ID Finder")
            st.info("Paste the restaurant URL to find its ID.")
            ot_url = st.text_input("URL", placeholder="opentable.com/r/...")
            if st.button("Find ID"):
                if ot_url:
                    rid = get_opentable_id(ot_url)
                    if rid: st.success(f"ID Found: `{rid}`")
                    else: st.error("Could not find ID. Try another URL.")
        else:
            st.subheader("🕵️‍♀️ SevenRooms Slug Hunter")
            st.info("Find the slug (the part after /reservations/)")
            q = st.text_input("Restaurant Name")
            if q:
                st.link_button("Search Google", f"https://www.google.com/search?q={q}+SevenRooms+reservations")
