import socket
import cv2
import numpy as np
import threading
import time

# 1. Connect directly to your specific Pi Zero IP configuration
PI_IP = "192.168.5.43"  
PORT = 9001

client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
client_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
client_socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 262144) 

client_socket.connect((PI_IP, PORT))
print("Successfully connected to the Ultra-Fast Pi Zero Stream!")

# Structural dimensions for the incoming 320x240 YUV420 frame
WIDTH = 320
HEIGHT = 240
FRAME_SIZE = int(WIDTH * HEIGHT * 1.5) 
YUV_ROWS = int(HEIGHT * 1.5)            

latest_frame_data = None
frame_lock = threading.Lock()
stop_event = threading.Event()

def network_ingest_thread():
    global latest_frame_data
    client_socket.setblocking(False)
    data_buffer = b''
    
    while not stop_event.is_set():
        try:
            while True:
                packet = client_socket.recv(32768)
                if not packet:
                    break
                data_buffer += packet
        except BlockingIOError:
            pass 
        
        if len(data_buffer) >= FRAME_SIZE:
            num_frames = len(data_buffer) // FRAME_SIZE
            start_idx = (num_frames - 1) * FRAME_SIZE
            end_idx = start_idx + FRAME_SIZE
            
            with frame_lock:
                latest_frame_data = data_buffer[start_idx:end_idx]
                
            data_buffer = data_buffer[end_idx:]
            
        time.sleep(0.001)

# Start the background thread
net_thread = threading.Thread(target=network_ingest_thread, daemon=True)
net_thread.start()

try:
    while True:
        frame_bytes = None
        with frame_lock:
            if latest_frame_data is not None:
                frame_bytes = latest_frame_data
                latest_frame_data = None 
                
        if frame_bytes is not None:
            # Reassemble the lightweight 320x240 raw matrix layout
            yuv_array = np.frombuffer(frame_bytes, dtype=np.uint8).reshape((YUV_ROWS, WIDTH))
            bgr_frame = cv2.cvtColor(yuv_array, cv2.COLOR_YUV2BGR_I420)
            
            # -------------------------------------------------------------
            #  🚀 RUN YOUR COMPUTER NEURAL NETWORK HERE 🚀
            #  Note: Pass the original, lightweight 'bgr_frame' (320x240)
            #  into your AI model so it continues to think instantly!
            # -------------------------------------------------------------
            
            # 🌟 NEW: Upscale the window frame locally on your PC 🌟
            # (WIDTH * 2, HEIGHT * 2) turns it into a clear 640x480 window
            # Change the multiplier to 3 (960x720) if you want it even larger!
            upscaled_frame = cv2.resize(
                bgr_frame, 
                (WIDTH * 2, HEIGHT * 2), 
                interpolation=cv2.INTER_NEAREST
            )
            
            # Display the crisp, upscaled real-time video window output
            cv2.imshow("Pi Zero Raw Wireless Stream", upscaled_frame)
            
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break
finally:
    print("Shutting down stream pipelines cleanly...")
    stop_event.set()
    net_thread.join(timeout=1.0)
    client_socket.close()
    cv2.destroyAllWindows()
