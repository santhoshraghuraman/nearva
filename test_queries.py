import httpx
import os
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

headers = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}"
}

print("Testing ambassadors query...")
url1 = f"{SUPABASE_URL}/rest/v1/ambassadors?select=id,name,phone,area,status,referral_code,created_at&order=created_at.desc"
r1 = httpx.get(url1, headers=headers)
print(f"S1: {r1.status_code}")
if r1.status_code >= 400: print(r1.text)

print("Testing workers count query...")
url2 = f"{SUPABASE_URL}/rest/v1/workers?select=ambassador_id&ambassador_id=not.is.null"
r2 = httpx.get(url2, headers=headers)
print(f"S2: {r2.status_code}")
if r2.status_code >= 400: print(r2.text)
