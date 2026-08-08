"""
picam.py — runs on the Raspberry Pi.

Streams raw YUV420 camera frames to the computer over TCP, and shares a
tiny text-log channel over the SAME socket so both machines can see what
the other one is doing/seeing wrong in real time.

WIRE PROTOCOL (must match com.py exactly):
    1 byte  : message type  -> b'F' = frame payload, b'L' = log text
    4 bytes : big-endian unsigned int, payload length in bytes
    N bytes : payload (raw YUV420 frame bytes, or utf-8 text for logs)

This framing is what actually fixes the "nothing displays" bug: the
previous version captured a frame and never sent it (the `send` call was
literally missing), and even a fixed version that assumed a constant
byte size per frame is fragile — any stride/padding mismatch silently
desyncs the stream forever. Explicit type+length headers make that
impossible: every message is self-describing, so both ends always agree
on where one message ends and the next begins.
"""

import socket
import struct
import signal
import sys
import threading
import time

from picamera2 import Picamera2

# ==========================================================
# CONFIG
# ==========================================================
HOST = '0.0.0.0'
PORT = 9001

WIDTH = 320
HEIGHT = 240
EXPECTED_FRAME_BYTES = int(WIDTH * HEIGHT * 1.5)  # YUV420 planar

MSG_FRAME = b'F'
MSG_LOG = b'L'
HEADER = struct.Struct('>cI')  # 1 byte type + 4 byte length

# ==========================================================
# GLOBAL STATE
# ==========================================================
server_socket = None
client_socket = None
picam2 = None
stop_event = threading.Event()
send_lock = threading.Lock()  # protects socket.sendall between threads


def log(where, msg):
    """Print locally AND forward to the computer (if connected) so both
    terminals tell the same story about what went wrong and where."""
    line = f"[PI:{where}] {msg}"
    print(line, flush=True)
    if client_socket is not None:
        try:
            payload = line.encode('utf-8')
            header = HEADER.pack(MSG_LOG, len(payload))
            with send_lock:
                client_socket.sendall(header + payload)
        except Exception as e:
            # Don't let a failed log-forward crash whatever called log()
            print(f"[PI:log] WARNING: could not forward log to computer: {e}", flush=True)


def send_frame(frame_bytes):
    header = HEADER.pack(MSG_FRAME, len(frame_bytes))
    with send_lock:
        client_socket.sendall(header + frame_bytes)


def incoming_listener_thread():
    """Runs for the life of the connection, blocked on recv, printing any
    log messages the computer sends us (e.g. 'hand tracking crashed',
    'closing down'). This is the Pi's half of the two-way channel."""
    try:
        while not stop_event.is_set():
            header = _recv_exact(client_socket, HEADER.size)
            if header is None:
                log("listener", "Connection closed by computer.")
                stop_event.set()
                return
            msg_type, length = HEADER.unpack(header)
            payload = _recv_exact(client_socket, length)
            if payload is None:
                log("listener", "Connection closed by computer mid-message.")
                stop_event.set()
                return
            if msg_type == MSG_LOG:
                print(f"[COMPUTER] {payload.decode('utf-8', errors='replace')}", flush=True)
            else:
                log("listener", f"WARNING: got unexpected message type {msg_type!r} "
                                 f"from computer — protocol mismatch between picam.py and com.py?")
    except OSError:
        pass  # socket was closed during shutdown, nothing to report
    except Exception as e:
        log("listener", f"ERROR: listener thread crashed unexpectedly: {e}")
        stop_event.set()


def _recv_exact(sock, n):
    """Read exactly n bytes or return None on clean disconnect."""
    data = b''
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            return None
        data += chunk
    return data


def cleanup(sig=None, frame=None):
    print("[PI:cleanup] Shutting down safely...", flush=True)
    stop_event.set()
    try:
        if client_socket is not None:
            client_socket.close()
    except Exception:
        pass
    try:
        if server_socket is not None:
            server_socket.close()
    except Exception:
        pass
    try:
        if picam2 is not None:
            picam2.stop()
    except Exception:
        pass
    sys.exit(0)


signal.signal(signal.SIGINT, cleanup)


def main():
    global server_socket, client_socket, picam2

    # 1. Open the listening socket
    try:
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 262144)
        server_socket.bind((HOST, PORT))
        server_socket.listen(1)
    except OSError as e:
        print(f"[PI:main] FATAL: could not bind/listen on {HOST}:{PORT} — {e}. "
              f"Is another instance of picam.py already running?", flush=True)
        sys.exit(1)

    print(f"[PI:main] Waiting for computer to connect on port {PORT}...", flush=True)
    try:
        client_socket, addr = server_socket.accept()
        client_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except Exception as e:
        print(f"[PI:main] FATAL: accept() failed — {e}", flush=True)
        cleanup()
        return

    print(f"[PI:main] Connected to computer at {addr}", flush=True)

    # Start the log-listener thread now that we have a live socket
    threading.Thread(target=incoming_listener_thread, daemon=True).start()

    # 2. Configure and start the camera
    try:
        picam2 = Picamera2()
        config = picam2.create_video_configuration(
            main={"size": (WIDTH, HEIGHT), "format": "YUV420"},
            controls={
                "FrameRate": 30,
                "AeConstraintMode": 0,
                "AwbMode": 1,
            },
            buffer_count=4,  # small pipeline = lower lag, still enough to avoid stalls
        )
        picam2.configure(config)
        picam2.start()
        log("camera", f"Camera started at {WIDTH}x{HEIGHT} YUV420.")
    except Exception as e:
        log("camera", f"FATAL: could not start Picamera2 — {e}")
        cleanup()
        return

    # 3. Capture -> send loop
    frame_count = 0
    warned_about_size = False
    try:
        while not stop_event.is_set():
            frame = picam2.capture_array()
            frame_bytes = frame.tobytes()

            if len(frame_bytes) != EXPECTED_FRAME_BYTES and not warned_about_size:
                # This is exactly the kind of silent-desync bug that breaks
                # the display on the computer side, so call it out loudly
                # and only once (not every frame) to avoid spamming.
                log("capture",
                    f"WARNING: captured frame is {len(frame_bytes)} bytes, "
                    f"but com.py expects {EXPECTED_FRAME_BYTES} bytes for "
                    f"{WIDTH}x{HEIGHT} YUV420. This usually means the sensor "
                    f"added row padding/stride. If the feed looks skewed or "
                    f"never shows, this is why — WIDTH/HEIGHT must match on "
                    f"both machines and be stride-free (multiples of 32 are safest).")
                warned_about_size = True

            send_frame(frame_bytes)
            frame_count += 1

    except (BrokenPipeError, ConnectionResetError) as e:
        log("capture", f"Connection to computer lost — {e}")
    except Exception as e:
        log("capture", f"FATAL: unexpected error in capture loop — {e}")
    finally:
        log("main", f"Sent {frame_count} frames total. Shutting down.")
        cleanup()


if __name__ == "__main__":
    main()