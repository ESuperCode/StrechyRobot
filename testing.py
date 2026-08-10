from arduino_controller import ArduinoController
import time
arm = ArduinoController("COM6")     # opens the port and does the full handshake right here
arm.send_servo_values([1.0] * 25)   # blocks until Arduino replies OK, all servos up
time.sleep(2)
arm.send_servo_values([0.0] * 25)   # all servos flat
arm.close()