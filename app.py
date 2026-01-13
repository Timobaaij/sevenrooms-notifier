import streamlit as st
import json
from github import Github
import datetime

# --- Constants ---
REPO_NAME = "Timobaaij/sevenrooms-notifier" # <--- CHANGE THIS to your actual username/repo
CONFIG_FILE_PATH = "config.json"

# --- Page Setup ---
st.set_page_config(page_title="SevenRooms Manager", layout="wide")
st.title("🍽️ SevenRooms Search Manager")

# --- Authentication & GitHub Connection ---
# We get the token from Streamlit Secrets (configured in the cloud dashboard later)
try:
    token = st.secrets["GITHUB_TOKEN"]
except FileNotFoundError:
    st.error("GitHub Token not found. Please set it in Streamlit Secrets.")
    st.stop()

try:
    g = Github(token)
    repo = g.get_repo(REPO_NAME)
    contents = repo.get_contents(CONFIG_FILE_PATH)
    config_data = json.loads(contents.decoded_content.decode("utf-8"))
except Exception as e:
    st.error(f"Error connecting to GitHub or reading config: {e}")
    st.stop()

# --- Helper Functions ---
def save_config_to_github(new_config):
    """Commits the updated JSON back to GitHub."""
    try:
        updated_content = json.dumps(new_config, indent=2, sort_keys=True)
        repo.update_file(
            path=contents.path,
            message="Update config via Web App",
            content=updated_content,
            sha=contents.sha
        )
        st.success("✅ Configuration saved! The notifier will pick up changes in the next run.")
        st.cache_data.clear() # Clear cache to force reload next time
    except Exception as e:
        st.error(f"Failed to save to GitHub: {e}")

# --- UI Layout ---

# 1. Display Current Searches
st.subheader("Current Searches")

searches = config_data.get("searches", [])

if not searches:
    st.info("No active searches found.")
else:
    for i, search in enumerate(searches):
        with st.expander(f"📍 {search.get('id', 'Unnamed')} - {', '.join(search.get('venues', []))}"):
            col1, col2 = st.columns([3, 1])
            with col1:
                st.write(f"**Date:** {search.get('date')} | **Party:** {search.get('party_size')}")
                st.write(f"**Window:** {search.get('window_start')} - {search.get('window_end')}")
                st.write(f"**Venue(s):** {search.get('venues')}")
            with col2:
                if st.button("Delete Search", key=f"del_{i}"):
                    searches.pop(i)
                    config_data["searches"] = searches
                    save_config_to_github(config_data)
                    st.rerun()

# 2. Add New Search Form
st.markdown("---")
st.subheader("➕ Add New Search")

with st.form("add_search_form"):
    c1, c2 = st.columns(2)
    with c1:
        new_id = st.text_input("Search ID (Unique Name)", value="birthday_dinner")
        new_venues = st.text_input("Venues (comma separated)", value="rambutan, som saa")
        new_date = st.date_input("Date", datetime.date.today() + datetime.timedelta(days=7))
    with c2:
        new_party = st.number_input("Party Size", min_value=1, value=2)
        new_start = st.time_input("Window Start", datetime.time(18, 0))
        new_end = st.time_input("Window End", datetime.time(21, 0))
        new_days = st.number_input("Check for X days (default 1)", min_value=1, value=1)
    
    submitted = st.form_submit_button("Save New Search")

    if submitted:
        # Format the data to match config.json requirements
        venue_list = [v.strip() for v in new_venues.split(",") if v.strip()]
        
        new_entry = {
            "id": new_id,
            "venues": venue_list,
            "party_size": int(new_party),
            "date": str(new_date),
            "window_start": new_start.strftime("%H:%M"),
            "window_end": new_end.strftime("%H:%M"),
            "time_slot": new_start.strftime("%H:%M"), # Defaulting preference to start time
            "num_days": int(new_days),
            "ntfy": {"title": f"Slot found: {new_id}"}
        }
        
        searches.append(new_entry)
        config_data["searches"] = searches
        save_config_to_github(config_data)
        st.rerun()
