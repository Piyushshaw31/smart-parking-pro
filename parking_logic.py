import sqlite3
from datetime import datetime
import math

def assign_slot(v_size, branch_id):
    """
    Search Hierarchy:
    Small:   Level 3 -> Level 2 -> Level 1
    Medium:  Level 2 -> Level 1 (Blocked from L3)
    Large:   Level 1 only (Blocked from L2/L3)
    """
    conn = sqlite3.connect('parking.db', timeout=20)
    cur = conn.cursor()
    
    if v_size == "Large":
        order = ["Level 1"]
    elif v_size == "Medium":
        order = ["Level 2", "Level 1"]
    else: # Small
        order = ["Level 3", "Level 2", "Level 1"]
        
    for lvl in order:
        cur.execute("""
            SELECT level, spot_id FROM parking_spots 
            WHERE branch_id=? AND level=? AND is_occupied=0 AND is_reserved=0 
            ORDER BY spot_id ASC LIMIT 1
        """, (branch_id, lvl))
        res = cur.fetchone()
        if res:
            conn.close()
            return res
            
    conn.close()
    return None, None

def calculate_bill(entry_time_str, plate):
    import database
    status = database.get_vehicle_status(plate)
    fmt = "%Y-%m-%d %H:%M:%S"
    entry_t = datetime.strptime(entry_time_str, fmt)
    exit_t = datetime.now()
    duration = exit_t - entry_t
    mins = duration.total_seconds() / 60
    dur_str = f"{int(mins // 60)}h {int(mins % 60)}m"
    
    if status == "VIP": 
        return exit_t.strftime(fmt), 0.0, dur_str
        
    billable = math.ceil(mins / 60)
    return exit_t.strftime(fmt), float(max(1, billable) * 30), dur_str