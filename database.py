import sqlite3
from datetime import datetime

def init_db():
    conn = sqlite3.connect('parking.db', timeout=20)
    cur = conn.cursor()
    cur.execute('''CREATE TABLE IF NOT EXISTS parking_spots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        branch_id TEXT, level TEXT, spot_id INTEGER,
        is_occupied INTEGER DEFAULT 0, is_reserved INTEGER DEFAULT 0,
        reserved_plate TEXT, reserved_size TEXT,
        booking_timestamp TEXT, plate_number TEXT, entry_time TEXT
    )''')
    cur.execute('''CREATE TABLE IF NOT EXISTS registry (plate_number TEXT PRIMARY KEY, status TEXT)''')
    cur.execute('''CREATE TABLE IF NOT EXISTS revenue_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT, branch_id TEXT,
        plate_number TEXT, amount REAL, checkout_time TEXT, duration TEXT
    )''')
    conn.commit()
    conn.close()

def cleanup_ghost_bookings(minutes=60):
    conn = sqlite3.connect('parking.db', timeout=20)
    cur = conn.cursor()
    try:
        cur.execute(f"""
            UPDATE parking_spots 
            SET is_reserved=0, reserved_plate=NULL, reserved_size=NULL, booking_timestamp=NULL 
            WHERE is_reserved=1 AND is_occupied=0 
            AND (strftime('%s','now') - strftime('%s', booking_timestamp)) > {minutes * 60}
        """)
        conn.commit()
    except: pass
    conn.close()

def ensure_branch_exists(branch_id):
    conn = sqlite3.connect('parking.db', timeout=20)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM parking_spots WHERE branch_id=?", (branch_id,))
    if cur.fetchone()[0] == 0:
        for lvl in ["Level 1", "Level 2", "Level 3"]:
            for i in range(1, 16):
                cur.execute("INSERT INTO parking_spots (branch_id, level, spot_id) VALUES (?, ?, ?)", (branch_id, lvl, i))
    conn.commit()
    conn.close()

def get_vehicle_status(plate):
    conn = sqlite3.connect('parking.db', timeout=20)
    cur = conn.cursor()
    cur.execute("SELECT status FROM registry WHERE plate_number=?", (plate,))
    res = cur.fetchone()
    conn.close()
    return res[0] if res else "Regular"

def add_to_registry(plate, status):
    conn = sqlite3.connect('parking.db', timeout=20)
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO registry (plate_number, status) VALUES (?, ?)", (plate, status))
    conn.commit()
    conn.close()

def get_vehicle(plate):
    conn = sqlite3.connect('parking.db', timeout=20)
    cur = conn.cursor()
    cur.execute("SELECT branch_id, level, spot_id, entry_time FROM parking_spots WHERE plate_number=?", (plate,))
    res = cur.fetchone()
    conn.close()
    return res

def insert_entry(branch_id, plate, lvl, spot, time, status):
    conn = sqlite3.connect('parking.db', timeout=20)
    cur = conn.cursor()
    cur.execute("""
        UPDATE parking_spots SET is_occupied=1, plate_number=?, entry_time=?, 
        is_reserved=0, reserved_plate=NULL, booking_timestamp=NULL 
        WHERE branch_id=? AND level=? AND spot_id=?
    """, (plate, time, branch_id, lvl, spot))
    conn.commit()
    conn.close()

def exit_vehicle(branch_id, plate, exit_time, amount, duration):
    conn = sqlite3.connect('parking.db', timeout=20)
    cur = conn.cursor()
    cur.execute("INSERT INTO revenue_history (branch_id, plate_number, amount, checkout_time, duration) VALUES (?,?,?,?,?)", (branch_id, plate, amount, exit_time, duration))
    cur.execute("UPDATE parking_spots SET is_occupied=0, plate_number=NULL, entry_time=NULL WHERE plate_number=?", (plate,))
    conn.commit()
    conn.close()

def get_all_spots(branch_id, lvl):
    conn = sqlite3.connect('parking.db', timeout=20)
    cur = conn.cursor()
    cur.execute("SELECT spot_id, is_occupied, plate_number, entry_time, is_reserved, reserved_plate FROM parking_spots WHERE branch_id=? AND level=?", (branch_id, lvl))
    res = cur.fetchall()
    conn.close()
    return res

def get_tier_availability(branch_id):
    conn = sqlite3.connect('parking.db', timeout=20)
    cur = conn.cursor()
    cur.execute("SELECT level FROM parking_spots WHERE branch_id=? AND is_occupied=0 AND is_reserved=0", (branch_id,))
    open_spots = [row[0] for row in cur.fetchall()]
    conn.close()
    return {
        "Small": len(open_spots),
        "Medium": len([l for l in open_spots if l != "Level 3"]),
        "Large": len([l for l in open_spots if l == "Level 1"]),
        "surge": 1.5 if len(open_spots) < 10 else 1.0
    }