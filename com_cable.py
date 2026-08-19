"""
com_cable.py — runs on the computer.

Connects to picam_cable.py on the Pi over the USB cable (instead of
Wi-Fi/socket) — the Pi's USB OTG port shows up here as a plain serial
port, the same way the Arduino does. Receives the YUV420 video stream
and displays it.

Same "low lag" design as com.py: a dedicated reader thread does
nothing but pull complete messages off the serial port as fast as
they arrive and stash the newest FRAME in `latest_frame_data`. The
display loop only ever renders the newest frame available and throws
away anything older, so processing never builds up a backlog/lag —
this is what keeps the fps high and consistent instead of slowly
drifting behind.

WIRE PROTOCOL (must match picam_cable.py exactly):
    1 byte  : message type -> b'F' = frame payload
    4 bytes : big-endian unsigned int, payload length in bytes
    N bytes : raw YUV420 frame bytes

FINDING THE PORT:
    Once picam_cable.py is running on the Pi (USB cable in its DATA
    port), the Pi shows up here as a new serial device — e.g. COM7 on
    Windows, /dev/tty.usbmodemXXXX on Mac, /dev/ttyACM0 on Linux. If
    the Arduino is plugged in too, you'll see two ports — the
    auto-detect below does a best-effort guess; set PI_PORT explicitly
    if it picks the wrong one.
"""

import struct
import threading
import time
import traceback

import cv2
import numpy as np
import serial
import serial.tools.list_ports

# ==========================================================
# PI CONNECTION
# ==========================================================

PI_PORT = "COM5"  # e.g. "COM7" or "/dev/tty.usbmodem14201" — set explicitly if auto-detect picks wrong
BAUD = 1000000  # nominal only, ignored by USB gadget serial

WIDTH = 320
HEIGHT = 240

MSG_FRAME = b'F'
HEADER = struct.Struct('>cI')  # 1 byte type + 4 byte length

SERIAL_READ_TIMEOUT_SEC = 1.0   # per-chunk read timeout, not an artificial delay
RECV_TIMEOUT_SEC = 5.0          # if we hear nothing for this long, warn loudly


def find_pi_port():
    """Best-effort auto-detect: a USB gadget serial device usually
    reports something with 'gadget', 'ACM', or 'CDC' in its
    description. Falls through to None (caller lists all ports) if
    nothing matches — safer than guessing wrong and stealing the
    Arduino's port."""
    for p in serial.tools.list_ports.comports():
        desc = (p.description or "").lower()
        if "gadget" in desc or "acm" in desc or "cdc" in desc:
            return p.device
    return None


port = PI_PORT or find_pi_port()
if port is None:
    print("[COMPUTER:main] FATAL: could not auto-detect the Pi's serial port. "
          "Available ports:", flush=True)
    for p in serial.tools.list_ports.comports():
        print(f"    {p.device} — {p.description}", flush=True)
    print("[COMPUTER:main] Set PI_PORT explicitly at the top of this file to "
          "one of the above.", flush=True)
    raise SystemExit(1)

print(f"[COMPUTER:main] Opening {port}...", flush=True)
ser = serial.Serial(port, BAUD, timeout=SERIAL_READ_TIMEOUT_SEC)
time.sleep(1)  # let the port settle before talking on it
ser.reset_input_buffer()

print("[COMPUTER:main] Waiting for Pi (READY)...", flush=True)
while True:
    line = ser.readline().decode("utf-8", errors="replace").strip()
    if line == "READY":
        ser.write(b"HELLO\n")
        ser.flush()
        print("[COMPUTER:main] Connected to Pi!", flush=True)
        break
    elif line:
        print(f"[COMPUTER:main] (ignoring unexpected line before handshake: {line!r})",
              flush=True)


# ==========================================================
# STREAM BUFFER
# ==========================================================

latest_frame_data = None
frame_lock = threading.Lock()
stop_event = threading.Event()


def _recv_exact(ser, n):
    """Read exactly n bytes off the serial port, or return None if the
    port goes quiet for RECV_TIMEOUT_SEC. Each ser.read() call blocks
    only up to SERIAL_READ_TIMEOUT_SEC and returns early as soon as
    data arrives, so this doesn't add artificial latency while frames
    are flowing normally."""
    data = b''
    deadline = time.time() + RECV_TIMEOUT_SEC
    while len(data) < n:
        chunk = ser.read(n - len(data))
        if chunk:
            data += chunk
            deadline = time.time() + RECV_TIMEOUT_SEC
        elif time.time() > deadline:
            return None
    return data


def serial_ingest_thread():
    global latest_frame_data

    last_warn_time = time.time()

    while not stop_event.is_set():
        header = _recv_exact(ser, HEADER.size)
        if header is None:
            if time.time() - last_warn_time > RECV_TIMEOUT_SEC:
                print(f"[COMPUTER:serial] WARNING: no data from Pi in "
                      f"{RECV_TIMEOUT_SEC:.0f}s. Check the cable and that "
                      f"picam_cable.py is still running.", flush=True)
                last_warn_time = time.time()
            continue

        try:
            msg_type, length = HEADER.unpack(header)
        except struct.error:
            continue  # garbled header — drop it and try to resync on the next read

        payload = _recv_exact(ser, length)
        if payload is None:
            print("[COMPUTER:serial] ERROR: connection dropped mid-message. Stopping.",
                  flush=True)
            stop_event.set()
            return

        if msg_type == MSG_FRAME:
            with frame_lock:
                latest_frame_data = payload
        # any other message type is ignored — this cable link only carries frames


threading.Thread(target=serial_ingest_thread, daemon=True).start()


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
                expected = int(WIDTH * HEIGHT * 1.5)
                if len(frame_bytes) != expected:
                    print(f"[COMPUTER:decode] WARNING: received frame of "
                          f"{len(frame_bytes)} bytes, expected {expected} for "
                          f"{WIDTH}x{HEIGHT} YUV420 — skipping this frame.", flush=True)
                else:
                    yuv = np.frombuffer(frame_bytes, dtype=np.uint8)
                    yuv = yuv.reshape((int(HEIGHT * 1.5), WIDTH))
                    frame = cv2.cvtColor(yuv, cv2.COLOR_YUV2BGR_I420)

                    display = cv2.resize(frame, (WIDTH * 2, HEIGHT * 2),
                                          interpolation=cv2.INTER_NEAREST)
                    cv2.imshow("Pi Camera (USB cable)", display)
                    frames_shown += 1

            except Exception as e:
                print(f"[COMPUTER:render] ERROR while decoding/rendering a frame — {e}",
                      flush=True)
                traceback.print_exc()

        if time.time() - last_report > 10:
            print(f"[COMPUTER:main] {frames_shown} frames shown in last ~10s "
                  f"({frames_shown/10:.1f} fps).", flush=True)
            frames_shown = 0
            last_report = time.time()

        if cv2.waitKey(1) & 0xFF == ord('q'):
            print("[COMPUTER:main] User pressed 'q' — closing.", flush=True)
            break

finally:
    print("[COMPUTER:main] Closing...", flush=True)
    stop_event.set()
    try:
        ser.close()
    except Exception:
        pass
    cv2.destroyAllWindows()