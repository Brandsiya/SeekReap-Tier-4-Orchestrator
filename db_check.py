#!/usr/bin/env python3
import psycopg2
import sys

# The connection string
conn_string = "postgresql://neondb_owner:npg_vZMKED9Wig2C@ep-rapid-base-ai27r1sa-pooler.c-4.us-east-1.aws.neon.tech/seekreap_neon_db?sslmode=require"

print("=" * 60)
print("DATABASE CONNECTIVITY CHECK")
print("=" * 60)

try:
    # Try to connect
    print("\n1. Attempting to connect to database...")
    conn = psycopg2.connect(conn_string, connect_timeout=10)
    print("   ✅ CONNECTION SUCCESSFUL!")
    
    cur = conn.cursor()
    
    # Check database info
    print("\n2. Database Information:")
    cur.execute("SELECT current_database(), current_user, version()")
    db_name, db_user, version = cur.fetchone()
    print(f"   Database: {db_name}")
    print(f"   User: {db_user}")
    print(f"   PostgreSQL: {version[:60]}...")
    
    # Check if tables exist
    print("\n3. Checking for tables:")
    cur.execute("""
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_schema = 'public'
        ORDER BY table_name
    """)
    tables = cur.fetchall()
    
    if tables:
        print(f"   ✅ Found {len(tables)} tables:")
        for table in tables:
            # Get row count for each table
            cur.execute(f"SELECT COUNT(*) FROM {table[0]}")
            count = cur.fetchone()[0]
            print(f"      - {table[0]}: {count:,} rows")
    else:
        print("   ⚠️  No tables found in public schema - database is empty!")
    
    # Check specific tables that should exist
    print("\n4. Checking critical tables:")
    critical_tables = ['submissions', 'creators', 'job_queue', 'fingerprints', 'content_matches']
    for table in critical_tables:
        cur.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_schema = 'public' 
                AND table_name = %s
            )
        """, (table,))
        exists = cur.fetchone()[0]
        if exists:
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            count = cur.fetchone()[0]
            print(f"   ✅ {table}: {count:,} rows")
        else:
            print(f"   ❌ {table}: table does not exist")
    
    # Check recent submissions
    print("\n5. Recent submissions (last 5):")
    cur.execute("""
        SELECT id, status, submitted_at, overall_risk_score 
        FROM submissions 
        ORDER BY submitted_at DESC 
        LIMIT 5
    """)
    recent = cur.fetchall()
    if recent:
        for row in recent:
            print(f"   - {row[0][:8]}... | {row[1]} | {row[2]} | score: {row[3]}")
    else:
        print("   No submissions found")
    
    # Check pending jobs
    print("\n6. Job queue status:")
    cur.execute("""
        SELECT status, COUNT(*) 
        FROM job_queue 
        GROUP BY status
    """)
    jobs = cur.fetchall()
    if jobs:
        for status, count in jobs:
            print(f"   - {status}: {count}")
    else:
        print("   No jobs in queue")
    
    conn.close()
    print("\n" + "=" * 60)
    print("✅ DATABASE CHECK COMPLETE")
    
except psycopg2.OperationalError as e:
    print(f"\n❌ CONNECTION FAILED: {e}")
    print("\nThis indicates the password is incorrect or the database is not accessible.")
    print("You need to get the correct connection string from Neon Console.")
    sys.exit(1)
except Exception as e:
    print(f"\n❌ ERROR: {e}")
    sys.exit(1)
