import asyncio
import asyncpg
import os
from dotenv import load_dotenv

load_dotenv()

async def test():
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("❌ ERROR: DATABASE_URL not found in environment!")
        return

    try:
        conn = await asyncpg.connect(db_url)
        print("✅ Connection Successful!")
        
        # Check if the 'result' column we added actually exists
        row = await conn.fetchrow("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = 'job_queue' AND column_name = 'result'
        """)
        
        if row:
            print("✅ 'result' column is present.")
        else:
            print("❌ 'result' column is MISSING! Run migration again.")
            
        await conn.close()
    except Exception as e:
        print(f"❌ Connection Failed: {e}")

if __name__ == "__main__":
    asyncio.run(test())
