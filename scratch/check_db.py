import asyncio
import httpx
import os
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json"
}

async def check_tables():
    async with httpx.AsyncClient() as client:
        # Check users table
        print("Checking 'users' table...")
        res = await client.get(f"{SUPABASE_URL}/rest/v1/users?select=count", headers=HEADERS)
        print(f"Users Status: {res.status_code}, Body: {res.text}")
        
        # Check ambassadors table
        print("\nChecking 'ambassadors' table...")
        res = await client.get(f"{SUPABASE_URL}/rest/v1/ambassadors?select=count", headers=HEADERS)
        print(f"Ambassadors Status: {res.status_code}, Body: {res.text}")
        
        # Check workers table
        print("\nChecking 'workers' table...")
        res = await client.get(f"{SUPABASE_URL}/rest/v1/workers?select=count", headers=HEADERS)
        print(f"Workers Status: {res.status_code}, Body: {res.text}")

if __name__ == "__main__":
    asyncio.run(check_tables())
