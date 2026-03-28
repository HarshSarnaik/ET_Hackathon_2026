import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv

load_dotenv(override=True)
SID = os.getenv("TWILIO_ACCOUNT_SID")
TOKEN = os.getenv("TWILIO_AUTH_TOKEN")

try:
    from twilio.rest import Client
    client = Client(SID, TOKEN)
    
    print("Fetching last 5 Twilio WhatsApp attempts...")
    print("-" * 50)
    messages = client.messages.list(limit=5)
    
    for msg in messages:
        print(f"Time  : {msg.date_created}")
        print(f"To    : {msg.to}")
        print(f"Status: {msg.status.upper()}")
        print(f"Error : Code {msg.error_code} — {msg.error_message}")
        print(f"Body  : {msg.body[:50]}...")
        print("-" * 50)
        
except Exception as e:
    print(f"Failed to fetch Twilio logs: {e}")
