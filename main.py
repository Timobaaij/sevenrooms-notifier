import os
import json
import hashlib
import datetime as dt
import requests
import smtplib
from email.message import EmailMessage

def load_json(path, default):
    try:
        with open(path, "r") as f: return json.load(f)
    except: return default

def save_json(path, obj):
    with open(path, "w") as f: json.dump(obj, f, indent=2)

def send_push(topic, title, message):
    if not topic: return
    requests.post(f"https://ntfy.sh/{topic}", data=message.encode("utf-8"), headers={"Title": title})

def send_email(to_email, subject, body):
    user, pw = os.environ.get("EMAIL_USER"), os.environ.get("EMAIL_PASS")
    if not user or not pw: return
    msg = EmailMessage()
    msg.set_content(body)
    msg['Subject'], msg['From'], msg['To'] = subject, user, to_email
    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as s:
        s.login(user, pw)
        s.send_message(msg)

def main():
    config = load_json("config.json", {"searches": []})
    state = load_json("state.json", {"notified": []})
    notified = set(state.get("notified", []))
    topic = config.get("ntfy", {}).get("topic")

    for s in config.get("searches", []):
        sid, plat, venues, date, party, salt = s.get("id"), s.get("platform"), s.get("venues"), s.get("date"), s.get("party_size"), s.get("salt", "")
        found = []
        for v in venues:
            if plat == "opentable":
                # OpenTable logic
                r = requests.get(f"https://www.opentable.com/api/v2/reservation/availability?rid={v}&partySize={party}&dateTime={date}T19:00", headers={"User-Agent": "Mozilla/5.0"})
                slots = [t.get("dateTime") for t in r.json().get("availability", {}).get(next(iter(r.json().get("availability", {})), ""), []) if t.get("isAvailable")]
            else:
                # SevenRooms logic
                d_sr = dt.datetime.strptime(date, "%Y-%m-%d").strftime("%m-%d-%Y")
                r = requests.get(f"https://www.sevenrooms.com/api-yoa/availability/widget/range?venue={v}&party_size={party}&start_date={d_sr}&num_days=1&channel=SEVENROOMS_WIDGET")
                slots = [t.get("time_iso") for day in r.json().get("data", {}).get("availability", {}).values() for timeslot in day for t in timeslot.get("times", []) if not t.get("is_requestable")]
            
            for slot in slots:
                fingerprint = hashlib.sha256(f"{sid}{slot}{v}{salt}".encode()).hexdigest()
                if fingerprint not in notified:
                    found.append(f"{v} @ {slot}")
                    notified.add(fingerprint)
        
        if found:
            msg = f"Tables for {sid}:\n" + "\n".join(found)
            send_push(topic, f"Table Alert: {sid}", msg)
            if s.get("email_to"): send_email(s.get("email_to"), f"Table Alert: {sid}", msg)

    save_json("state.json", {"notified": list(notified)[-1000:]})

if __name__ == "__main__":
    main()
