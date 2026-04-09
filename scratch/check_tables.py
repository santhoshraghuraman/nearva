
import httpx
import asyncio
import os
from dotenv import load_dotenv

load_dotenv()

async def check_tables():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}"
    }
    
    tables = ["workers", "users", "ambassadors", "job_requests"]
    async with httpx.AsyncClient() as client:
        for table in tables:
            resp = await client.get(f"{url}/rest/v1/{table}?select=count", headers=headers)
            print(f"Table {table}: {resp.status_code}")
            if resp.status_code != 200:
                print(f"  Error: {resp.text}")

if __name__ == "__main__":
    asyncio.run(check_tables())
