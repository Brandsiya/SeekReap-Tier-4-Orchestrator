# Add this line after DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://')
    
# Test connection with SSL
try:
    from sqlalchemy import create_engine
    engine = create_engine(DATABASE_URL + "?sslmode=require")
    conn = engine.connect()
    conn.close()
    DB_CONNECTED = True
    print("✅ Database connected with SSL")
except:
    DB_CONNECTED = False
    print("❌ Database SSL connection failed")
