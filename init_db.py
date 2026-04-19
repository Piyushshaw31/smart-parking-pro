import sqlite3

def factory_reset():
    conn = sqlite3.connect('parking.db')
    cursor = conn.cursor()

    # THE NUCLEAR OPTION: Destroy old messy tables if they exist
    cursor.execute("DROP TABLE IF EXISTS parking_records")
    cursor.execute("DROP TABLE IF EXISTS parking_spots")

    # 1. Create fresh Records Table
    cursor.execute('''
        CREATE TABLE parking_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plate_number TEXT NOT NULL,
            level TEXT NOT NULL,
            spot_id INTEGER NOT NULL,
            entry_time TEXT NOT NULL,
            exit_time TEXT,
            total_amount REAL
        )
    ''')

    # 2. Create fresh Spots Table with PRIMARY KEY safety
    cursor.execute('''
        CREATE TABLE parking_spots (
            spot_id INTEGER,
            level TEXT,
            is_occupied INTEGER DEFAULT 0,
            PRIMARY KEY (spot_id, level)
        )
    ''')

    # 3. Create exactly 15 spots per level (Total 45)
    levels = ["Level 1", "Level 2", "Level 3"]
    for lvl in levels:
        for spot in range(1, 16):
            cursor.execute("INSERT INTO parking_spots (spot_id, level, is_occupied) VALUES (?, ?, 0)", (spot, lvl))

    conn.commit()
    conn.close()
    print("✨ SUCCESS: The database has been cleaned and reset to 45 perfect spots.")

if __name__ == "__main__":
    factory_reset()