import easyocr
import re
import cv2
import numpy as np
import streamlit as st

@st.cache_resource
def get_ocr_reader():
    return easyocr.Reader(['en'], gpu=False)

# UPDATED: Added 'T'->'1' and 'D'->'0' to handle weird fonts/screws
CHAR_TO_NUM = {'Z': '7', 'S': '5', 'O': '0', 'B': '8', 'I': '1', 'G': '6', 'Q': '0', 'T': '1', 'D': '0'}
NUM_TO_CHAR = {'7': 'Z', '5': 'S', '0': 'O', '8': 'B', '1': 'I', '6': 'G', '2': 'Z'}

def correct_indian_plate(text):
    """Forces character corrections based on the Indian license plate format."""
    if len(text) not in [9, 10]:
        return text 
    
    fixed_text = list(text)
    
    # State Code (Indices 0, 1): Must be letters
    for i in range(0, 2):
        if fixed_text[i] in NUM_TO_CHAR:
            fixed_text[i] = NUM_TO_CHAR[fixed_text[i]]
            
    # District Code (Indices 2, 3): Must be numbers
    for i in range(2, 4):
        if fixed_text[i] in CHAR_TO_NUM:
            fixed_text[i] = CHAR_TO_NUM[fixed_text[i]]
            
    # Unique Number (Last 4 indices): Must be numbers
    for i in range(len(fixed_text) - 4, len(fixed_text)):
        if fixed_text[i] in CHAR_TO_NUM:
            fixed_text[i] = CHAR_TO_NUM[fixed_text[i]]
            
    # Series (Middle indices): Must be letters
    for i in range(4, len(fixed_text) - 4):
        if fixed_text[i] in NUM_TO_CHAR:
            fixed_text[i] = NUM_TO_CHAR[fixed_text[i]]
            
    return "".join(fixed_text)

def extract_and_correct_plate(raw_text):
    """Slides a window over the messy text to find a valid license plate."""
    clean_text = "".join(e for e in raw_text if e.isalnum()).upper()
    
    # UPDATED: Strip "IND" out entirely so it doesn't interrupt two-line plates
    clean_text = clean_text.replace("IND", "")
    
    pattern = re.compile(r'^[A-Z]{2}[0-9]{2}[A-Z]{1,2}[0-9]{4}$')
    
    # Check all possible 10 and 9 character windows in the string
    for window_size in [10, 9]:
        if len(clean_text) >= window_size:
            for i in range(len(clean_text) - window_size + 1):
                substring = clean_text[i : i + window_size]
                corrected = correct_indian_plate(substring)
                
                if pattern.match(corrected):
                    return corrected
    return ""

def preprocess_plate(frame):
    """Safely cleans the image without destroying screen pixels."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    resized = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    return resized

def detect_text(frame):
    reader = get_ocr_reader()
    try:
        processed_frame = preprocess_plate(frame)
        results = reader.readtext(processed_frame, allowlist='ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789')
        
        if not results: 
            return ""
            
        # 1. Combine everything EasyOCR saw into one string
        combined_raw = "".join([text for (bbox, text, prob) in results])
        
        # 2. Extract plate from noise
        found_plate = extract_and_correct_plate(combined_raw)
        if found_plate:
            return found_plate
            
        # 3. Fallback check
        candidates = []
        for (bbox, text, prob) in results:
            clean = "".join(e for e in text if e.isalnum()).upper()
            
            # Also remove IND from the fallback checks
            clean = clean.replace("IND", "") 
            
            corrected = correct_indian_plate(clean)
            if len(corrected) >= 7 and prob > 0.4:
                candidates.append((prob, corrected))
                
        if candidates:
            candidates.sort(key=lambda x: x[0], reverse=True)
            return candidates[0][1]
            
        return ""
    except Exception as e:
        print(f"OCR Error: {e}")
        return ""