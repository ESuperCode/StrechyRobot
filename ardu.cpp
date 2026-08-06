#include <Wire.h>
#include <Adafruit_PWMServoDriver.h>

Adafruit_PWMServoDriver pwm40 = Adafruit_PWMServoDriver(0x40);
Adafruit_PWMServoDriver pwm41 = Adafruit_PWMServoDriver(0x41);

#define USMIN 600
#define USMAX 2400
#define SERVO_FREQ 50

float servoValues[25];

// Pi numbering -> actual board/channels
struct ServoMap {
  Adafruit_PWMServoDriver* board;
  uint8_t channel;
};

ServoMap servos[25] = {
  {&pwm41, 1},   // 1
  {&pwm41, 2},   // 2
  {&pwm41, 3},   // 3
  {&pwm41, 4},   // 4
  {&pwm41, 5},   // 5

  {&pwm41, 6},   // 6
  {&pwm41, 7},   // 7
  {&pwm41, 8},   // 8
  {&pwm41, 9},   // 9
  {&pwm41,10},   // 10

  {&pwm41,11},   // 11
  {&pwm41,12},   // 12
  {&pwm41,13},   // 13
  {&pwm41,14},   // 14
  {&pwm41,15},   // 15

  {&pwm40, 6},   // 16
  {&pwm40, 7},   // 17
  {&pwm40, 8},   // 18
  {&pwm40, 9},   // 19
  {&pwm40,10},   // 20

  {&pwm40,11},   // 21
  {&pwm40,12},   // 22
  {&pwm40,13},   // 23
  {&pwm40,14},   // 24
  {&pwm40,15}    // 25
};

void setServoAngle(Adafruit_PWMServoDriver &driver, int channel, float angle) {
  int pulse = map((int)angle, 0, 180, USMIN, USMAX);
  driver.writeMicroseconds(channel, pulse);
}

void updateServos(float values[]) {

  for (int i = 0; i < 25; i++) {

    // y = -90x + 180
    float angle = (-90.0 * values[i]) + 180.0;

    angle = constrain(angle, 90.0, 180.0);

    setServoAngle(
      *(servos[i].board),
      servos[i].channel,
      angle
    );
  }
}

bool parseLine(String line, float values[]) {

  int start = 0;

  for (int i = 0; i < 25; i++) {

    int comma = line.indexOf(',', start);

    if (comma == -1) {
      if (i == 24) {
        values[i] = line.substring(start).toFloat();
        return true;
      }
      return false;
    }

    values[i] = line.substring(start, comma).toFloat();
    start = comma + 1;
  }

  return false;
}

void setup() {

  Serial.begin(115200);

  pwm40.begin();
  pwm40.setOscillatorFrequency(27000000);
  pwm40.setPWMFreq(SERVO_FREQ);

  pwm41.begin();
  pwm41.setOscillatorFrequency(27000000);
  pwm41.setPWMFreq(SERVO_FREQ);

  delay(100);

  // Start flat
  for (int i = 0; i < 25; i++) {
    setServoAngle(
      *(servos[i].board),
      servos[i].channel,
      180
    );
  }

  Serial.println("READY");
}

void loop() {

  if (Serial.available()) {

    String line = Serial.readStringUntil('\n');
    line.trim();

    if (parseLine(line, servoValues)) {

      updateServos(servoValues);

      Serial.println("OK");
    }
    else {

      Serial.println("ERROR");
    }
  }
}