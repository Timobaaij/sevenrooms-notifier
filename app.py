import streamlit as st
import json
from github import Github
import datetime

# --- Constants ---
# CRITICAL: UPDATE THIS TO YOUR REPO
REPO_NAME = "Timobaaij/sevenrooms-notifier" 
CONFIG_FILE_PATH = "config.json"

# --- Page Setup ---
st.set_page_config(page_title="SevenRooms Manager", layout="wide")
st.title("🍽️ SevenRooms Search Manager")

# --- Authentication ---
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
    st.error(f"Error connecting to GitHub: {e}")
    st.stop()

# --- Helpers ---
def save_config(new_data):
    try:
        repo.update_file(
            path=contents.path,
            message="Update config via Web App",
            content=json.dumps(new_data, indent=2, sort_keys=True),
            sha=contents.sha
        )
        st.success("✅ Saved to GitHub!")
        st.cache_data.clear()
        st.rerun()
    except Exception as e:
        st.error(f"Failed to save: {e}")

def parse_date(d_str):
    try: return datetime.datetime.strptime(d_str, "%Y-%m-%d").date()
    except: return datetime.date.today()

def parse_time(t_str):
    try: return datetime.datetime.strptime(t_str, "%H:%M").time()
    except: return datetime.time(18, 0)

# --- Main UI ---

searches = config_data.get("searches", [])

st.subheader(f"Manage Searches ({len(searches)})")

if not searches:
    st.info("No active searches. Add one below.")

for i, search in enumerate(searches):
    label = f"📍 {search.get('id', 'Unnamed')} ({search.get('date', '?')})"
    with st.expander(label, expanded=False):
        
        with st.form(key=f"edit_form_{i}"):
            c1, c2, c3 = st.columns(3)
            
            with c1:
                e_id = st.text_input("ID", value=search.get("id", ""))
                e_venues = st.text_input("Venues (comma sep)", value=", ".join(search.get("venues", [])))
                e_date = st.date_input("Date", value=parse_date(search.get("date", "")))
                
            with c2:
                e_party = st.number_input("Party Size", min_value=1, value=int(search.get("party_size", 2)))
                e_start = st.time_input("Start Time", value=parse_time(search.get("window_start", "18:00")))
                e_end = st.time_input("End Time", value=parse_time(search.get("window_end", "21:00")))
            
            with c3:
                e_days = st.number_input("Days to Check", min_value=1, value=int(search.get("num_days", 1)))
                # NEW FIELD
                e_email = st.text_input("Email Alert To (Optional)", value=search.get("email_to", ""))

            if st.form_submit_button("💾 Update Search"):
                searches[i] = {
                    "id": e_id,
                    "venues": [v.strip() for v in e_venues.split(",") if v.strip()],
                    "party_size": e_party,
                    "date": str(e_date),
                    "window_start": e_start.strftime("%H:%M"),
                    "window_end": e_end.strftime("%H:%M"),
                    "time_slot": e_start.strftime("%H:%M"), 
                    "num_days": e_days,
                    "email_to": e_email.strip(), # Save Email
                    "ntfy": search.get("ntfy", {"title": f"Slot found: {e_id}"})
                }
                config_data["searches"] = searches
                save_config(config_data)

        if st.button("🗑️ Delete Search", key=f"del_{i}"):
            searches.pop(i)
            config_data["searches"] = searches
            save_config(config_data)

st.markdown("---")
st.subheader("➕ Add New Search")

with st.form("add_new"):
    c1, c2, c3 = st.columns(3)
    with c1:
        n_id = st.text_input("ID", value="new_dinner")
        n_venues = st.text_input("Venues", value="restaurant_a, restaurant_b")
        n_date = st.date_input("Date", value=datetime.date.today() + datetime.timedelta(days=7))
    with c2:
        n_party = st.number_input("Party", min_value=1, value=2)
        n_start = st.time_input("Start", value=datetime.time(19, 0))
        n_end = st.time_input("End", value=datetime.time(21, 30))
    with c3:
        n_days = st.number_input("Days", min_value=1, value=1)
        # NEW FIELD
        n_email = st.text_input("Email Alert To (Optional)", value="")
    
    if st.form_submit_button("Add Search"):
        new_entry = {
            "id": n_id,
            "venues": [v.strip() for v in n_venues.split(",") if v.strip()],
            "party_size": n_party,
            "date": str(n_date),
            "window_start": n_start.strftime("%H:%M"),
            "window_end": n_end.strftime("%H:%M"),
            "time_slot": n_start.strftime("%H:%M"),
            "num_days": n_days,
            "email_to": n_email.strip(), # Save Email
            "ntfy": {"title": f"Slot found: {n_id}"}
        }
        searches.append(new_entry)
        config_data["searches"] = searches
        save_config(config_data)
