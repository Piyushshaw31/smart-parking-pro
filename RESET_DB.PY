import sqlite3

conn = sqlite3.connect('smart_parking.db')
cursor = conn.cursor()

cursor.execute("DROP TABLE IF EXISTS parking_spots")
cursor.execute("DROP TABLE IF EXISTS parking_logs")

# Create fresh tables
cursor.execute("""
CREATE TABLE parking_spots (
    level INTEGER,
    spot_number INTEGER,
    occupied INTEGER
)
""")

cursor.execute("""
CREATE TABLE parking_logs (
    plate_number TEXT PRIMARY KEY,
    parking_level INTEGER,
    spot_number INTEGER
)
""")

# Insert parking spots
for level in range(1, 4):
    for spot in range(1, 11):
        cursor.execute(
            "INSERT INTO parking_spots VALUES (?, ?, 0)",
            (level, spot)
        )

conn.commit()
conn.close()

print("✅ Database Reset Complete")