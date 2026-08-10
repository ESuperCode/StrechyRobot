"""
arduino_controller.py

A reusable class wrapping the serial connection to the Uno running
ardu.cpp. Creating an instance opens the port and performs the full
READY/HELLO handshake automatically. After that, call send_servo_values()
with a list of 25 floats (0.0-1.0) any time you want to move the servos.

WIRE PROTOCOL (must match ardu.cpp exactly):
  - 115200 baud
  - Arduino repeats "READY" every ~500ms until it gets "HELLO" back
  - After handshake: we send ONE line of 25 comma-separated floats
    (0.0-1.0), ending in '\n'
      0.0 -> servo at 180 degrees (flat)
      1.0 -> servo at 90 degrees  (other extreme)
  - Arduino replies "OK" if it parsed all 25 values, "ERROR" otherwise

USAGE:
    from arduino_controller import ArduinoController

    arm = ArduinoController("COM6")          # setup + handshake happens here
    arm.send_servo_values([1.0] * 25)         # all servos up
    arm.send_servo_values([0.0] * 25)         # all servos flat
    arm.close()
"""

import time
import glob

import serial
import serial.tools.list_ports


class ArduinoError(Exception):
    """Raised when the Arduino link fails to connect, handshake, or
    respond as expected."""
    pass


class ArduinoController:

    BAUD = 115200
    NUM_SERVOS = 25
    HANDSHAKE_TIMEOUT_SEC = 15
    REPLY_TIMEOUT_SEC = 2

    def __init__(self, port=None, verbose=True):
        """Opens the serial port and performs the full READY/HELLO
        handshake before returning. Raises ArduinoError if either step
        fails, so by the time __init__ returns successfully, the
        Arduino is confirmed alive and ready for real commands."""
        self.verbose = verbose
        self.port = port or self._find_port()

        if self.port is None:
            raise ArduinoError(
                "Could not find any serial port. Plug in the Arduino, "
                "or pass the port explicitly, e.g. ArduinoController('COM6')."
            )

        self._log(f"Opening {self.port} at {self.BAUD} baud...")
        try:
            self.ser = serial.Serial(self.port, self.BAUD, timeout=self.REPLY_TIMEOUT_SEC)
        except Exception as e:
            raise ArduinoError(
                f"Could not open port {self.port} — {e}. Is the Arduino "
                f"IDE's Serial Monitor open on this port? Close it first, "
                f"only one program can hold the port at a time."
            )

        time.sleep(2)  # let the Arduino finish its power-on reset

        self._handshake()

    def _find_port(self):
        ports = list(serial.tools.list_ports.comports())
        for p in ports:
            desc = (p.description or "").lower()
            if "arduino" in desc or "usb serial" in desc or "ch340" in desc or "wch" in desc:
                return p.device
        if ports:
            return ports[0].device
        return None

    def _log(self, msg):
        if self.verbose:
            print(f"[ARDUINO_CONTROLLER] {msg}")

    def _handshake(self):
        """Reads lines until READY appears, then sends HELLO back. The
        Arduino repeats READY every 500ms until acked, so this doesn't
        depend on any precise timing and needs no reset button press."""
        self._log("Waiting for Arduino handshake (repeats every 500ms, no reset needed)...")
        start = time.time()

        while time.time() - start < self.HANDSHAKE_TIMEOUT_SEC:
            line = self.ser.readline().decode("utf-8", errors="replace").strip()
            if line:
                self._log(f"[ARDUINO] {line}")
            if line == "READY":
                self._log("Got READY — sending HELLO ack.")
                self.ser.write(b"HELLO\n")
                self._log("Handshake complete.")
                return

        self.ser.close()
        raise ArduinoError(
            f"Never completed handshake within {self.HANDSHAKE_TIMEOUT_SEC}s. "
            f"Check that ardu.cpp is uploaded and the port/baud rate are correct."
        )

    def send_servo_values(self, values):
        """Sends 25 floats (0.0-1.0) to the Arduino and waits for its
        OK/ERROR reply. Returns True on OK. Raises ArduinoError if the
        values are malformed, the Arduino rejects them, or no reply
        arrives in time."""
        if len(values) != self.NUM_SERVOS:
            raise ArduinoError(
                f"Expected {self.NUM_SERVOS} values, got {len(values)}."
            )

        line = ",".join(f"{v:.3f}" for v in values)
        self._log(f"Sending: {line}")

        try:
            self.ser.write((line + "\n").encode("utf-8"))
        except Exception as e:
            raise ArduinoError(f"Failed to write to serial port — {e}")

        reply = self.ser.readline().decode("utf-8", errors="replace").strip()

        if not reply:
            raise ArduinoError(
                "No reply received from Arduino before timeout — check "
                "wiring/baud rate/port, or that the sketch is still running."
            )

        self._log(f"[ARDUINO] {reply}")

        if reply == "ERROR":
            raise ArduinoError(
                "Arduino rejected the values — check that exactly 25 "
                "comma-separated floats were sent."
            )
        if reply != "OK":
            raise ArduinoError(f"Unexpected reply from Arduino: {reply!r}")

        return True

    def close(self):
        if self.ser and self.ser.is_open:
            self.ser.close()
            self._log("Port closed.")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


if __name__ == "__main__":
    # Quick smoke test: sweep all 25 servos up/down 3 times.
    import sys

    port = sys.argv[1] if len(sys.argv) > 1 else None

    with ArduinoController(port) as arm:
        for i in range(3):
            print(f"--- Cycle {i+1}: all servos UP ---")
            arm.send_servo_values([1.0] * ArduinoController.NUM_SERVOS)
            time.sleep(1.5)

            print(f"--- Cycle {i+1}: all servos FLAT ---")
            arm.send_servo_values([0.0] * ArduinoController.NUM_SERVOS)
            time.sleep(1.5)

    print("Done.")