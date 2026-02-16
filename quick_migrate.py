#!/usr/bin/env python3
"""
Quick migration of essential data for FIS Alpine Analytics demo
Copies minimal recent data to make the deployed app functional
"""

import psycopg2
from psycopg2.extras import execute_batch
import sys
from datetime import datetime

# Local database
LOCAL_DB = {
    'host': '127.0.0.1',
    'port': 5433,
    'user': 'alpine_analytics',
    'password': 'Plymouthskiing1!',
    'database': 'alpine_analytics'
}

# Render database
RENDER_DB_URL = "postgresql://alpine_analytics:Ne22KJajA0y99QBIEf7gfYlYJxBWwu76@dpg-d68iufesb7us73cco69g-a.ohio-postgres.render.com/alpine_analytics_bau7"

print("🏔️ FIS Alpine Analytics - Quick Data Migration")
print("=" * 60)
print()
print("This will copy recent data to make your deployed app functional")
print()

# Connect to databases
print("📡 Connecting to databases...")
try:
    local_conn = psycopg2.connect(**LOCAL_DB)
    local_cur = local_conn.cursor()
    print("  ✅ Local database connected")
except Exception as e:
    print(f"  ❌ Failed to connect to local database: {e}")
    print(f"     Make sure PostgreSQL is running on port 5433")
    sys.exit(1)

try:
    render_conn = psycopg2.connect(RENDER_DB_URL)
    render_cur = render_conn.cursor()
    render_conn.set_session(autocommit=False)
    print("  ✅ Render database connected")
except Exception as e:
    print(f"  ❌ Failed to connect to Render database: {e}")
    sys.exit(1)

print()
print("🏗️ Setting up database structure...")

# Create schemas
schemas = ['raw', 'athlete_aggregate', 'course_aggregate', 'race_aggregate']
for schema in schemas:
    try:
        render_cur.execute(f"CREATE SCHEMA IF NOT EXISTS {schema};")
        render_conn.commit()
    except Exception as e:
        render_conn.rollback()

print("  ✅ Schemas created")
print()
print("📊 Migrating data (this will take 2-3 minutes)...")
print()

def copy_table_structure_and_data(schema, table, where_clause="", limit=None, order_by=""):
    """Copy table structure and data from local to Render"""
    try:
        # Get CREATE TABLE statement
        local_cur.execute(f"""
            SELECT
                'CREATE TABLE IF NOT EXISTS {schema}.{table} (' ||
                string_agg(
                    column_name || ' ' || data_type ||
                    CASE WHEN character_maximum_length IS NOT NULL
                        THEN '(' || character_maximum_length || ')'
                        ELSE ''
                    END ||
                    CASE WHEN is_nullable = 'NO' THEN ' NOT NULL' ELSE '' END,
                    ', '
                ) ||
                ');'
            FROM information_schema.columns
            WHERE table_schema = '{schema}' AND table_name = '{table}'
            GROUP BY table_schema, table_name;
        """)

        create_stmt = local_cur.fetchone()
        if not create_stmt:
            print(f"  ⚠️  Table {schema}.{table} not found, skipping")
            return

        # Create table on Render
        render_cur.execute(create_stmt[0])
        render_conn.commit()

        # Get column names
        local_cur.execute(f"""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = '{schema}' AND table_name = '{table}'
            ORDER BY ordinal_position;
        """)
        columns = [row[0] for row in local_cur.fetchall()]
        col_list = ', '.join([f'"{col}"' for col in columns])

        # Build query
        query = f'SELECT {col_list} FROM {schema}.{table}'
        if where_clause:
            query += f' WHERE {where_clause}'
        if order_by:
            query += f' ORDER BY {order_by}'
        if limit:
            query += f' LIMIT {limit}'

        # Copy data
        local_cur.execute(query)
        rows = local_cur.fetchall()

        if rows:
            placeholders = ', '.join(['%s'] * len(columns))
            insert_query = f'INSERT INTO {schema}.{table} ({col_list}) VALUES ({placeholders}) ON CONFLICT DO NOTHING'
            execute_batch(render_cur, insert_query, rows, page_size=500)
            render_conn.commit()
            print(f"  ✅ {schema}.{table}: {len(rows)} rows")
        else:
            print(f"  ⚠️  {schema}.{table}: no data found")

    except Exception as e:
        print(f"  ❌ {schema}.{table}: {e}")
        render_conn.rollback()

# Copy essential tables with recent data
print("1️⃣  Copying race data...")

# First copy race_details (has the date column)
copy_table_structure_and_data(
    'raw', 'race_details',
    where_clause="date >= '2024-01-01'",
    limit=1000,
    order_by='date DESC'
)

# Then copy fis_results (all results, will filter later if needed)
copy_table_structure_and_data(
    'raw', 'fis_results',
    limit=5000
)

print()
print("2️⃣  Copying athlete data...")
copy_table_structure_and_data(
    'athlete_aggregate', 'athlete_career_stats',
    limit=500
)

copy_table_structure_and_data(
    'athlete_aggregate', 'athlete_discipline_stats',
    limit=2000
)

print()
print("3️⃣  Copying course data...")
copy_table_structure_and_data(
    'course_aggregate', 'course_difficulty',
    limit=500
)

print()
print("✅ Migration complete!")
print()
print("🌐 Your application should now work at:")
print("   Frontend: https://fis-alpine-analytics.vercel.app")
print("   Backend: https://fis-alpine-analytics.onrender.com")
print()
print("Note: Data is limited to recent races for demo purposes")
print("Full data remains on your local machine")

# Close connections
local_cur.close()
local_conn.close()
render_cur.close()
render_conn.close()
