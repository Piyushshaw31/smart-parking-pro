import streamlit as st
import pytz
import math
import cv2
import numpy as np
import sqlite3
import requests
import qrcode
import io
import re
import pandas as pd
import plotly.express as px
import base64
from datetime import datetime
from fuzzywuzzy import process
from PIL import Image, ImageDraw
import database, ocr, parking_logic

# --- CORE SETUP ---
st.set_page_config(layout="wide", page_title="Smart Parking Pro", page_icon="🚥")
database.init_db()
database.cleanup_ghost_bookings(minutes=30) 

# --- SESSION STATE MANAGEMENT ---
if 'blocked_plate' not in st.session_state:
    st.session_state['blocked_plate'] = None
if 'checkout_amount' not in st.session_state:
    st.session_state['checkout_amount'] = 0

try:
    with open('style.css') as f: 
        st.markdown(f'<style>{f.read()}</style>', unsafe_allow_html=True)
except: 
    pass

# --- SESSION STATE INITIALIZATION ---
keys = [
    "role", "managed_branch_id", "managed_branch_name", "entry_ocr", "exit_ocr", 
    "entry_result", "exit_result", "selected_branch_id", "live_branches", 
    "last_processed_id", "last_processed_exit_id", "trigger_balloons"
]
for key in keys:
    if key not in st.session_state: 
        if key in ["live_branches"]: st.session_state[key] = []
        elif key in ["trigger_balloons"]: st.session_state[key] = False
        else: st.session_state[key] = ""

if st.session_state.trigger_balloons:
    st.balloons()
    st.session_state.trigger_balloons = False

# --- CORE HELPERS ---

@st.cache_data(ttl=3600)
def fetch_nearby_branches(search_loc="Tarakeswar, West Bengal"):
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        geo_res = requests.get(f"https://nominatim.openstreetmap.org/search?q={search_loc}&format=json&limit=1", headers=headers, timeout=5).json()
        lat, lon = geo_res[0]['lat'], geo_res[0]['lon']
        query = f"[out:json];(node['amenity'='parking'](around:3000,{lat},{lon});way['amenity'='parking'](around:3000,{lat},{lon}););out center 5;"
        osm_data = requests.get("https://overpass-api.de/api/interpreter", params={'data': query}, headers=headers, timeout=10).json()
        return [{"id": f"R_{i}", "name": e.get('tags',{}).get('name', f"Zone {i+1}"), "dist": f"{round(0.5+i*0.2, 1)} km", "prices": {"Small": 20, "Medium": 30, "Large": 50}} for i, e in enumerate(osm_data['elements'])]
    except:
        return [{"id": "F1", "name": "Rajbari Smart Parking", "dist": "0.3 km", "prices": {"Small": 20, "Medium": 30, "Large": 50}},
                {"id": "F2", "name": "Post Office Road Zone", "dist": "0.8 km", "prices": {"Small": 15, "Medium": 25, "Large": 40}}]

if not st.session_state.live_branches:
    st.session_state.live_branches = fetch_nearby_branches()

def is_valid_plate(p):
    return re.match(r'^[A-Z]{2}[0-9]{2}[A-Z]{0,2}[0-9]{4}$', p) is not None

def find_best_plate_match(scanned, bid, mode="all"):
    if not scanned: return ""
    with sqlite3.connect('parking.db', timeout=20) as conn:
        cur = conn.cursor()
        active = []
        if mode in ["all", "res"]:
            cur.execute("SELECT reserved_plate FROM parking_spots WHERE branch_id=? AND is_reserved=1", (bid,))
            active.extend([row[0] for row in cur.fetchall() if row[0]])
        if mode in ["all", "occ"]:
            cur.execute("SELECT plate_number FROM parking_spots WHERE branch_id=? AND is_occupied=1", (bid,))
            active.extend([row[0] for row in cur.fetchall() if row[0]])
    if not active: return scanned
    match, score = process.extractOne(scanned, active)
    return match if score > 85 else scanned

def render_thermal_ticket(title, branch, plate, level, spot, time_val, extra=""):
    qr = qrcode.make(f"TICKET:{plate}"); buf = io.BytesIO(); qr.save(buf, format="PNG")
    qr_b64 = base64.b64encode(buf.getvalue()).decode()
    st.markdown(f"""
    <div class="thermal-ticket">
        <div class="ticket-header">🅿️ {branch}</div>
        <div class="ticket-title">*** {title} ***</div>
        <div style="text-align:center; margin-bottom:15px;"><img src="data:image/png;base64,{qr_b64}" width="140"></div>
        <b>PLATE:</b> {plate}<br><b>ZONE:</b> {level}<br><b>SPOT:</b> {spot}<br><b>TIME:</b> {time_val}
        <div style="margin-top:10px; border-top:1px dashed #222; padding-top:10px; font-size:0.85rem;">{extra}</div>
        <div style="text-align:center; margin-top:10px; font-size:0.7rem;">Drive Safely</div>
    </div>
    """, unsafe_allow_html=True)

def generate_ticket_download(title, branch, plate, level, spot, time_val, extra=""):
    img = Image.new('RGB', (400, 750), (255,255,255)); d = ImageDraw.Draw(img)
    clean_extra = str(extra).replace('<br>', '\n').replace('<b>','').replace('</b>','')
    content = f"{branch}\n*** {title} ***\n\nPLATE: {plate}\nZONE: {level}\nSPOT: {spot}\nTIME: {time_val}\n\n{clean_extra}\n\nDrive Safely."
    d.multiline_text((40, 40), content, fill=(0,0,0))
    qr = qrcode.make(f"TICKET:{plate}"); img.paste(qr.resize((150, 150)), (125, 520))
    buf = io.BytesIO(); img.save(buf, format="PNG")
    return buf.getvalue()

def render_svg_map(spots):
    h_map = "<div class='map-grid'>"
    for s_id, occ, p, t, res, rp in spots:
        style_override = ""
        if occ:
            if p == 'BLOCKED':
                status_class = "spot-empty"
                label = f"SPOT {s_id}<br><br><span style='color: #ef4444; font-weight: 900; font-size: 1.2rem; letter-spacing: 2px;'>BLOCKED</span>"
                style_override = "background: #451a1e; border: 2px solid #ef4444; box-shadow: 0 0 15px rgba(239, 68, 68, 0.4);"
            else:
                status_class = "spot-occupied"
                label = f"SPOT {s_id}<br><span style='font-size: 1.6rem;'>🚗</span><br><span class='spot-plate'>{p}</span>"
        elif res:
            status_class = "spot-reserved"
            label = f"SPOT {s_id}<br><span style='font-size: 1.6rem;'>⏳</span><br><span class='spot-plate'>{rp}</span>"
        else:
            status_class = "spot-empty"
            label = f"Spot {s_id}"
            
        if style_override:
            h_map += f"<div class='spot-card {status_class}' style='{style_override}'>{label}</div>"
        else:
            h_map += f"<div class='spot-card {status_class}'>{label}</div>"
    h_map += "</div>"
    
    st.markdown(h_map, unsafe_allow_html=True)

# --- USER PORTALS ---

def render_entry_gate(bid, bname):
    st.markdown(f"<div class='header-style'>🚗 {bname} - Admission</div>", unsafe_allow_html=True)
    c1, c2 = st.columns([1, 1.2], gap="large")
    
    with c1:
        img = st.camera_input("Scan Entry Plate", key=f"en_cam_{bid}")
        if img:
            img_hash = hash(img.getvalue())
            if st.session_state.last_processed_id != img_hash:
                with st.spinner("AI enhancing and analyzing plate..."):
                    frame = cv2.imdecode(np.frombuffer(img.getvalue(), np.uint8), 1)
                    st.session_state.entry_ocr = find_best_plate_match(ocr.detect_text(frame), bid, mode="res")
                    st.session_state.last_processed_id = img_hash
                    st.rerun()
                
    with c2:
        val = (st.session_state.entry_ocr or "")
        final_p = st.text_input("Validated Plate", value=val).upper().strip()
        actual_sz = st.radio("Physical Size (Guard's Decision)", ["Small", "Medium", "Large"], horizontal=True)
        
        if st.button("Proceed to Admit", type="primary", use_container_width=True):
            if not is_valid_plate(final_p): 
                st.error("❌ Invalid Format. Please correct the plate manually.")
            else:
                status = database.get_vehicle_status(final_p)
                if status == "Blacklist":
                    st.error(f"🚫 ALERT: ENTRY DENIED! Vehicle {final_p} is on the Blacklist.")
                else:
                    with sqlite3.connect('parking.db', timeout=20) as conn:
                        cur = conn.cursor()
                        cur.execute("SELECT level, spot_id FROM parking_spots WHERE plate_number=? AND is_occupied=1", (final_p,))
                        already = cur.fetchone()
                    
                    if already: 
                        st.error(f"🚨 Denied: Vehicle {final_p} is already inside at {already[0]}, Spot {already[1]}.")
                    else:
                        with sqlite3.connect('parking.db', timeout=20) as conn:
                            cur = conn.cursor()
                            cur.execute("SELECT level, spot_id, reserved_size FROM parking_spots WHERE branch_id=? AND reserved_plate=?", (bid, final_p))
                            resv = cur.fetchone()

                        IST = pytz.timezone('Asia/Kolkata')
                        now = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
                        
                        if resv:
                            r_lvl, r_spot, r_sz = resv
                            if actual_sz == r_sz:
                                database.insert_entry(bid, final_p, r_lvl, r_spot, now, "Regular")
                                st.session_state.entry_result = (final_p, r_lvl, r_spot, now, "RESERVATION VERIFIED")
                                st.session_state.exit_result = "" 
                                st.session_state.trigger_balloons = True
                            else:
                                st.warning(f"📐 Override: Reserved as {r_sz}, physically {actual_sz}. Re-calculating spot...")
                                with sqlite3.connect('parking.db', timeout=20) as conn:
                                    conn.execute("UPDATE parking_spots SET is_reserved=0, reserved_plate=NULL WHERE reserved_plate=?", (final_p,))
                                    conn.commit()
                                
                                new_lvl, new_spot = parking_logic.assign_slot(actual_sz, bid)
                                if new_lvl:
                                    database.insert_entry(bid, final_p, new_lvl, new_spot, now, "Regular")
                                    st.session_state.entry_result = (final_p, new_lvl, new_spot, now, "RE-ASSIGNED (SIZE OVERRIDE)")
                                    st.session_state.exit_result = "" 
                                    st.session_state.trigger_balloons = True
                                else: 
                                    st.error(f"No spots available for {actual_sz}!")
                        else:
                            lvl, spot = parking_logic.assign_slot(actual_sz, bid)
                            if lvl:
                                database.insert_entry(bid, final_p, lvl, spot, now, "Regular")
                                st.session_state.entry_result = (final_p, lvl, spot, now, "WALK-IN OK")
                                st.session_state.exit_result = "" 
                                st.session_state.trigger_balloons = True
                            else: 
                                st.error("Facility is full for this vehicle size!")
                    st.rerun()

    if st.session_state.entry_result:
        p, l, s, t, msg = st.session_state.entry_result
        render_thermal_ticket(msg, bname, p, l, s, t)
        dl_ticket = generate_ticket_download(msg, bname, p, l, s, t)
        st.download_button("📥 Download Ticket", dl_ticket, f"Entry_{p}.png", use_container_width=True)
        if st.button("Clear for Next Vehicle", use_container_width=True): 
            st.session_state.entry_result = ""
            st.session_state.entry_ocr = ""
            st.rerun()

    st.markdown("<br><div class='header-style' style='font-size: 1.5rem; border-bottom: 2px solid #3b82f6;'>🗺️ Live Floor Map</div>", unsafe_allow_html=True)
    lvl_en = st.selectbox("Select Floor to View", ["Level 1", "Level 2", "Level 3"], key=f"en_floor_map_{bid}")
    spots_en = database.get_all_spots(bid, lvl_en)
    render_svg_map(spots_en)


def render_exit_gate(bid, bname):
    st.markdown(f"<div class='header-style'>🧾 {bname} - Checkout</div>", unsafe_allow_html=True)
    c1, c2 = st.columns([1, 1.2], gap="large")
    
    with c1:
        img = st.camera_input("Scan Exit Plate", key=f"ex_cam_{bid}")
        if img:
            img_hash = hash(img.getvalue())
            if st.session_state.last_processed_exit_id != img_hash:
                with st.spinner("AI finding vehicle..."):
                    frame = cv2.imdecode(np.frombuffer(img.getvalue(), np.uint8), 1)
                    st.session_state.exit_ocr = find_best_plate_match(ocr.detect_text(frame), bid, mode="occ")
                    st.session_state.last_processed_exit_id = img_hash
                    st.rerun()
                
    with c2:
        val = (st.session_state.exit_ocr or "")
        plate = st.text_input("Exit Plate Number", value=val).upper().strip()
        if st.button("Calculate Final Bill", type="primary", use_container_width=True):
            if not plate:
                st.error("Please enter a plate number.")
            else:
                vehicle_data = database.get_vehicle(plate)
                
                if vehicle_data:
                    branch, lvl, spot, entry_time = vehicle_data
                    
                    # Calculate basic dummy duration and amount
                    # --- DYNAMIC TIME & RATE CALCULATION ---
                    
                    exit_time_dt = datetime.now()
                    exit_time = exit_time_dt.strftime("%Y-%m-%d %H:%M:%S")
                    
                    try:
                        # Convert entry time from string to a real datetime object
                        entry_time_dt = datetime.strptime(entry_time, "%Y-%m-%d %H:%M:%S")
                        
                        # Calculate the exact difference in seconds
                        time_diff = exit_time_dt - entry_time_dt
                        total_seconds = time_diff.total_seconds()
                        
                        # Format the exact duration for the receipt (e.g., "2h 15m")
                        h = int(total_seconds // 3600)
                        m = int((total_seconds % 3600) // 60)
                        duration = f"{h}h {m}m"
                        
                        # Calculate billable hours (rounds UP to the nearest whole hour)
                        # Example: 1 hr 5 mins becomes 2 billable hours.
                        billable_hours = math.ceil(total_seconds / 3600)
                        
                        # Ensure we charge for at least 1 hour even if they just entered
                        if billable_hours < 1:
                            billable_hours = 1
                            
                        # Set your hourly rate (₹30 for Medium)
                        hourly_rate = 30.0 
                        
                        # Final dynamic amount!
                        base_amount = float(billable_hours * hourly_rate)
                        
                    except Exception as e:
                        # Fallback just in case the database date format is weird
                        base_amount = 50.0
                        duration = "Unknown Time"
                    # ----------------------------------------
                    
                    # --- FIX 1: EXPLICITLY CHECK BLACKLIST STATUS FIRST ---
                    status = database.get_vehicle_status(plate)
                    
                    if status == "Blacklist":
                        # SECURITY CHECKPOINT TRIGGERED!
                        st.session_state['blocked_plate'] = plate
                        st.session_state['checkout_amount'] = base_amount
                        st.rerun() # Force a UI refresh to show the red warning
                    else:
                        # Attempt standard checkout for normal/VIP cars
                        success = database.exit_vehicle(bid, plate, exit_time, base_amount, duration, guard_override=False)
                        
                        if success:
                            st.success(f"Checkout Complete for {plate}. Amount Paid: ₹{base_amount}")
                            # --- FIX 2 & 3: SET EXIT RESULT FOR BILL & TRIGGER BALLOONS ---
                            st.session_state.exit_result = (plate, lvl, spot, entry_time, exit_time, base_amount, duration)
                            st.session_state.trigger_balloons = True
                            st.rerun() 
                        else:
                            st.error("Error processing checkout in database.")
                else:
                    st.warning("Vehicle not found in the active parking database.")
        
        # --- THE GUARD OVERRIDE UI ---
        if st.session_state['blocked_plate'] == plate and plate != "":
            st.error(f"🚨 **SECURITY ALERT: VEHICLE {plate} IS BLACKLISTED!** 🚨\n\nExit Denied. The gate will not open.")
            st.warning("Admin/Guard Override Required to release vehicle.")
            
            if st.button("Guard Override: Release & Add ₹100 Fine", type="primary"):
                exit_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                new_total = st.session_state['checkout_amount'] + 100
                
                # Fetch vehicle data again to populate the receipt properly
                vehicle_data = database.get_vehicle(plate)
                lvl, spot, entry_time = "N/A", "N/A", "N/A"
                if vehicle_data:
                    _, lvl, spot, entry_time = vehicle_data

                # Attempt checkout again, passing the new total and guard_override=True
                database.exit_vehicle(
                    bid, 
                    plate, 
                    exit_time, 
                    new_total, 
                    "2 hours", 
                    guard_override=True
                )
                
                st.success(f"Override Accepted. Vehicle Released. Total collected: ₹{new_total} (includes ₹100 fine).")
                
                # --- FIX 2 & 3: GENERATE RECEIPT AND BALLOONS FOR BLACKLISTED CARS TOO ---
                st.session_state.exit_result = (plate, lvl, spot, entry_time, exit_time, new_total, "2 hours")
                st.session_state.trigger_balloons = True
                
                # Clear the session state so the red box goes away
                st.session_state['blocked_plate'] = None
                st.rerun()

    # --- THIS BLOCK RENDERS THE BILL ---
    if st.session_state.exit_result:
        p, lvl, spot, entry_t, exit_t, a, d = st.session_state.exit_result
        
        extra_details = f"Entry Time: {entry_t}<br>Duration: {d}<br><b>Total Paid: ₹{a}</b>"
        
        render_thermal_ticket("EXIT RECEIPT", bname, p, lvl, spot, exit_t, extra_details)
        dl_receipt = generate_ticket_download("EXIT RECEIPT", bname, p, lvl, spot, exit_t, extra_details)
        st.download_button("📥 Download Receipt", dl_receipt, f"Exit_{p}.png", use_container_width=True)
        if st.button("Ready for Next Exit", use_container_width=True): 
            st.session_state.exit_result = ""
            st.session_state.exit_ocr = ""
            st.rerun()

    st.markdown("<br><div class='header-style' style='font-size: 1.5rem; border-bottom: 2px solid #3b82f6;'>🗺️ Live Floor Map</div>", unsafe_allow_html=True)
    lvl_ex = st.selectbox("Select Floor to View", ["Level 1", "Level 2", "Level 3"], key=f"ex_floor_map_{bid}")
    spots_ex = database.get_all_spots(bid, lvl_ex)
    render_svg_map(spots_ex)


def render_security(bid, bname):
    st.markdown("<br>", unsafe_allow_html=True)
    mode = st.radio("🚦 Select Gate Operation", ["🚗 Entry Gate", "🧾 Exit Gate"], horizontal=True, key=f"sec_mode_{bid}")
    
    if "Entry" in mode:
        render_entry_gate(bid, bname)
    else:
        render_exit_gate(bid, bname)


def render_admin(bid, bname):
    st.markdown("<br>", unsafe_allow_html=True)
    admin_mode = st.radio("⚙️ Admin Dashboard Menu", ["📈 Analytics & Revenue", "🗺️ Manual Floor Control", "🚦 Remote Gates", "📒 Registry & Search"], horizontal=True, key=f"admin_mode_{bid}")
    
    if admin_mode == "📈 Analytics & Revenue":
        st.markdown("### 💰 Financial Dashboard")
        with sqlite3.connect('parking.db', timeout=20) as conn:
            df = pd.read_sql_query("SELECT * FROM revenue_history WHERE branch_id=?", conn, params=(bid,))
            
        if not df.empty:
            df['checkout_time'] = pd.to_datetime(df['checkout_time'])
            
            df['Date'] = df['checkout_time'].dt.date.astype(str)
            df['Week'] = df['checkout_time'].dt.to_period('W').astype(str)
            df['Month'] = df['checkout_time'].dt.to_period('M').astype(str)
            df['Year'] = df['checkout_time'].dt.year.astype(str)
            
            total_rev = df['amount'].sum()
            st.metric(label="Total Lifetime Revenue", value=f"₹{total_rev:,.2f}")
            
            view_mode = st.radio("Select View:", ["Daily", "Weekly", "Monthly", "Yearly"], horizontal=True)
            
            if view_mode == "Daily":
                chart_df = df.groupby('Date')['amount'].sum().reset_index()
                fig = px.bar(chart_df, x='Date', y='amount', title="Daily Revenue Trend", text_auto=True)
                fig.update_traces(marker_color='#10b981', width=0.3)
            elif view_mode == "Weekly":
                chart_df = df.groupby('Week')['amount'].sum().reset_index()
                fig = px.bar(chart_df, x='Week', y='amount', title="Weekly Revenue", text_auto=True)
                fig.update_traces(marker_color='#3b82f6', width=0.3)
            elif view_mode == "Monthly":
                chart_df = df.groupby('Month')['amount'].sum().reset_index()
                fig = px.bar(chart_df, x='Month', y='amount', title="Monthly Revenue", text_auto=True)
                fig.update_traces(marker_color='#8b5cf6', width=0.3)
            else:
                chart_df = df.groupby('Year')['amount'].sum().reset_index()
                fig = px.bar(chart_df, x='Year', y='amount', title="Yearly Revenue", text_auto=True)
                fig.update_traces(marker_color='#6366f1', width=0.3)
            
            fig.update_layout(xaxis_type='category')
            st.plotly_chart(fig, use_container_width=True)
            
            col1, col2 = st.columns([1, 1], vertical_alignment="center")
            with col1:
                st.info("💡 **To download the Graph above:** Hover over the top-right corner of the chart and click the **Camera Icon (Download plot as png)**.")
            with col2:
                clean_df = df[['plate_number', 'checkout_time', 'duration', 'amount']].sort_values(by="checkout_time", ascending=False)
                csv_data = clean_df.to_csv(index=False)
                date_str = datetime.now().strftime('%Y-%m-%d')
                
                st.download_button(
                    label="📥 Download Raw Financial Data (CSV)",
                    data=csv_data,
                    file_name=f"Revenue_Report_{bname}_{date_str}.csv",
                    mime="text/csv",
                    use_container_width=True
                )
            
            with st.expander("Show Raw Data Table"):
                st.dataframe(clean_df, use_container_width=True)
        else: 
            st.info("No transaction data available yet.")

    elif admin_mode == "🗺️ Manual Floor Control":
        st.markdown("### 🗺️ Live Facility Map")
        lvl = st.selectbox("Floor Selection", ["Level 1", "Level 2", "Level 3"], key="admin_floor_map")
        spots = database.get_all_spots(bid, lvl)
        render_svg_map(spots)
        
        st.markdown("### 🔧 Manual Spot Override")
        st.caption("Use this to manually block broken spots or force-free a spot if a car left without checking out.")
        
        col1, col2, col3 = st.columns([1, 1, 1], vertical_alignment="bottom")
        override_spot = col1.selectbox("Select Spot ID", range(1, 16))
        action = col2.selectbox("Action", ["Free Spot", "Mark Occupied (Block)"])
        
        if col3.button("Apply Override", type="primary", use_container_width=True):
            with sqlite3.connect('parking.db', timeout=20) as conn:
                if action == "Free Spot":
                    conn.execute("UPDATE parking_spots SET is_occupied=0, plate_number=NULL, is_reserved=0, reserved_plate=NULL WHERE branch_id=? AND level=? AND spot_id=?", (bid, lvl, override_spot))
                else:
                    conn.execute("UPDATE parking_spots SET is_occupied=1, plate_number='BLOCKED', is_reserved=0, reserved_plate=NULL WHERE branch_id=? AND level=? AND spot_id=?", (bid, lvl, override_spot))
                conn.commit()
            st.success(f"Spot {override_spot} on {lvl} updated successfully!")
            st.rerun()

    elif admin_mode == "🚦 Remote Gates":
        render_security(bid, bname)

    elif admin_mode == "📒 Registry & Search":
    
        st.markdown("### 📊 Pre-Booking Analytics")

        with sqlite3.connect('parking.db', timeout=20) as conn:
            cursor = conn.cursor()

            # Total pre-booked vehicles
            cursor.execute("""
                SELECT COUNT(*) FROM parking_spots 
                WHERE branch_id=? AND is_reserved=1 AND reserved_plate IS NOT NULL
            """, (bid,))
        
            total_prebooked = cursor.fetchone()[0]

            st.metric("🚗 Total Pre-Booked Vehicles", total_prebooked)

            # Fetch all pre-booked vehicles
            df_prebook = pd.read_sql_query("""
                SELECT level as 'Level', 
                    spot_id as 'Spot', 
                    reserved_plate as 'Plate', 
                    reserved_size as 'Size',
                    booking_timestamp as 'Booking Time'
                FROM parking_spots
                WHERE branch_id=? AND is_reserved=1
            """, conn, params=(bid,))

        if not df_prebook.empty:
            st.success("📋 Pre-Booked Vehicles List")
            st.dataframe(df_prebook, use_container_width=True, hide_index=True)
        else:
            st.info("No active pre-bookings.")

        st.markdown("### 📅 Filter Pre-Bookings by Date")

        selected_date = st.date_input("Select Booking Date")

        if selected_date:
            filtered_df = df_prebook[
                pd.to_datetime(df_prebook['Booking Time']).dt.date == selected_date
            ]

            st.write(f"Bookings on {selected_date}: {len(filtered_df)}")
            st.dataframe(filtered_df, use_container_width=True)

        st.markdown("### 🔍 Search Active Vehicles")
        st.caption("Look up currently parked cars by plate or filter by their entry date.")
        
        sc1, sc2 = st.columns(2, vertical_alignment="bottom")
        search_plate = sc1.text_input("Search Plate Number (Optional)").upper().strip()
        use_date = sc2.checkbox("Filter by Entry Date")
        
        search_date = None
        if use_date:
            search_date = sc2.date_input("Select Date")
        
        query = "SELECT level as 'Zone', spot_id as 'Spot', plate_number as 'Plate', entry_time as 'Entry Time', booking_timestamp FROM parking_spots WHERE branch_id=? AND is_occupied=1"
        params = [bid]
        
        if search_plate:
            query += " AND plate_number LIKE ?"
            params.append(f"%{search_plate}%")
            
        if use_date and search_date:
            query += " AND date(entry_time) = ?"
            params.append(search_date.strftime("%Y-%m-%d"))
            
        with sqlite3.connect('parking.db', timeout=20) as conn:
            search_df = pd.read_sql_query(query, conn, params=params)
            
        if not search_df.empty:
            search_df['Pre-Booked?'] = search_df['booking_timestamp'].notna().apply(lambda x: "Yes" if x else "No")
            search_df = search_df.drop(columns=['booking_timestamp'])
            st.dataframe(search_df, use_container_width=True, hide_index=True)
        else:
            st.info("No vehicles currently parked match your search criteria.")
            
        st.markdown("---")
        
        st.markdown("### ➕ Manage Vehicle Status (VIP/Blacklist)")
        c1, c2, c3, c4 = st.columns([2, 1, 1, 1], vertical_alignment="bottom")
        p = c1.text_input("Enter Plate Number", key="reg_plate_input").upper().strip()
        s = c2.selectbox("Assign Status", ["VIP", "Blacklist"])
        
        if c3.button("Save Record", type="primary", use_container_width=True):
            if not is_valid_plate(p):
                st.error("Invalid Plate Format.")
            else:
                database.add_to_registry(p, s)
                st.success(f"Plate {p} registered as {s}.")
                st.rerun()
                
        # --- NEW: Delete Button Added Here ---
        if c4.button("🗑️ Remove", use_container_width=True):
            if not p:
                st.error("Please enter a Plate Number to remove.")
            else:
                with sqlite3.connect('parking.db', timeout=20) as conn:
                    cur = conn.cursor()
                    cur.execute("DELETE FROM registry WHERE plate_number=?", (p,))
                    if cur.rowcount > 0:
                        st.success(f"Plate {p} has been removed from the registry.")
                    else:
                        st.warning(f"Plate {p} was not found in the registry.")
                    conn.commit()
                st.rerun()
                
        st.markdown("### 📋 Current Registry List")
        with sqlite3.connect('parking.db', timeout=20) as conn:
            reg_df = pd.read_sql_query("SELECT plate_number as 'Plate Number', status as 'Status' FROM registry", conn)
            
        if not reg_df.empty:
            st.dataframe(reg_df, use_container_width=True, hide_index=True)
        else:
            st.info("The registry is currently empty. Add VIP or Blacklist vehicles above.")


def render_customer():
    st.markdown("<div class='header-style'>🌍 Real-Time Radar Finder</div>", unsafe_allow_html=True)
    c_s, c_b = st.columns([3, 1], vertical_alignment="bottom")
    loc = c_s.text_input("Destination City or Area", value="Tarakeswar, West Bengal")
    
    if c_b.button("Scan Map", use_container_width=True, type="primary"):
        st.session_state.live_branches = fetch_nearby_branches(loc)
        st.rerun()
    
    if not st.session_state.selected_branch_id:
        for b in st.session_state.live_branches:
            database.ensure_branch_exists(b['id'])
            avail = database.get_tier_availability(b['id'])
            st.markdown(f"""
            <div class='branch-card'>
                <span class='branch-title'>{b['name']}</span><br>
                🚗 <b>{avail['Small']}</b> Small | 🚙 <b>{avail['Medium']}</b> Medium | 🚛 <b>{avail['Large']}</b> Large spots free
            </div>
            """, unsafe_allow_html=True)
            if st.button(f"Select {b['name']}", key=f"sel_{b['id']}", use_container_width=True): 
                st.session_state.selected_branch_id = b['id']
                st.rerun()
    else:
        b = next(i for i in st.session_state.live_branches if i['id'] == st.session_state.selected_branch_id)
        if st.button("⬅️ Back to Map"): 
            st.session_state.selected_branch_id = ""
            st.rerun()
        
        st.markdown(f"## {b['name']}")
        tb, tl = st.tabs(["✨ Reserve a Spot", "🔍 Locate My Car"])
        with tb:
            bc1, bc2 = st.columns(2, vertical_alignment="bottom")
            p = bc1.text_input("Vehicle Plate Number").upper().strip()
            sz = bc2.selectbox("Vehicle Size", ["Small", "Medium", "Large"])
            st.info(f"💰 Hourly Rate: ₹{b['prices'].get(sz, 30)}")
            
            if st.button("Confirm Live Booking", type="primary", use_container_width=True):
                if not is_valid_plate(p): 
                    st.error("Invalid Plate Format. Use format like WB12AB1234")
                else:
                    status = database.get_vehicle_status(p)
                    if status == "Blacklist":
                        st.error("🚫 BOOKING DENIED: This vehicle is currently on the Blacklist.")
                    else:
                        with sqlite3.connect('parking.db', timeout=20) as conn:
                            cur = conn.cursor()
                            cur.execute("SELECT branch_id FROM parking_spots WHERE (plate_number=? OR reserved_plate=?) AND (is_occupied=1 OR is_reserved=1)", (p, p))
                            already = cur.fetchone()
                            
                        if already: 
                            st.error("Vehicle already has an active reservation or spot.")
                        else:
                            lvl, spot = parking_logic.assign_slot(sz, b['id'])
                            if lvl:
                                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                with sqlite3.connect('parking.db', timeout=20) as conn:
                                    conn.execute("UPDATE parking_spots SET is_reserved=1, reserved_plate=?, reserved_size=?, booking_timestamp=? WHERE branch_id=? AND level=? AND spot_id=?", (p, sz, now, b['id'], lvl, spot))
                                    conn.commit()
                                st.success(f"Successfully Reserved Spot {spot} on {lvl}!")
                                st.session_state.trigger_balloons = True
                                
                                render_thermal_ticket("ONLINE BOOKING", b['name'], p, lvl, spot, now, "SHOW QR AT ENTRY GATE")
                                dl_booking = generate_ticket_download("ONLINE BOOKING", b['name'], p, lvl, spot, now, "SHOW AT GATE")
                                st.download_button("📥 Download Booking Slip", dl_booking, f"Booking_{p}.png", use_container_width=True)
                            else: 
                                st.error("Parking is currently full for this vehicle size.")
        with tl:
            lc1, lc2 = st.columns([3, 1], vertical_alignment="bottom")
            lp = lc1.text_input("Enter Plate to Locate").upper().strip()
            if lc2.button("Locate Vehicle", use_container_width=True):
                v_data = database.get_vehicle(lp)
                if v_data and v_data[0] == b['id']:
                    st.success(f"✅ Vehicle Found on **{v_data[1]}**, **Spot {v_data[2]}**")
                    render_svg_map(database.get_all_spots(b['id'], v_data[1]))
                else: 
                    st.error("Vehicle not currently registered in this facility.")

# --- MAIN ROUTER ---

if not st.session_state.role:
    st.markdown("<h1 style='text-align: center;'>🔐 Access Portal</h1>", unsafe_allow_html=True)
    _, center_col, _ = st.columns([1, 2, 1])
    
    with center_col:
        role_sel = st.selectbox("Select Portal", ["Customer", "Security", "Admin"])
        
        if role_sel == "Customer":
            if st.button("Enter Portal", type="primary", use_container_width=True): 
                st.session_state.role = "Customer"
                st.rerun()
        else:
            b_list = st.session_state.live_branches
            sel_name = st.selectbox("Location Site", [b['name'] for b in b_list])
            sel_b = next((b for b in b_list if b['name'] == sel_name), b_list[0])
            
            pwd = "".join([c for c in sel_name if c.isalpha()])[:4].lower() + "0123"
            st.info(f"💡 Branch Key: {pwd}")
            
            key_in = st.text_input("Enter Access Key", type="password")
            if st.button("Unlock System", type="primary", use_container_width=True):
                if key_in == pwd: 
                    st.session_state.role = role_sel
                    st.session_state.managed_branch_id = sel_b['id']
                    st.session_state.managed_branch_name = sel_name
                    st.rerun()
                else: 
                    st.error("Invalid Security Key")
else:
    if st.sidebar.button("🚪 Logout"): 
        st.session_state.clear()
        st.rerun()
    
    role = st.session_state.role
    bid = st.session_state.managed_branch_id
    bname = st.session_state.managed_branch_name
    
    if role == "Admin": 
        render_admin(bid, bname)
    elif role == "Security": 
        render_security(bid, bname)
    elif role == "Customer": 
        render_customer()
