import streamlit as st
import json
import time
from github import Github
import datetime

# --- CONFIGURATION ---
# CRITICAL: UPDATE THIS TO YOUR REPO
REPO_NAME = "Timobaaij/sevenrooms-notifier" 
CONFIG_FILE_PATH = "config.json"

st.set_page_config(page_title="SevenRooms Manager", layout="wide")
st.title("🍽️ SevenRooms Search Manager")

# --- HELP SECTION ---
with st.expander("ℹ️ Help: How to find 'Venue Slugs'"):
    st.markdown("""
    **What is a Slug?**
    It is the unique ID for the restaurant in the SevenRooms URL.
    
    **How to find it:**
    1. Go to the restaurant's booking page.
    2. Look at the URL: `https://www.sevenrooms.com/reservations/gymkhana`
    3. The slug is the last part: **`gymkhana`**
    
    **Examples:**
    - URL: `.../reservations/lidios` -> Slug: `lidios`
    - URL: `.../reservations/somsaa` -> Slug: `somsaa`
    
    **Pro Tip:**
    To find new restaurants, Google this:  
    `site:sevenrooms.com/reservations "London"`
    """)

# --- AUTH ---
try:
    token = st.secrets["GITHUB_TOKEN"]
    g = Github(token)
    repo = g.get_repo(REPO_NAME)
    contents = repo.get_contents(CONFIG_FILE_PATH)
    config_data = json.loads(contents.decoded_content.decode("utf-8"))
except Exception as e:
    st.error(f"Connection Error: {e}")
    st.stop()

# --- FUNCTIONS ---
def save_config(new_data):
    try:
        repo.update_file(contents.path, "Update config via Web App", json.dumps(new_data, indent=2, sort_keys=True), contents.sha)
        st.success("✅ Saved to GitHub!")
        st.cache_data.clear()
        st.rerun()
    except Exception as e: st.error(f"Save Failed: {e}")

def parse_date(d):
    try: return datetime.datetime.strptime(d, "%Y-%m-%d").date()
    except: return datetime.date.today()

def parse_time(t):
    try: return datetime.datetime.strptime(t, "%H:%M").time()
    except: return datetime.time(19, 0)

# --- UI ---
searches = config_data.get("searches", [])
st.subheader(f"Active Searches ({len(searches)})")

if not searches: st.info("No active searches.")

for i, s in enumerate(searches):
    label = f"📍 {s.get('id')} - {s.get('date')}"
    with st.expander(label):
        with st.form(key=f"edit_{i}"):
            c1, c2, c3 = st.columns(3)
            with c1:
                e_id = st.text_input("ID", s.get("id"))
                e_venues = st.text_input("Venues (comma sep)", ", ".join(s.get("venues", [])))
                e_date = st.date_input("Date", parse_date(s.get("date")))
            with c2:
                e_party = st.number_input("Party", 1, value=int(s.get("party_size", 2)))
                e_start = st.time_input("Start Window", parse_time(s.get("window_start")))
                e_end = st.time_input("End Window", parse_time(s.get("window_end")))
            with c3:
                e_email = st.text_input("Email Alert To", s.get("email_to", ""))
                e_days = st.number_input("Days to check", 1, value=int(s.get("num_days", 1)))
                
            if st.form_submit_button("💾 Update (Resets Notifications)"):
                searches[i] = {
                    "id": e_id,
                    "venues": [v.strip() for v in e_venues.split(",") if v.strip()],
                    "party_size": e_party,
                    "date": str(e_date),
                    "window_start": e_start.strftime("%H:%M"),
                    "window_end": e_end.strftime("%H:%M"),
                    "email_to": e_email.strip(),
                    "num_days": e_days,
                    "salt": str(time.time()) # Resets the unique ID so you get emailed again
                }
                config_data["searches"] = searches
                save_config(config_data)
                
        if st.button("Delete", key=f"del_{i}"):
            searches.pop(i)
            config_data["searches"] = searches
            save_config(config_data)

st.markdown("---")
st.subheader("➕ Add New Search")
with st.form("add"):
    c1, c2, c3 = st.columns(3)
    with c1:
        n_id = st.text_input("ID", "Birthday")
        n_venues = st.text_input("Venues (slugs)", "lidios, som-saa")
        n_date = st.date_input("Date")
    with c2:
        n_party = st.number_input("Party", 1, 2)
        n_start = st.time_input("Start", datetime.time(18,0))
        n_end = st.time_input("End", datetime.time(21,0))
    with c3:
        n_email = st.text_input("Email To", "")
        n_days = st.number_input("Days to check", 1, value=1)
        
    if st.form_submit_button("Add Search"):
        searches.append({
            "id": n_id,
            "venues": [v.strip() for v in n_venues.split(",") if v.strip()],
            "party_size": n_party,
            "date": str(n_date),
            "window_start": n_start.strftime("%H:%M"),
            "window_end": n_end.strftime("%H:%M"),
            "email_to": n_email.strip(),
            "num_days": n_days,
            "salt": str(time.time()) # New salt for new search
        })
        config_data["searches"] = searches
        save_config(config_data)
