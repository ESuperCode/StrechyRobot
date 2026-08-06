import socket
import signal
import sys
from picamera2 import Picamera2

# 1. Initialize Network Socket with zero-latency toggles
server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 262144) # 256KB buffer is plenty now

server_socket.bind(('0.0.0.0', 9001))
server_socket.listen(1)

print("Pi Zero waiting for ultra-fast 320x240 connection...")
client_socket, addr = server_socket.accept()
print(f"Connected to computer at {addr}")

# 2. Configure Picamera2 to run at 320x240 for max speed
picam2 = Picamera2()
config = picam2.create_video_configuration(
    main={"size": (320, 240), "format": "YUV420"}, # Slashed data size by 75%
    controls={
        "FrameRate": 30,
        "AeConstraintMode": 0,
        "AwbMode": 1
    },
    buffer_count=2
)
picam2.configure(config)
picam2.start()

def cleanup(sig, frame):
    print("\nShutting down safely...")
    picam2.stop()
    client_socket.close()
    server_socket.close()
    sys.exit(0)

signal.signal(signal.SIGINT, cleanup)

try:
    while True:
        # Pull the tiny raw array instantly out of the GPU
        frame_bytes = picam2.capture_array()

        # Blast raw byte data across the open line instantly
except Exception as e:
    print(f"Connection lost: {e}")
finally:
    cleanup(None, None)