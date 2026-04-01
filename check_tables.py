import psycopg2

conn = psycopg2.connect(
    host="ep-rapid-base-ai27r1sa-pooler.c-4.us-east-1.aws.neon.tech",
    port=5432,
    user="neondb_owner",
    password="npg_vZMKED9Wig2C",
    database="seekreap_neon_db",
    sslmode="require",
    connect_timeout=10
)

cur = conn.cursor()

# Get all table names and row counts
cur.execute("""
    SELECT 
        table_name,
        (SELECT COUNT(*) FROM information_schema.tables t2 
         WHERE t2.table_name = t1.table_name AND t2.table_schema = 'public')
    FROM information_schema.tables t1
    WHERE table_schema = 'public'
    ORDER BY table_name
""")

print("=" * 60)
print("DATABASE TABLES AND ROW COUNTS")
print("=" * 60)

for table in cur.fetchall():
    table_name = table[0]
    try:
        cur.execute(f"SELECT COUNT(*) FROM {table_name}")
        count = cur.fetchone()[0]
        print(f"{table_name:30} : {count:>8,} rows")
    except Exception as e:
        print(f"{table_name:30} : ERROR - {e}")

# Check job queue specifically
print("\n" + "=" * 60)
print("JOB QUEUE STATUS")
print("=" * 60)
cur.execute("""
    SELECT status, COUNT(*) 
    FROM job_queue 
    GROUP BY status
""")
for status, count in cur.fetchall():
    print(f"  {status:15} : {count}")

# Check recent submissions
print("\n" + "=" * 60)
print("RECENT SUBMISSIONS (last 5)")
print("=" * 60)
cur.execute("""
    SELECT id, status, submitted_at 
    FROM submissions 
    ORDER BY submitted_at DESC 
    LIMIT 5
""")
for row in cur.fetchall():
    print(f"  {row[0][:8]}... | {row[1]:12} | {row[2]}")

conn.close()
