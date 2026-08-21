"""
calibrate_grid.py — runs on the computer, ONE TIME (and again anytime
the camera or grid physically moves).

Connects to the Pi's video feed (identical protocol/setup to com.py)
and lets you click the 4 corners of the block grid, in order:
    1. top-left
    2. top-right
    3. bottom-left
    4. bottom-right
Once all 4 are placed, it prints a ready-to-paste Python array —
copy/paste that into MANUAL_CORNERS_PX near the top of
grid_controller.py's GRID TRACKING section.

CONTROLS:
    left click  - place the next corner
    r           - reset and start over
    q           - quit (prints the corners again if you got all 4)

Applies the exact same vertical flip grid_controller.py applies, so
the coordinates you click here line up with what it actually sees.
"""

import socket
import struct
import threading

import cv2
import numpy as np

# ==========================================================
# PI CONNECTION  (same setup as com.py)
# ==========================================================
PI_IP = "192.168.5.43"
PORT = 9001
WIDTH = 320
HEIGHT = 240

MSG_FRAME = b'F'
MSG_LOG = b'L'
HEADER = struct.Struct('>cI')
RECV_TIMEOUT_SEC = 5.0

client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
client_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
client_socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 262144)

print(f"[CALIBRATE] Connecting to Pi at {PI_IP}:{PORT} ...", flush=True)
client_socket.connect((PI_IP, PORT))
print("[CALIBRATE] Connected!", flush=True)

latest_frame_data = None
frame_lock = threading.Lock()
stop_event = threading.Event()


def _recv_exact(sock, n):
    data = b''
    while len(data) < n:
        try:
            chunk = sock.recv(n - len(data))
        except socket.timeout:
            return None
        if not chunk:
            return None
        data += chunk
    return data


def network_ingest_thread():
    global latest_frame_data
    client_socket.settimeout(RECV_TIMEOUT_SEC)
    while not stop_event.is_set():
        header = _recv_exact(client_socket, HEADER.size)
        if header is None:
            continue
        msg_type, length = HEADER.unpack(header)
        payload = _recv_exact(client_socket, length)
        if payload is None:
            print("[CALIBRATE] Connection to Pi dropped.", flush=True)
            stop_event.set()
            return
        if msg_type == MSG_FRAME:
            with frame_lock:
                latest_frame_data = payload
        elif msg_type == MSG_LOG:
            print(f"[PI] {payload.decode('utf-8', errors='replace')}", flush=True)


threading.Thread(target=network_ingest_thread, daemon=True).start()


# ==========================================================
# CLICK CALIBRATION
# ==========================================================
CORNER_LABELS = ["top-left", "top-right", "bottom-left", "bottom-right"]
DISPLAY_SCALE = 2  # matches the 2x resize grid_controller.py/com.py use for viewing

clicked_points_display = []  # in DISPLAY (2x) pixel coordinates

WINDOW_NAME = "Click 4 grid corners (TL, TR, BL, BR)"


def on_mouse(event, x, y, flags, param):
    if event == cv2.EVENT_LBUTTONDOWN and len(clicked_points_display) < 4:
        clicked_points_display.append((x, y))
        real_x, real_y = x / DISPLAY_SCALE, y / DISPLAY_SCALE
        print(f"[CALIBRATE] {CORNER_LABELS[len(clicked_points_display)-1]} = "
              f"({real_x:.1f}, {real_y:.1f})", flush=True)


cv2.namedWindow(WINDOW_NAME)
cv2.setMouseCallback(WINDOW_NAME, on_mouse)

print("[CALIBRATE] Click the 4 grid corners in order: top-left, top-right, "
      "bottom-left, bottom-right. Press 'r' to reset, 'q' when done.", flush=True)

try:
    while not stop_event.is_set():
        frame_bytes = None
        with frame_lock:
            if latest_frame_data is not None:
                frame_bytes = latest_frame_data
                latest_frame_data = None

        if frame_bytes:
            expected = int(WIDTH * HEIGHT * 1.5)
            if len(frame_bytes) == expected:
                yuv = np.frombuffer(frame_bytes, dtype=np.uint8)
                yuv = yuv.reshape((int(HEIGHT * 1.5), WIDTH))
                frame = cv2.cvtColor(yuv, cv2.COLOR_YUV2BGR_I420)
                frame = cv2.flip(frame, 0)  # match grid_controller.py's upside-down camera fix

                display = cv2.resize(frame, (WIDTH * DISPLAY_SCALE, HEIGHT * DISPLAY_SCALE),
                                      interpolation=cv2.INTER_NEAREST)

                for i, (dx, dy) in enumerate(clicked_points_display):
                    cv2.circle(display, (dx, dy), 6, (0, 0, 255), -1)
                    cv2.putText(display, CORNER_LABELS[i], (dx + 8, dy - 8),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)

                if len(clicked_points_display) < 4:
                    next_label = f"Click: {CORNER_LABELS[len(clicked_points_display)]}"
                else:
                    next_label = "All 4 placed - press 'q' to finish"
                cv2.putText(display, next_label, (10, 25),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)

                cv2.imshow(WINDOW_NAME, display)

        key = cv2.waitKey(1) & 0xFF
        if key == ord('r'):
            clicked_points_display.clear()
            print("[CALIBRATE] Reset.", flush=True)
        elif key == ord('q'):
            break

finally:
    stop_event.set()
    try:
        client_socket.close()
    except Exception:
        pass
    cv2.destroyAllWindows()

if len(clicked_points_display) == 4:
    print("\n[CALIBRATE] Done! Paste this into grid_controller.py "
          "(replacing the existing MANUAL_CORNERS_PX):\n")
    print("MANUAL_CORNERS_PX = np.array([")
    for label, (dx, dy) in zip(CORNER_LABELS, clicked_points_display):
        real_x, real_y = dx / DISPLAY_SCALE, dy / DISPLAY_SCALE
        print(f"    [{real_x:.1f}, {real_y:.1f}],  # {label}")
    print("], dtype=np.float32)\n")
else:
    print(f"\n[CALIBRATE] Only got {len(clicked_points_display)}/4 corners — "
          f"nothing to paste. Run again.", flush=True)