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

def magic_get_opentable_id(url_or_id):
    """
    Smart Function:
    1. If user pastes a URL (opentable.com/r/...), it fetches the page and finds the ID.
    2. If user pastes a number (12345), it returns it as is.
    """
    clean_input = url_or_id.strip()
    
    # If it's already a number, just return it
    if clean_input.isdigit():
        return clean_input
        
    # If it's a URL, let's go hunting
    if "opentable" in clean_input:
        try:
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
            r = requests.get(clean_input, headers=headers, timeout=10)
            
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, 'html.parser')
                
                # Method A: Look for the specific meta tag (Most reliable)
                # <meta name="ot:restaurant_id" content="12345">
                meta = soup.find("meta", {"name": "ot:restaurant_id"})
                if meta:
                    return meta.get("content")
                
                # Method B: Look for the ID in the JSON data scripts
                # "restaurantId":12345
                match = re.search(r'"restaurantId":\s*(\d+)', r.text)
                if match:
                    return match.group(1)
                    
        except Exception as e:
            print(f"Error fetching ID: {e}")
            return None
            
    return None

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
        # 2-Column Grid for cards
        grid = st.columns(2)
        for i, s in enumerate(searches):
            with grid[i % 2]:
                with st.container(border=True):
                    # Header with Badge
                    c_title, c_badge = st.columns([3, 1])
                    c_title.subheader(s.get("id", "Unnamed"))
                    
                    plat = s.get("platform", "sevenrooms")
                    if plat == "opentable":
                        c_badge.markdown(":red[**OpenTable**]")
                    else:
                        c_badge.markdown(":blue[**7Rooms**]")
                    
                    # Metrics
                    m1, m2 = st.columns(2)
                    m1.metric("Date", s.get("date"))
                    m2.metric("Venues", len(s.get("venues", [])))
                    
                    # Edit / Delete
                    with st.expander("Details"):
                        st.write(f"**Venues:** {', '.join(s.get('venues', []))}")
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
        # 1. Platform Switch
        platform = st.radio("Choose Platform", ["SevenRooms", "OpenTable"], horizontal=True)
        is_ot = (platform == "OpenTable")
        
        with st.form("add_form"):
            st.write("### 1. Restaurant Details")
            n_id = st.text_input("Name this Search", placeholder="e.g. Birthday Dinner")
            
            # --- SMART INPUT FIELD ---
            if is_ot:
                st.info("💡 **Easy Mode:** Just paste the full OpenTable website link below. We'll find the ID for you.")
                n_input = st.text_input("Restaurant Link", placeholder="https://www.opentable.com/r/gymkhana-london")
            else:
                st.info("💡 **Tip:** The slug is the last part of the URL. (sevenrooms.com/reservations/**slug**)")
                n_input = st.text_input("Venue Slug", placeholder="e.g. sexyfishlondon")

            st.write("### 2. Date & Time")
            n_date = st.date_input("Date")
            c1, c2 = st.columns(2)
            n_start = c1.time_input("Start", datetime.time(18,0))
            n_end = c2.time_input("End", datetime.time(21,0))
            
            st.write("### 3. Preferences")
            c3, c4 = st.columns(2)
            n_party = c3.number_input("Party Size", 2)
            n_days = c4.number_input("Flexibility (Days)", 1, help="Check X days starting from the date above")
            
            n_email = st.text_input("Email Alert To", placeholder="me@gmail.com")
            
            submit = st.form_submit_button("🚀 Start Searching", type="primary")
            
            if submit:
                # --- THE MAGIC LOGIC ---
                final_venues = []
                
                if is_ot:
                    # Logic for OpenTable: Convert Link -> ID
                    with st.spinner("🕵️‍♂️ Visiting OpenTable to find ID..."):
                        # Split by comma in case user pastes multiple links
                        links = [x.strip() for x in n_input.split(",") if x.strip()]
                        for link in links:
                            found_id = magic_get_opentable_id(link)
                            if found_id:
                                final_venues.append(found_id)
                                st.success(f"Found ID: {found_id}")
                            else:
                                st.error(f"Could not find ID for: {link}")
                                st.stop()
                else:
                    # Logic for SevenRooms: Use slug directly
                    final_venues = [x.strip() for x in n_input.split(",") if x.strip()]

                # Save if we have valid venues
                if final_venues:
                    searches.append({
                        "id": n_id,
                        "platform": "opentable" if is_ot else "sevenrooms",
                        "venues": final_venues,
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
                else:
                    st.error("Please enter a valid Link or Slug.")

    # Extra helper for SevenRooms since it doesn't have the Magic Link feature yet
    if not is_ot:
        st.write("")
        with st.expander("Don't know the SevenRooms slug?"):
            q = st.text_input("Restaurant Name", placeholder="Gymkhana")
            if q:
                url = f"https://www.google.com/search?q={q.replace(' ', '+')}+SevenRooms+reservations"
                st.link_button("Search on Google", url)
