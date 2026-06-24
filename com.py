import socket
import cv2
import numpy as np
import threading
import time
import mediapipe as mp

# ==========================================================
# CONNECTION SETTINGS
# ==========================================================

PI_IP = "192.168.5.43"
PORT = 9001

WIDTH = 320
HEIGHT = 240

FRAME_SIZE = int(WIDTH * HEIGHT * 1.5)
YUV_ROWS = int(HEIGHT * 1.5)

# ==========================================================
# MEDIAPIPE HAND TRACKER
# ==========================================================

mp_hands = mp.solutions.hands
mp_draw = mp.solutions.drawing_utils

hands = mp_hands.Hands(
    static_image_mode=False,
    max_num_hands=1,
    min_detection_confidence=0.5,
    min_tracking_confidence=0.5
)

# ==========================================================
# NETWORK SETUP
# ==========================================================

client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
client_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
client_socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 262144)

client_socket.connect((PI_IP, PORT))
print("Successfully connected to Pi Zero stream!")

latest_frame_data = None
frame_lock = threading.Lock()
stop_event = threading.Event()

# ==========================================================
# BACKGROUND NETWORK THREAD
# ==========================================================

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

# ==========================================================
# START NETWORK THREAD
# ==========================================================

net_thread = threading.Thread(
    target=network_ingest_thread,
    daemon=True
)

net_thread.start()

# ==========================================================
# MAIN LOOP
# ==========================================================

try:

    fps_timer = time.time()
    frame_counter = 0

    while True:

        frame_bytes = None

        with frame_lock:
            if latest_frame_data is not None:
                frame_bytes = latest_frame_data
                latest_frame_data = None

        if frame_bytes is not None:

            # Decode YUV420 -> BGR
            yuv_array = np.frombuffer(
                frame_bytes,
                dtype=np.uint8
            ).reshape((YUV_ROWS, WIDTH))

            bgr_frame = cv2.cvtColor(
                yuv_array,
                cv2.COLOR_YUV2BGR_I420
            )

            # ==================================================
            # HAND DETECTION
            # ==================================================

            rgb_frame = cv2.cvtColor(
                bgr_frame,
                cv2.COLOR_BGR2RGB
            )

            results = hands.process(rgb_frame)

            if results.multi_hand_landmarks:

                for hand_landmarks in results.multi_hand_landmarks:

                    # Draw hand skeleton
                    mp_draw.draw_landmarks(
                        bgr_frame,
                        hand_landmarks,
                        mp_hands.HAND_CONNECTIONS
                    )

                    h, w, _ = bgr_frame.shape

                    xs = []
                    ys = []

                    for lm in hand_landmarks.landmark:
                        xs.append(int(lm.x * w))
                        ys.append(int(lm.y * h))

                    # Bounding box
                    x_min = max(min(xs), 0)
                    y_min = max(min(ys), 0)
                    x_max = min(max(xs), w - 1)
                    y_max = min(max(ys), h - 1)

                    cv2.rectangle(
                        bgr_frame,
                        (x_min, y_min),
                        (x_max, y_max),
                        (0, 255, 0),
                        2
                    )

                    # Hand center
                    center_x = (x_min + x_max) // 2
                    center_y = (y_min + y_max) // 2

                    cv2.circle(
                        bgr_frame,
                        (center_x, center_y),
                        5,
                        (0, 0, 255),
                        -1
                    )

                    # Wrist coordinate (landmark 0)
                    wrist = hand_landmarks.landmark[0]

                    wrist_x = int(wrist.x * w)
                    wrist_y = int(wrist.y * h)

                    cv2.circle(
                        bgr_frame,
                        (wrist_x, wrist_y),
                        7,
                        (255, 0, 0),
                        -1
                    )

                    # Display coordinates
                    cv2.putText(
                        bgr_frame,
                        f"Center X:{center_x} Y:{center_y}",
                        (x_min, max(y_min - 10, 20)),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.5,
                        (0, 255, 0),
                        2
                    )

                    cv2.putText(
                        bgr_frame,
                        f"Wrist X:{wrist_x} Y:{wrist_y}",
                        (10, 20),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.6,
                        (255, 0, 0),
                        2
                    )

                    # Print coordinates to terminal
                    print(
                        f"Wrist: ({wrist_x}, {wrist_y}) | "
                        f"Center: ({center_x}, {center_y})",
                        end="\r"
                    )

            # ==================================================
            # FPS COUNTER
            # ==================================================

            frame_counter += 1

            current_time = time.time()

            if current_time - fps_timer >= 1.0:
                fps = frame_counter
                frame_counter = 0
                fps_timer = current_time

            try:
                cv2.putText(
                    bgr_frame,
                    f"FPS: {fps}",
                    (10, HEIGHT - 10),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    (0, 255, 255),
                    2
                )
            except:
                pass

            # ==================================================
            # UPSCALE FOR DISPLAY
            # ==================================================

            upscaled_frame = cv2.resize(
                bgr_frame,
                (WIDTH * 2, HEIGHT * 2),
                interpolation=cv2.INTER_NEAREST
            )

            cv2.imshow(
                "Pi Zero Hand Tracking",
                upscaled_frame
            )

        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

finally:

    print("\nShutting down cleanly...")

    stop_event.set()

    net_thread.join(timeout=1.0)

    hands.close()

    client_socket.close()

    cv2.destroyAllWindows()