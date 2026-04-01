import psycopg2
import time

print("Attempting to wake up database (this may take 15-30 seconds)...")
start = time.time()

try:
    conn = psycopg2.connect(
        host="ep-rapid-base-ai27r1sa-pooler.c-4.us-east-1.aws.neon.tech",
        port=5432,
        user="neondb_owner",
        password="npg_vZMKED9Wig2C",
        database="seekreap_neon_db",
        sslmode="require",
        connect_timeout=45  # Long timeout for wake-up
    )
    elapsed = time.time() - start
    print(f"✅ Database woke up and connected in {elapsed:.1f} seconds!")
    
    # Test the connection
    cur = conn.cursor()
    cur.execute("SELECT version(), current_database(), current_user")
    version, db, user = cur.fetchone()
    print(f"   Database: {db}")
    print(f"   User: {user}")
    print(f"   Version: {version[:60]}...")
    
    # Check if tables exist
    cur.execute("""
        SELECT COUNT(*) FROM information_schema.tables 
        WHERE table_schema = 'public'
    """)
    table_count = cur.fetchone()[0]
    print(f"   Tables found: {table_count}")
    
    conn.close()
    
except Exception as e:
    elapsed = time.time() - start
    print(f"❌ Failed after {elapsed:.1f} seconds: {e}")
