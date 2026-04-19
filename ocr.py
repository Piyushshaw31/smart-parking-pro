import easyocr
import re
import cv2
import numpy as np
import streamlit as st

@st.cache_resource
def get_ocr_reader():
    # Cache the model to stop lag
    return easyocr.Reader(['en'], gpu=False)

def preprocess_plate(frame):
    """Cleans the image so the AI can read it properly."""
    # 1. Convert to Grayscale
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    # 2. Enlarge the image (helps OCR read small text)
    resized = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    # 3. Boost contrast to make black text pop against white background
    adjusted = cv2.convertScaleAbs(resized, alpha=1.5, beta=0)
    return adjusted

def detect_text(frame):
    reader = get_ocr_reader()
    try:
        # Pass the cleaned image to the AI
        processed_frame = preprocess_plate(frame)
        
        # 'allowlist' forces the AI to only look for A-Z and 0-9. No weird symbols.
        results = reader.readtext(processed_frame, allowlist='ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789')
        
        if not results: 
            return ""
            
        # Strict Indian Pattern Checker
        pattern = re.compile(r'^[A-Z]{2}[0-9]{2}[A-Z]{0,2}[0-9]{4}$')
        candidates = []
        
        for (bbox, text, prob) in results:
            clean = "".join(e for e in text if e.isalnum()).upper()
            
            # If it's a perfect match, return it instantly
            if pattern.match(clean): 
                return clean
                
            # Otherwise, log it if it has good probability
            if len(clean) >= 7 and prob > 0.4:
                candidates.append((prob, clean))
                
        # Return the highest probability match if no perfect match was found
        if candidates:
            candidates.sort(key=lambda x: x[0], reverse=True)
            return candidates[0][1]
            
        return ""
    except Exception as e:
        print(f"OCR Error: {e}")
        return ""