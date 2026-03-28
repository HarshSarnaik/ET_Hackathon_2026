import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv(override=True)  # override=True forces .env to win over OS env vars

SID   = os.getenv("TWILIO_ACCOUNT_SID")
TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
FROM_ = os.getenv("TWILIO_WHATSAPP_FROM")
TO_   = os.getenv("OWNER_WHATSAPP")

print(f"SID  : {SID}")
print(f"TOKEN: {TOKEN[:4]}...{TOKEN[-4:] if TOKEN else ''}")
print(f"FROM : {FROM_}")
print(f"TO   : {TO_}")
print(f"{'='*40}")

try:
    from twilio.rest import Client
    client = Client(SID, TOKEN)
    msg = client.messages.create(
        body="[HIGH] Idle VM: dev-backend-01 (t2.micro, DEV)\nCPU:8.72% Idle:62h\nApprove: http://localhost:5050/approve?id=i-001",
        from_=FROM_,
        to=TO_
    )
    print(f"SUCCESS: SID={msg.sid} | status={msg.status}")
except Exception as e:
    print(f"EXACT ERROR: {e}")
