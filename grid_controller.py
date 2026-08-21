"""
grid_controller.py — runs on the computer.

Combines com.py's video pipeline with arduino_controller.py: uses a
one-time MANUAL calibration of the grid's 4 corners (no per-frame
detection at all — no rim, no dots, no brightness thresholding),
builds a perspective-corrected mapping from pixels -> real-world
centimeters from those fixed corners, finds which grid block the hand
is closest to, and (optionally) tells the Arduino to raise that block.

HOW TO CALIBRATE: run calibrate_grid.py once (and again anytime the
camera or grid physically moves), click the 4 corners of the block
grid in the window it opens, and paste its printed output into
MANUAL_CORNERS_PX below.

WIRE PROTOCOL for the video stream (must match picam.py exactly):
    1 byte  : message type  -> b'F' = frame payload, b'L' = log text
    4 bytes : big-endian unsigned int, payload length in bytes
    N bytes : payload (raw YUV420 frame bytes, or utf-8 text for logs)

The video ingestion (network thread, frame decode, palm detection +
smoothing) below is copied UNCHANGED from com.py on purpose — the
instructions were not to touch that logic. Everything under the
"GRID TRACKING" and "ARDUINO CONTROL" headers is new.

GRID GEOMETRY
--------------
Block size ~3cm x 3.1cm, spacing ~0.5cm, 5x5 grid -> the block+spacing
area works out to ~17.0cm x 17.5cm. MANUAL_CORNERS_PX maps directly
onto that rectangle via cv2.getPerspectiveTransform, computed once at
startup (not per frame), so this correctly handles camera angle/skew
the same way the detection-based versions did — the only difference is
where the 4 corners come from (a human click instead of computer
vision).
"""

import socket
import struct
import threading
import time
import traceback

import cv2
import numpy as np
import mediapipe as mp

from arduino_controller import ArduinoController, ArduinoError

# ==========================================================
# PI CONNECTION  (unchanged from com.py)
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
    print(f"[COMPUTER:main] Connecting to Pi at {PI_IP}:{PORT} ...", flush=True)
    client_socket.connect((PI_IP, PORT))
except Exception as e:
    print(f"[COMPUTER:main] FATAL: could not connect to Pi at {PI_IP}:{PORT} — {e}. "
          f"Is picam.py running on the Pi, and is PI_IP correct/reachable?", flush=True)
    raise SystemExit(1)

print("[COMPUTER:main] Connected to Pi Zero stream!", flush=True)


# ==========================================================
# MEDIAPIPE HAND TRACKING  (unchanged from com.py)
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
# STREAM BUFFER  (unchanged from com.py)
# ==========================================================

latest_frame_data = None
frame_lock = threading.Lock()
stop_event = threading.Event()


# ==========================================================
# NETWORK THREAD  (unchanged from com.py)
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
                  f"picam.py and grid_controller.py?", flush=True)


threading.Thread(target=network_ingest_thread, daemon=True).start()


# ==========================================================
# PALM SMOOTHING  (unchanged from com.py)
# ==========================================================

smooth_x = None
smooth_y = None


# ==========================================================
# GRID TRACKING  (new)
# ==========================================================

# --- physical layout, used only to keep the block/spacing proportions correct ---
GRID_COLS = 5
GRID_ROWS = 5
BLOCK_W_CM = 3.0
BLOCK_H_CM = 3.1
SPACING_CM = 0.5

BLOCK_GRID_W_CM = GRID_COLS * BLOCK_W_CM + (GRID_COLS - 1) * SPACING_CM  # 17.0
BLOCK_GRID_H_CM = GRID_ROWS * BLOCK_H_CM + (GRID_ROWS - 1) * SPACING_CM  # 17.5

# world-space (cm) corners/centers of every cell, row-major, measured
# from the block cluster's own top-left corner (0,0) — index 0 =
# top-left block, index 24 = bottom-right block, matching
# arduino_controller.send_servo_values().
CELLS = []
for row in range(GRID_ROWS):
    for col in range(GRID_COLS):
        x0 = col * (BLOCK_W_CM + SPACING_CM)
        y0 = row * (BLOCK_H_CM + SPACING_CM)
        x1 = x0 + BLOCK_W_CM
        y1 = y0 + BLOCK_H_CM
        CELLS.append({
            "index": row * GRID_COLS + col,
            "row": row,
            "col": col,
            "center_cm": (x0 + BLOCK_W_CM / 2.0, y0 + BLOCK_H_CM / 2.0),
            "corners_cm": np.array(
                [[x0, y0], [x1, y0], [x1, y1], [x0, y1]], dtype=np.float32
            ),
        })

WORLD_CORNERS_CM = np.array(
    [[0.0, 0.0], [BLOCK_GRID_W_CM, 0.0], [0.0, BLOCK_GRID_H_CM], [BLOCK_GRID_W_CM, BLOCK_GRID_H_CM]],
    dtype=np.float32,
)  # order: TL, TR, BL, BR — corners of the block cluster itself

# --- MANUAL CALIBRATION ---
MANUAL_CORNERS_PX = np.array([
    [92.5, 16.0],  # top-left
    [299.5, 21.0],  # top-right
    [83.5, 227.5],  # bottom-left
    [291.5, 238.5],  # bottom-right
], dtype=np.float32)

# Homography is computed ONCE at startup from the fixed corners above
# — no per-frame detection at all, so this is also cheap.
h_img2world = cv2.getPerspectiveTransform(MANUAL_CORNERS_PX, WORLD_CORNERS_CM)
h_world2img = cv2.getPerspectiveTransform(WORLD_CORNERS_CM, MANUAL_CORNERS_PX)

# How far outside the grid (as a fraction of grid size) the hand is
# still allowed to "count" toward the nearest block. Sanity check so a
# hand way off in a corner of the frame doesn't grab whatever block
# happens to be nearest.
OUT_OF_BOUNDS_MARGIN = 0.35


def image_point_to_world(pt_xy):
    pts = np.array([[pt_xy]], dtype=np.float32)  # shape (1,1,2)
    out = cv2.perspectiveTransform(pts, h_img2world)
    return float(out[0, 0, 0]), float(out[0, 0, 1])


def world_points_to_image(pts_xy):
    pts = np.array([pts_xy], dtype=np.float32)  # shape (1,N,2)
    out = cv2.perspectiveTransform(pts, h_world2img)
    return out[0]  # shape (N,2)


def nearest_cell(world_xy):
    """Returns the CELLS entry closest to world_xy (in cm), or None if
    world_xy is well outside the grid bounds (see OUT_OF_BOUNDS_MARGIN)."""
    wx, wy = world_xy
    margin_x = BLOCK_GRID_W_CM * OUT_OF_BOUNDS_MARGIN
    margin_y = BLOCK_GRID_H_CM * OUT_OF_BOUNDS_MARGIN
    if not (-margin_x <= wx <= BLOCK_GRID_W_CM + margin_x
            and -margin_y <= wy <= BLOCK_GRID_H_CM + margin_y):
        return None

    best = None
    best_dist2 = None
    for cell in CELLS:
        cx, cy = cell["center_cm"]
        dist2 = (wx - cx) ** 2 + (wy - cy) ** 2
        if best_dist2 is None or dist2 < best_dist2:
            best_dist2 = dist2
            best = cell
    return best


def draw_grid_overlay(frame, active_cell):
    """Draws every cell's outline, and fills the active cell (if any)
    with a translucent highlight."""
    overlay = frame.copy()

    for cell in CELLS:
        img_pts = world_points_to_image(cell["corners_cm"])
        img_pts_int = img_pts.astype(np.int32)

        is_active = active_cell is not None and cell["index"] == active_cell["index"]
        color = (0, 255, 255) if is_active else (255, 180, 0)

        if is_active:
            cv2.fillPoly(overlay, [img_pts_int], color)

        cv2.polylines(frame, [img_pts_int], isClosed=True, color=color,
                      thickness=2 if is_active else 1)

    cv2.addWeighted(overlay, 0.35, frame, 0.65, 0, dst=frame)

    # also mark the 4 calibrated corners for visual reference
    for pt in MANUAL_CORNERS_PX:
        cv2.circle(frame, (int(pt[0]), int(pt[1])), 5, (0, 0, 255), -1)


# ==========================================================
# ARDUINO CONTROL
# ==========================================================

ENABLE_ARDUINO = True  # flip to True once the grid overlay looks right
ARDUINO_PORT = None      # e.g. "COM6" — None auto-detects like testing.py
ARDUINO_POLL_SEC = 0.05  # how often the send thread checks for a new target

arm = None
desired_target_index = None  # None = no block should be raised
target_lock = threading.Lock()

if ENABLE_ARDUINO:
    try:
        arm = ArduinoController(ARDUINO_PORT)
    except ArduinoError as e:
        print(f"[GRID_CONTROLLER:arduino] WARNING: could not connect to Arduino "
              f"— continuing with grid display only. ({e})", flush=True)
        arm = None


def arduino_send_thread():
    """Mirrors the network thread's design: always send the freshest
    target, never let a slow/blocked serial write stall the main video
    loop. Only sends when the target actually changes, so we're not
    spamming the Arduino (and jittering servos) every frame."""
    last_sent_index = "unset"  # sentinel, distinct from None
    while not stop_event.is_set():
        with target_lock:
            idx = desired_target_index

        if idx != last_sent_index:
            values = [0.0] * ArduinoController.NUM_SERVOS
            if idx is not None:
                values[idx] = 1.0
            try:
                arm.send_servo_values(values)
                last_sent_index = idx
            except ArduinoError as e:
                print(f"[GRID_CONTROLLER:arduino] WARNING: send failed — {e}", flush=True)

        time.sleep(ARDUINO_POLL_SEC)


if arm is not None:
    threading.Thread(target=arduino_send_thread, daemon=True).start()


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
                # Convert YUV420 to BGR  (unchanged from com.py)
                expected = int(WIDTH * HEIGHT * 1.5)
                if len(frame_bytes) != expected:
                    log("decode", f"WARNING: received frame of {len(frame_bytes)} "
                                   f"bytes, expected {expected} for {WIDTH}x{HEIGHT} "
                                   f"YUV420 — skipping this frame (dimensions/format "
                                   f"mismatch between picam.py and grid_controller.py?).")
                else:
                    yuv = np.frombuffer(frame_bytes, dtype=np.uint8)
                    yuv = yuv.reshape((int(HEIGHT * 1.5), WIDTH))
                    frame = cv2.cvtColor(yuv, cv2.COLOR_YUV2BGR_I420)

                    # Camera is physically mounted upside down (top/bottom
                    # swapped, left/right already correct) — flip 0 =
                    # vertical-only flip, corrects that without touching
                    # left/right. Done once here, before anything downstream
                    # (hand detection, grid overlay) sees the frame, so
                    # everything stays consistent.
                    frame = cv2.flip(frame, 0)

                    # ==================================================
                    # HAND DETECTION  (unchanged from com.py)
                    # ==================================================
                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    results = hands.process(rgb)

                    hand_present = False
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
                            hand_present = True

                    # ==================================================
                    # GRID TRACKING  (new)
                    # ==================================================
                    active_cell = None
                    if hand_present:
                        world_xy = image_point_to_world((smooth_x, smooth_y))
                        active_cell = nearest_cell(world_xy)

                    draw_grid_overlay(frame, active_cell)

                    if active_cell is not None:
                        cv2.putText(
                            frame,
                            f"Target block: row {active_cell['row']} col {active_cell['col']} "
                            f"(index {active_cell['index']})",
                            (10, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 2,
                        )

                    with target_lock:
                        desired_target_index = active_cell["index"] if active_cell else None

                    display = cv2.resize(frame, (WIDTH * 2, HEIGHT * 2), interpolation=cv2.INTER_NEAREST)
                    cv2.imshow("Grid Tracking", display)
                    frames_shown += 1

            except Exception as e:
                log("render", f"ERROR while decoding/rendering a frame — {e}")
                traceback.print_exc()

        if time.time() - last_report > 10:
            print(f"[COMPUTER:main] {frames_shown} frames shown in last ~10s "
                  f"({frames_shown/10:.1f} fps).", flush=True)
            frames_shown = 0
            last_report = time.time()

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
    if arm is not None:
        try:
            arm.close()
        except Exception:
            pass