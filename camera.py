import cv2
from ultralytics import YOLO

# Load the trained AI model
model = YOLO('license_plate_detector.pt') 

def detect_plate_region(frame):
    results = model(frame)
    for r in results:
        boxes = r.boxes
        for box in boxes:
            # Safely extract tensor values to integers
            coords = box.xyxy[0].tolist()
            x1, y1, x2, y2 = [int(c) for c in coords]
            
            # Padding gives the OCR white space to breathe
            pad = 20 
            h, w, _ = frame.shape
            
            # Ensure padding doesn't push coordinates outside the image boundaries
            y1 = max(0, y1 - pad)
            y2 = min(h, y2 + pad)
            x1 = max(0, x1 - pad)
            x2 = min(w, x2 + pad)
            
            crop = frame[y1:y2, x1:x2]
            return crop
            
    return frame