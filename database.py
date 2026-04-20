import sqlite3
import os
from datetime import datetime

# Forces the database to always save exactly where this Python file lives.
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'parking.db') 

def init_db():
    conn = sqlite3.connect(DB_PATH, timeout=20)
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
    conn = sqlite3.connect(DB_PATH, timeout=20)
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
    conn = sqlite3.connect(DB_PATH, timeout=20)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM parking_spots WHERE branch_id=?", (branch_id,))
    if cur.fetchone()[0] == 0:
        for lvl in ["Level 1", "Level 2", "Level 3"]:
            for i in range(1, 16):
                cur.execute("INSERT INTO parking_spots (branch_id, level, spot_id) VALUES (?, ?, ?)", (branch_id, lvl, i))
    conn.commit()
    conn.close()

def get_vehicle_status(plate):
    conn = sqlite3.connect(DB_PATH, timeout=20)
    cur = conn.cursor()
    cur.execute("SELECT status FROM registry WHERE plate_number=?", (plate,))
    res = cur.fetchone()
    conn.close()
    return res[0] if res else "Regular"

def add_to_registry(plate, status):
    conn = sqlite3.connect(DB_PATH, timeout=20)
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO registry (plate_number, status) VALUES (?, ?)", (plate, status))
    conn.commit()
    conn.close()

def get_vehicle(plate):
    conn = sqlite3.connect(DB_PATH, timeout=20)
    cur = conn.cursor()
    cur.execute("SELECT branch_id, level, spot_id, entry_time FROM parking_spots WHERE plate_number=?", (plate,))
    res = cur.fetchone()
    conn.close()
    return res

def insert_entry(branch_id, plate, lvl, spot, time, status):
    conn = sqlite3.connect(DB_PATH, timeout=20)
    cur = conn.cursor()
    
    cur.execute("""
        SELECT level, spot_id, booking_timestamp 
        FROM parking_spots 
        WHERE branch_id=? AND reserved_plate=? AND is_reserved=1
    """, (branch_id, plate))
    reservation = cur.fetchone()
    
    if reservation:
        old_lvl, old_spot, book_time = reservation
        
        if str(old_lvl) == str(lvl) and str(old_spot) == str(spot):
            cur.execute("""
                UPDATE parking_spots 
                SET is_occupied=1, plate_number=?, entry_time=?
                WHERE branch_id=? AND level=? AND spot_id=?
            """, (plate, time, branch_id, lvl, spot))
        else:
            cur.execute("""
                UPDATE parking_spots 
                SET is_reserved=0, reserved_plate=NULL, booking_timestamp=NULL
                WHERE branch_id=? AND level=? AND spot_id=?
            """, (branch_id, old_lvl, old_spot))
            
            cur.execute("""
                UPDATE parking_spots 
                SET is_occupied=1, plate_number=?, entry_time=?,
                    is_reserved=1, reserved_plate=?, booking_timestamp=?
                WHERE branch_id=? AND level=? AND spot_id=?
            """, (plate, time, plate, book_time, branch_id, lvl, spot))
            
    else:
        cur.execute("""
            UPDATE parking_spots 
            SET is_occupied=1, plate_number=?, entry_time=?, 
            is_reserved=0, reserved_plate=NULL, booking_timestamp=NULL 
            WHERE branch_id=? AND level=? AND spot_id=?
        """, (plate, time, branch_id, lvl, spot))
        
    conn.commit()
    conn.close()

def exit_vehicle(branch_id, plate, exit_time, amount, duration, guard_override=False):
    """
    UPGRADED: Blacklisted cars are blocked from exiting.
    If the guard sets guard_override=True, the system allows the exit 
    but automatically adds a 100 Rs penalty to the revenue log.
    """
    conn = sqlite3.connect(DB_PATH, timeout=20)
    cur = conn.cursor()
    
    # 1. SECURITY CHECK: Is this car blacklisted?
    cur.execute("SELECT status FROM registry WHERE plate_number=?", (plate,))
    res = cur.fetchone()
    status = res[0] if res else "Regular"
    
    if status.lower() == "blacklisted":
        if not guard_override:
            # First attempt: Block the exit immediately.
            conn.close()
            return False  
        else:
            # Second attempt: Guard authorized the release. Add 100 Rs Fine.
            amount += 100
        
    # 2. NORMAL CHECKOUT: Car is safe to leave
    cur.execute("INSERT INTO revenue_history (branch_id, plate_number, amount, checkout_time, duration) VALUES (?,?,?,?,?)", (branch_id, plate, amount, exit_time, duration))
    
    cur.execute("""
        UPDATE parking_spots 
        SET is_occupied=0, plate_number=NULL, entry_time=NULL,
        is_reserved=0, reserved_plate=NULL, booking_timestamp=NULL
        WHERE plate_number=?
    """, (plate,))
    
    conn.commit()
    conn.close()
    return True

def get_all_spots(branch_id, lvl):
    conn = sqlite3.connect(DB_PATH, timeout=20)
    cur = conn.cursor()
    cur.execute("SELECT spot_id, is_occupied, plate_number, entry_time, is_reserved, reserved_plate FROM parking_spots WHERE branch_id=? AND level=?", (branch_id, lvl))
    res = cur.fetchall()
    conn.close()
    return res

def get_tier_availability(branch_id):
    conn = sqlite3.connect(DB_PATH, timeout=20)
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

def check_active_reservation(branch_id, plate):
    conn = sqlite3.connect(DB_PATH, timeout=20)
    cur = conn.cursor()
    cur.execute("""
        SELECT level, spot_id 
        FROM parking_spots 
        WHERE branch_id=? AND reserved_plate=? AND is_reserved=1 AND is_occupied=0
    """, (branch_id, plate))
    res = cur.fetchone()
    conn.close()
    return res 

def smart_gate_entry(branch_id, plate, fallback_lvl, fallback_spot, time, status):
    reservation = check_active_reservation(branch_id, plate)
    
    conn = sqlite3.connect(DB_PATH, timeout=20)
    cur = conn.cursor()
    
    if reservation:
        target_lvl, target_spot = reservation
        is_prebooked = True
        cur.execute("""
            UPDATE parking_spots 
            SET is_occupied=1, plate_number=?, entry_time=?
            WHERE branch_id=? AND level=? AND spot_id=?
        """, (plate, time, branch_id, target_lvl, target_spot))
    else:
        target_lvl, target_spot = fallback_lvl, fallback_spot
        is_prebooked = False
        cur.execute("""
            UPDATE parking_spots 
            SET is_occupied=1, plate_number=?, entry_time=?,
            is_reserved=0, reserved_plate=NULL, booking_timestamp=NULL
            WHERE branch_id=? AND level=? AND spot_id=?
        """, (plate, time, branch_id, target_lvl, target_spot))

    conn.commit()
    conn.close()
    
    return is_prebooked, target_lvl, target_spot