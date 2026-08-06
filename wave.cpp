#include <Wire.h>
#include <Adafruit_PWMServoDriver.h>

Adafruit_PWMServoDriver pwm40 = Adafruit_PWMServoDriver(0x40);
Adafruit_PWMServoDriver pwm41 = Adafruit_PWMServoDriver(0x41);

#define USMIN  600   
#define USMAX  2400  
#define SERVO_FREQ 50 

// SPEED CONTROL: Lower this number to make the wave travel faster (in milliseconds)
const int waveSpeedDelay = 300; 

// Using the exact pin numbers from your image drawing
int board41_pins[5][3] = {
  {1, 6, 11}, // Column 0
  {2, 7, 12}, // Column 1
  {3, 8, 13}, // Column 2
  {4, 9, 14}, // Column 3
  {5, 10, 15} // Column 4
};

int board40_pins[5][2] = {
  {6, 11}, // Column 0
  {7, 12}, // Column 1
  {8, 13}, // Column 2
  {9, 14}, // Column 3
  {10, 15} // Column 4
};

void setServoAngle(Adafruit_PWMServoDriver &driver, int channel, int angle) {
  int pulse = map(angle, 0, 180, USMIN, USMAX);
  driver.writeMicroseconds(channel, pulse);
}

void setup() {
  pwm40.begin();
  pwm40.setOscillatorFrequency(27000000);
  pwm40.setPWMFreq(SERVO_FREQ);

  pwm41.begin();
  pwm41.setOscillatorFrequency(27000000);
  pwm41.setPWMFreq(SERVO_FREQ);

  // Force all 25 servos to start flat at 180 degrees
  for (int col = 0; col < 5; col++) {
    for (int r = 0; r < 3; r++) setServoAngle(pwm41, board41_pins[col][r], 180);
    for (int r = 0; r < 2; r++) setServoAngle(pwm40, board40_pins[col][r], 180);
  }
  delay(1500); 
}

void loop() {
  // Step across columns 0 to 4
  for (int col = 0; col < 5; col++) {
    
    // 1. Instantly pull down the active column to 90 degrees
    for (int r = 0; r < 3; r++) setServoAngle(pwm41, board41_pins[col][r], 90);
    for (int r = 0; r < 2; r++) setServoAngle(pwm40, board40_pins[col][r], 90);

    // 2. Pause briefly while it is down
    delay(waveSpeedDelay);

    // 3. Drive this column straight back up to 180 degrees
    for (int r = 0; r < 3; r++) setServoAngle(pwm41, board41_pins[col][r], 180);
    for (int r = 0; r < 2; r++) setServoAngle(pwm40, board40_pins[col][r], 180);
  }
}
