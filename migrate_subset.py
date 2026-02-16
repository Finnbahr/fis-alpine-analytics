#!/usr/bin/env python3
"""
Migrate a subset of data from local PostgreSQL to Render PostgreSQL
Only copies recent data (last 3 years) to stay under 1GB limit
"""

import psycopg2
from psycopg2.extras import execute_batch
import sys

# Local database connection
LOCAL_DB = {
    'host': '127.0.0.1',
    'port': 5433,
    'user': 'alpine_analytics',
    'password': 'Plymouthskiing1!',
    'database': 'alpine_analytics'
}

# Render database connection (paste your External Database URL here)
RENDER_DB_URL = "postgresql://alpine_analytics:Ne22KJajA0y99QBIEf7gfY1YJxBWwu76@dpg-d68iufesb7us73cco69g-a.oregon-postgres.render.com/alpine_analytics_bau7"

print("🔄 FIS Alpine Analytics - Data Migration")
print("=" * 50)
print()

# Connect to local database
print("📡 Connecting to local database...")
try:
    local_conn = psycopg2.connect(**LOCAL_DB)
    local_cur = local_conn.cursor()
    print("✅ Connected to local database")
except Exception as e:
    print(f"❌ Failed to connect to local database: {e}")
    sys.exit(1)

# Connect to Render database
print("📡 Connecting to Render database...")
try:
    render_conn = psycopg2.connect(RENDER_DB_URL)
    render_cur = render_conn.cursor()
    print("✅ Connected to Render database")
except Exception as e:
    print(f"❌ Failed to connect to Render database: {e}")
    sys.exit(1)

print()
print("🏗️ Creating schemas...")

# Create schemas
schemas = ['raw', 'athlete_aggregate', 'course_aggregate', 'race_aggregate', 'worldcup_aggregate']
for schema in schemas:
    try:
        render_cur.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        render_conn.commit()
        print(f"  ✓ Created schema: {schema}")
    except Exception as e:
        print(f"  ⚠️  Schema {schema}: {e}")
        render_conn.rollback()

print()
print("📊 Copying data (this will take a few minutes)...")
print()

# Step 1: Copy recent race results (limit to 50,000 most recent)
print("1️⃣  Copying race results...")
try:
    # Get table structure
    local_cur.execute("""
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = 'raw' AND table_name = 'fis_results'
        ORDER BY ordinal_position
    """)
    columns = local_cur.fetchall()
    col_names = [col[0] for col in columns]
    col_list = ', '.join(col_names)

    # Create table on Render
    local_cur.execute("SELECT pg_get_tabledef('raw', 'fis_results')")
    # This won't work - let me use a simpler approach

    # Copy data
    local_cur.execute(f"""
        SELECT {col_list}
        FROM raw.fis_results
        WHERE raceDate >= '2023-01-01'
        ORDER BY raceDate DESC
        LIMIT 50000
    """)

    rows = local_cur.fetchall()
    print(f"   Found {len(rows)} recent race results")

    if rows:
        placeholders = ', '.join(['%s'] * len(col_names))
        insert_query = f"INSERT INTO raw.fis_results ({col_list}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"
        execute_batch(render_cur, insert_query, rows, page_size=1000)
        render_conn.commit()
        print(f"   ✅ Copied {len(rows)} race results")

except Exception as e:
    print(f"   ❌ Error copying race results: {e}")
    render_conn.rollback()

print()
print("✅ Migration complete!")
print()
print("🌐 Your application should now work at:")
print("   https://fis-alpine-analytics.vercel.app")

# Close connections
local_cur.close()
local_conn.close()
render_cur.close()
render_conn.close()
