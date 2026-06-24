import socket
import cv2
import numpy as np
import threading
import time
import mediapipe as mp


# ==========================================================
# PI CONNECTION
# ==========================================================

PI_IP = "192.168.5.43"
PORT = 9001

WIDTH = 320
HEIGHT = 240

FRAME_SIZE = int(WIDTH * HEIGHT * 1.5)
YUV_ROWS = int(HEIGHT * 1.5)


client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

client_socket.setsockopt(
    socket.IPPROTO_TCP,
    socket.TCP_NODELAY,
    1
)

client_socket.setsockopt(
    socket.SOL_SOCKET,
    socket.SO_RCVBUF,
    262144
)

client_socket.connect((PI_IP, PORT))

print("Connected to Pi Zero stream!")


# ==========================================================
# MEDIAPIPE HAND TRACKING
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
# STREAM BUFFER
# ==========================================================

latest_frame_data = None

frame_lock = threading.Lock()

stop_event = threading.Event()


# ==========================================================
# NETWORK THREAD
# ==========================================================

def network_ingest_thread():

    global latest_frame_data

    client_socket.setblocking(False)

    buffer = b''


    while not stop_event.is_set():

        try:

            while True:

                packet = client_socket.recv(32768)

                if not packet:
                    break

                buffer += packet


        except BlockingIOError:
            pass


        if len(buffer) >= FRAME_SIZE:


            frames = len(buffer) // FRAME_SIZE


            start = (frames - 1) * FRAME_SIZE

            end = start + FRAME_SIZE


            with frame_lock:

                latest_frame_data = buffer[start:end]


            buffer = buffer[end:]


        time.sleep(0.001)



threading.Thread(
    target=network_ingest_thread,
    daemon=True
).start()



# ==========================================================
# PALM SMOOTHING
# ==========================================================

smooth_x = None
smooth_y = None



# ==========================================================
# MAIN LOOP
# ==========================================================

try:


    while True:


        frame_bytes = None


        with frame_lock:

            if latest_frame_data is not None:

                frame_bytes = latest_frame_data

                latest_frame_data = None



        if frame_bytes:


            # Convert YUV420 to BGR

            yuv = np.frombuffer(
                frame_bytes,
                dtype=np.uint8
            )


            yuv = yuv.reshape(
                (YUV_ROWS, WIDTH)
            )


            frame = cv2.cvtColor(
                yuv,
                cv2.COLOR_YUV2BGR_I420
            )



            # ==================================================
            # HAND DETECTION
            # ==================================================


            rgb = cv2.cvtColor(
                frame,
                cv2.COLOR_BGR2RGB
            )


            results = hands.process(rgb)



            if results.multi_hand_landmarks:


                for hand in results.multi_hand_landmarks:



                    # Draw skeleton

                    mp_draw.draw_landmarks(
                        frame,
                        hand,
                        mp_hands.HAND_CONNECTIONS
                    )



                    h,w,_ = frame.shape



                    # -------------------------------
                    # HITBOX
                    # -------------------------------


                    xs = []
                    ys = []


                    for lm in hand.landmark:

                        xs.append(int(lm.x*w))
                        ys.append(int(lm.y*h))


                    x1 = min(xs)
                    y1 = min(ys)

                    x2 = max(xs)
                    y2 = max(ys)



                    cv2.rectangle(

                        frame,

                        (x1,y1),

                        (x2,y2),

                        (0,255,0),

                        2

                    )



                    # -------------------------------
                    # PALM CENTER
                    # -------------------------------


                    palm_points = [
                        0, 1, 5,
                        9, 13, 17
                    ]



                    palm_x = int(

                        sum(
                            hand.landmark[i].x
                            for i in palm_points
                        )

                        /

                        len(palm_points)

                        *

                        w

                    )



                    palm_y = int(

                        sum(
                            hand.landmark[i].y
                            for i in palm_points
                        )

                        /

                        len(palm_points)

                        *

                        h

                    )




                    # -------------------------------
                    # SMOOTH POSITION
                    # -------------------------------


                    if smooth_x is None:

                        smooth_x = palm_x
                        smooth_y = palm_y


                    else:

                        alpha = 1


                        smooth_x = int(

                            alpha*palm_x +

                            (1-alpha)*smooth_x

                        )


                        smooth_y = int(

                            alpha*palm_y +

                            (1-alpha)*smooth_y

                        )



                    # Draw palm center

                    cv2.circle(

                        frame,

                        (smooth_x,smooth_y),

                        8,

                        (0,0,255),

                        -1

                    )



                    cv2.putText(

                        frame,

                        f"Palm X:{smooth_x} Y:{smooth_y}",

                        (10,25),

                        cv2.FONT_HERSHEY_SIMPLEX,

                        0.6,

                        (0,255,0),

                        2

                    )





            # Upscale display

            display = cv2.resize(

                frame,

                (WIDTH*2, HEIGHT*2),

                interpolation=cv2.INTER_NEAREST

            )


            cv2.imshow(

                "Pi Zero Palm Tracking",

                display

            )




        if cv2.waitKey(1) & 0xFF == ord('q'):

            break



finally:


    print("Closing...")

    stop_event.set()

    client_socket.close()

    hands.close()

    cv2.destroyAllWindows()