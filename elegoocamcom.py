import cv2
import time

# Target the confirmed static endpoint
stream_url = "http://192.168.4.1"

print("Connecting directly to your Elegoo Camera stream...")
# Add a slight buffer timeout window to allow OpenCV network handshakes to settle
cap = cv2.VideoCapture(stream_url, cv2.CAP_FFMPEG) 

# Wait a brief moment for connection warming
time.sleep(1.0)

if not cap.isOpened():
    print("\n[ERROR] Python OpenCV still cannot bind to the stream endpoint.")
    print("The chip is likely still failing to initialize its physical camera sensor component.")
    print("Please verify your OPI PSRAM setting was enabled during compilation.")
    exit()

print("\n[SUCCESS] Connected! Stream is active. Press 'q' to exit.")

while True:
    ret, frame = cap.read()
    if not ret:
        print("[WARNING] Frame dropped.")
        continue

    cv2.imshow('Elegoo-Cam Direct Live Stream', frame)

    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()
print("Stream closed successfully.")
