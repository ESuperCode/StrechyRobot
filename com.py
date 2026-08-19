"""
com.py — runs on the computer.

Connects to picam.py on the Pi, receives the YUV420 video stream, runs
Mediapipe hand tracking on it, and displays the annotated feed.

WIRE PROTOCOL (must match picam.py exactly):
    1 byte  : message type  -> b'F' = frame payload, b'L' = log text
    4 bytes : big-endian unsigned int, payload length in bytes
    N bytes : payload (raw YUV420 frame bytes, or utf-8 text for logs)

Design notes for "low lag":
  - A dedicated network thread does nothing but read complete messages
    off the socket as fast as they arrive and stash the newest FRAME in
    `latest_frame_data`. It never blocks waiting on the display loop.
  - The display loop only ever renders the newest frame available and
    throws away anything older, so if processing (Mediapipe) is briefly
    slower than the incoming frame rate, you drop stale frames instead
    of building up a growing backlog/lag.
  - Both sides also exchange plain-text "log" messages over the same
    socket, so a problem on either machine is visible on BOTH terminals
    (prefixed [PI] or [COMPUTER]) instead of one side silently failing.
"""

import socket
import struct
import threading
import time
import traceback

import cv2
import numpy as np
import mediapipe as mp

import matplotlib.pyplot as plt
import time
import random
# ==========================================================
# PI CONNECTION
# ==========================================================

PI_IP = "192.168.5.43"
PORT = 9001

WIDTH = 320
HEIGHT = 240

MSG_FRAME = b'F'
MSG_LOG = b'L'
HEADER = struct.Struct('>cI')  # 1 byte type + 4 byte length

RECV_TIMEOUT_SEC = 5.0  # if we hear nothing for this long, warn loudly

client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
client_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
client_socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 262144)

send_lock = threading.Lock()  # protects sendall from both threads


class LiveGrapher:
    def __init__(self, title="Live Data Stream", xlabel="Time / Step", ylabel="Value"):
        """Initializes the window and plot styles."""
        # Turn on interactive mode so plt.show() doesn't block your code execution
        plt.ion()
        
        # Setup figure and axis
        self.fig, self.ax = plt.subplots()
        self.x_data = []
        self.y_data = []
        
        # Initialize an empty line plot ('o-' gives points connected by lines)
        self.line, = self.ax.plot(self.x_data, self.y_data, 'b-o', markersize=4)
        
        # Aesthetic setup
        self.ax.set_title(title)
        self.ax.set_xlabel(xlabel)
        self.ax.set_ylabel(ylabel)
        self.ax.grid(True, linestyle='--', alpha=0.6)

    def add_data_point(self, x, y):
        """Call this function whenever your code generates a new value."""
        # Append new data
        self.x_data.append(x)
        self.y_data.append(y)
        
        # Update the line object with the new datasets
        self.line.set_xdata(self.x_data)
        self.line.set_ydata(self.y_data)
        
        # Dynamically rescale the axis limits to fit the new data
        self.ax.relim()
        self.ax.autoscale_view()
        
        # Redraw the canvas and flush UI events so the window updates instantly
        self.fig.canvas.draw()
        self.fig.canvas.flush_events()

    def keep_open(self):
        """Call this at the very end of your script so the graph window stays open."""
        plt.ioff()
        plt.show()

def log(where, msg):
    """Print locally AND forward to the Pi, so both terminals agree on
    what went wrong and where it happened."""
    line = f"[COMPUTER:{where}] {msg}"
    print(line, flush=True)
    try:
        payload = line.encode('utf-8')
        header = HEADER.pack(MSG_LOG, len(payload))
        with send_lock:
            client_socket.sendall(header + payload)
    except Exception as e:
        print(f"[COMPUTER:log] WARNING: could not forward log to Pi: {e}", flush=True)


try:
    log_prefix_ready = False  # client_socket exists but log() would try to send before connect
    print(f"[COMPUTER:main] Connecting to Pi at {PI_IP}:{PORT} ...", flush=True)
    client_socket.connect((PI_IP, PORT))
except Exception as e:
    print(f"[COMPUTER:main] FATAL: could not connect to Pi at {PI_IP}:{PORT} — {e}. "
          f"Is picam.py running on the Pi, and is PI_IP correct/reachable?", flush=True)
    raise SystemExit(1)

print("[COMPUTER:main] Connected to Pi Zero stream!", flush=True)


# ==========================================================
# MEDIAPIPE HAND TRACKING
# ==========================================================

try:
    mp_hands = mp.solutions.hands
    mp_draw = mp.solutions.drawing_utils

    hands = mp_hands.Hands(
        static_image_mode=False,
        max_num_hands=1,
        min_detection_confidence=0.5,
        min_tracking_confidence=0.5,
    )
except Exception as e:
    log("mediapipe", f"FATAL: could not initialize Mediapipe Hands — {e}")
    raise SystemExit(1)


# ==========================================================
# STREAM BUFFER
# ==========================================================

latest_frame_data = None
frame_lock = threading.Lock()
stop_event = threading.Event()


# ==========================================================
# NETWORK THREAD
# ==========================================================

def _recv_exact(sock, n):
    """Read exactly n bytes or return None on clean disconnect/timeout."""
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
    last_frame_time = time.time()

    while not stop_event.is_set():
        header = _recv_exact(client_socket, HEADER.size)

        if header is None:
            # Either a real disconnect, or just a quiet 5s with no data.
            # Distinguish by trying a zero-length probe isn't reliable
            # over a stream socket, so just warn and keep looping — if
            # the connection is truly dead, the *next* recv will also
            # time out repeatedly, which is itself the useful signal.
            if time.time() - last_frame_time > RECV_TIMEOUT_SEC:
                print(f"[COMPUTER:network] WARNING: no data from Pi in "
                      f"{RECV_TIMEOUT_SEC:.0f}s. Check that picam.py is "
                      f"still running and the Wi-Fi link is up.", flush=True)
                last_frame_time = time.time()
            continue

        msg_type, length = HEADER.unpack(header)
        payload = _recv_exact(client_socket, length)
        if payload is None:
            print("[COMPUTER:network] ERROR: connection to Pi dropped mid-message. "
                  "Stopping.", flush=True)
            stop_event.set()
            return

        if msg_type == MSG_FRAME:
            with frame_lock:
                latest_frame_data = payload
            last_frame_time = time.time()
        elif msg_type == MSG_LOG:
            print(f"[PI] {payload.decode('utf-8', errors='replace')}", flush=True)
        else:
            print(f"[COMPUTER:network] WARNING: unexpected message type "
                  f"{msg_type!r} from Pi — protocol mismatch between "
                  f"picam.py and com.py?", flush=True)


threading.Thread(target=network_ingest_thread, daemon=True).start()


# ==========================================================
# PALM SMOOTHING
# ==========================================================

smooth_x = None
smooth_y = None
#FPS plot
graph = LiveGrapher(title="Sensor Readings over Time", xlabel="Seconds", ylabel="Temperature")
current_time=10

# ==========================================================
# MAIN LOOP
# ==========================================================

try:
    frames_shown = 0
    last_report = time.time()

    while not stop_event.is_set():

        frame_bytes = None
        with frame_lock:
            if latest_frame_data is not None:
                frame_bytes = latest_frame_data
                latest_frame_data = None

        if frame_bytes:
            try:
                # Convert YUV420 to BGR
                expected = int(WIDTH * HEIGHT * 1.5)
                if len(frame_bytes) != expected:
                    log("decode", f"WARNING: received frame of {len(frame_bytes)} "
                                   f"bytes, expected {expected} for {WIDTH}x{HEIGHT} "
                                   f"YUV420 — skipping this frame (dimensions/format "
                                   f"mismatch between picam.py and com.py?).")
                else:
                    yuv = np.frombuffer(frame_bytes, dtype=np.uint8)
                    yuv = yuv.reshape((int(HEIGHT * 1.5), WIDTH))
                    frame = cv2.cvtColor(yuv, cv2.COLOR_YUV2BGR_I420)

                    # ==================================================
                    # HAND DETECTION
                    # ==================================================
                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    results = hands.process(rgb)

                    if results.multi_hand_landmarks:
                        for hand in results.multi_hand_landmarks:
                            mp_draw.draw_landmarks(frame, hand, mp_hands.HAND_CONNECTIONS)

                            h, w, _ = frame.shape

                            xs = [int(lm.x * w) for lm in hand.landmark]
                            ys = [int(lm.y * h) for lm in hand.landmark]
                            x1, y1, x2, y2 = min(xs), min(ys), max(xs), max(ys)
                            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)

                            palm_points = [0, 1, 5, 9, 13, 17]
                            palm_x = int(sum(hand.landmark[i].x for i in palm_points) / len(palm_points) * w)
                            palm_y = int(sum(hand.landmark[i].y for i in palm_points) / len(palm_points) * h)

                            if smooth_x is None:
                                smooth_x, smooth_y = palm_x, palm_y
                            else:
                                alpha = 1
                                smooth_x = int(alpha * palm_x + (1 - alpha) * smooth_x)
                                smooth_y = int(alpha * palm_y + (1 - alpha) * smooth_y)

                            cv2.circle(frame, (smooth_x, smooth_y), 8, (0, 0, 255), -1)
                            cv2.putText(frame, f"Palm X:{smooth_x} Y:{smooth_y}", (10, 25),
                                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)

                    display = cv2.resize(frame, (WIDTH * 2, HEIGHT * 2), interpolation=cv2.INTER_NEAREST)
                    cv2.imshow("Pi Zero Palm Tracking", display)
                    frames_shown += 1

            except Exception as e:
                log("render", f"ERROR while decoding/rendering a frame — {e}")
                traceback.print_exc()

        if time.time() - last_report > 10:
            graph.add_data_point(current_time, frames_shown)
            print(f"[COMPUTER:main] {frames_shown} frames shown in last ~10s "
                  f"({frames_shown/10:.1f} fps).", flush=True)
            frames_shown = 0
            last_report = time.time()
            current_time+=10
        if cv2.waitKey(1) & 0xFF == ord('q'):
            log("main", "User pressed 'q' — closing.")
            break

finally:
    print("[COMPUTER:main] Closing...", flush=True)
    stop_event.set()
    try:
        client_socket.close()
    except Exception:
        pass
    hands.close()
    cv2.destroyAllWindows()