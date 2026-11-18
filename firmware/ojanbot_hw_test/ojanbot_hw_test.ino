// ============================================================================
//  OjanBot hardware test + calibration (run this BEFORE ojanbot_synapse).
//  Uses the same config.h, so whatever you fix here is fixed for the real firmware.
//  Open Serial Monitor at 115200 baud, line ending "Newline", and type a letter:
//
//    l  line sensors (10 s live)    u  ultrasonic (10 s live)     w  Wi-Fi + backend check
//    f  both wheels forward 1 s     b  both backward 1 s
//    L  left wheel forward 1 s      R  right wheel forward 1 s
//    p  pivot left 90 degrees       P  pivot 4 x 90 (should end facing the same way)
//    m  follow the line to the next station bar and stop with the wheels on it
//    v  follow the line for 2 s, then measure how far it went (sets CM_PER_S)
//    k  wiring check (analog AN wire or 5 digital wires, as set by LINE_MODE in config.h)
//    a  find which pin the Maker Line's analog AN wire is on
//    x  stop everything
//
//  Lift the wheels off the table (or hold the robot) for f/b/L/R the first time.
//  Use a dummy weight like the phone's (about 200 g) on top while calibrating.
// ============================================================================
#include <esp_arduino_version.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <WiFiClientSecure.h>
#include "config.h"   // copy of ../ojanbot_synapse/config.h - keep the two identical

// Stop with a clear message if this folder still has an old config.h next to a newer sketch.
#if !defined(FEATURE_CONFIRM) || !defined(SENSOR_AHEAD_CM) || !defined(PIN_LINE_D5) || !defined(LINE_MODE)
#error "config.h is out of date: copy firmware/ojanbot_synapse/config.h from the repo into this sketch's folder"
#endif

#if ESP_ARDUINO_VERSION_MAJOR >= 3
static void pwmSetup(int pin, int ch) { if (pin >= 0) ledcAttach(pin, PWM_FREQ_HZ, PWM_BITS); }
static void pwmWrite(int pin, int ch, int duty) { if (pin >= 0) ledcWrite(pin, duty); }
#else
static void pwmSetup(int pin, int ch) { if (pin >= 0) { ledcSetup(ch, PWM_FREQ_HZ, PWM_BITS); ledcAttachPin(pin, ch); } }
static void pwmWrite(int pin, int ch, int duty) { if (pin >= 0) ledcWrite(ch, duty); }
#endif

static void motor(bool left, int speed) {
  if ((left && INVERT_LEFT) || (!left && INVERT_RIGHT)) speed = -speed;
  speed = constrain(speed, -255, 255);
  int a = left ? PIN_L_IN1 : PIN_R_IN1, b = left ? PIN_L_IN2 : PIN_R_IN2;
#if MOTOR_DRIVER == DRIVER_2PWM
  int ca = left ? 0 : 2, cb = left ? 1 : 3;
  if (speed >= 0) { pwmWrite(a, ca, speed); pwmWrite(b, cb, 0); } else { pwmWrite(a, ca, 0); pwmWrite(b, cb, -speed); }
#else
  digitalWrite(a, speed > 0); digitalWrite(b, speed < 0);
  pwmWrite(left ? PIN_L_EN : PIN_R_EN, left ? 0 : 1, abs(speed));
#endif
}
static void drive(int l, int r) { motor(true, l); motor(false, r); }

static const int LINE_PINS[5] = {PIN_LINE_D1, PIN_LINE_D2, PIN_LINE_D3, PIN_LINE_D4, PIN_LINE_D5};
static float anVolts() { return analogRead(PIN_LINE_AN) * 3.3f / 4095.0f; }
static uint8_t lineBits() {
#if LINE_MODE == LINE_MODE_ANALOG
  float v = analogRead(PIN_LINE_AN) / 4095.0f;
  if (v < AN_NO_LINE) return 0;
  if (v > AN_CROSS) return 0b11111;
  float p = constrain((v - 0.5f) / 0.4f, -1.0f, 1.0f);
  return 1 << (int)lroundf(2 + 2 * p);
#else
  uint8_t b = 0;
  for (int i = 0; i < 5; i++) if ((digitalRead(LINE_PINS[i]) == HIGH) == (LINE_ACTIVE_HIGH == 1)) b |= 1 << i;
  return b;
#endif
}
static int countBits(uint8_t b) { int n = 0; for (int i = 0; i < 5; i++) n += (b >> i) & 1; return n; }
static bool barLike(uint8_t b) { return (b & 0b00001) && (b & 0b10000); }        // tape at both outer sensors
static void printBits(uint8_t b) {
#if LINE_MODE == LINE_MODE_ANALOG
  Serial.printf("AN %.2f V  ", anVolts());
#endif
  for (int i = 0; i < 5; i++) Serial.print((b >> i) & 1 ? "#" : ".");
  const char* what = barLike(b) ? "  <- BAR (station / top of T)" :
                     ((b & 1) && (b & 4)) ? "  <- branch LEFT" : ((b & 16) && (b & 4)) ? "  <- branch RIGHT" : "";
  Serial.printf("   (%d on black)%s\n", countBits(b), what);
}
static float distanceCm() {
  digitalWrite(PIN_US_TRIG, LOW); delayMicroseconds(3);
  digitalWrite(PIN_US_TRIG, HIGH); delayMicroseconds(10); digitalWrite(PIN_US_TRIG, LOW);
  unsigned long us = pulseIn(PIN_US_ECHO, HIGH, (unsigned long)(US_MAX_CM * 58.0 * 1.3));
  return us ? us / 58.0f : -1;
}

static bool stopRequested() {
  if (Serial.available() && Serial.peek() == 'x') { Serial.read(); return true; }
  return digitalRead(PIN_ESTOP) == LOW;
}
static void runFor(int l, int r, uint32_t ms) {
  uint32_t t0 = millis();
  drive(l, r);
  while (millis() - t0 < ms) { if (stopRequested()) break; delay(2); }
  drive(0, 0);
}

static void followToMark() {
  Serial.println("Following the line to the next mark (x or BOOT button = stop)...");
  int lastErr = 0, prevErr = 0;
  bool prevMark = barLike(lineBits());
  int reads = 0;
  uint32_t lastSeen = millis(), t0 = millis();
  while (millis() - t0 < 15000) {
    if (stopRequested()) break;
    uint8_t b = lineBits(); int n = countBits(b);
    bool isMark = barLike(b) || n >= 4;
    reads = isMark ? reads + 1 : 0;
    if (reads >= FEATURE_CONFIRM && !prevMark) {
      Serial.printf("Mark seen at %lu ms - driving %d ms more (MARK_CENTER_MS)\n", millis() - t0, MARK_CENTER_MS);
      uint32_t c0 = millis();
      while (millis() - c0 < (uint32_t)MARK_CENTER_MS) {
        int corr = (KP * lastErr) / 10;
        drive(BASE_SPEED * 3 / 4 + corr, BASE_SPEED * 3 / 4 - corr); delay(5);
      }
      drive(0, 0);
      Serial.println("Stopped. The WHEEL AXLE should be right over the mark.");
      Serial.println("  Axle short of the mark -> increase MARK_CENTER_MS. Past it -> decrease.");
      return;
    }
    if (!isMark) prevMark = false;
    if (n == 0) { if (millis() - lastSeen > LINE_LOST_MS) { drive(0, 0); Serial.println("Line lost - stopped."); return; } }
    else {
      lastSeen = millis();
      if (!isMark) { int s = 0; for (int i = 0; i < 5; i++) if (b & (1 << i)) s += i - 2; lastErr = s * 10 / n; }
    }
    int corr = (KP * lastErr + KD * (lastErr - prevErr)) / 10; prevErr = lastErr;
    drive(constrain(BASE_SPEED + corr, -MAX_SPEED, MAX_SPEED), constrain(BASE_SPEED - corr, -MAX_SPEED, MAX_SPEED));
    delay(5);
  }
  drive(0, 0);
  Serial.println("No mark within 15 s.");
}

static void speedCheck() {
  Serial.println("Put the robot on a long straight piece of tape. It follows the line for 2 s.");
  delay(1500);
  int lastErr = 0; uint32_t t0 = millis();
  while (millis() - t0 < 2000) {
    if (stopRequested()) break;
    uint8_t b = lineBits(); int n = countBits(b);
    if (n) { int s = 0; for (int i = 0; i < 5; i++) if (b & (1 << i)) s += i - 2; lastErr = s * 10 / n; }
    int corr = (KP * lastErr) / 10;
    drive(BASE_SPEED + corr, BASE_SPEED - corr); delay(5);
  }
  drive(0, 0);
  Serial.println("Measure how far the wheels moved (cm) and set CM_PER_S = that distance / 2.");
}

// Analog wiring: which ADC pin does the Maker Line's AN wire go to? (only reads, never drives)
static void findAnalogPin() {
  static const int ADC[] = {36, 39, 34, 35, 32, 33, 25, 26, 4, 2, 15, 13, 12, 14, 27};
  const int N = sizeof(ADC) / sizeof(ADC[0]);
  int lo[N], hi[N];
  for (int i = 0; i < N; i++) { lo[i] = 4095; hi[i] = 0; }
  Serial.println("Slide the robot slowly back and forth across the tape for 6 s (sensor LEDs should flicker)...");
  uint32_t t0 = millis();
  while (millis() - t0 < 6000) {
    for (int i = 0; i < N; i++) {
      if (ADC[i] == 12 || ADC[i] == 13 || ADC[i] == 14 || ADC[i] == 27) continue;   // motor pins: skip
      int v = analogRead(ADC[i]); lo[i] = min(lo[i], v); hi[i] = max(hi[i], v);
    }
    delay(4);
  }
  int best = -1, span = 0;
  for (int i = 0; i < N; i++) if (hi[i] - lo[i] > span) { span = hi[i] - lo[i]; best = i; }
  for (int i = 0; i < N; i++) if (hi[i] - lo[i] > 400)
    Serial.printf("  GPIO %d: %.2f V .. %.2f V\n", ADC[i], lo[i] * 3.3 / 4095, hi[i] * 3.3 / 4095);
  if (best < 0 || span < 800)
    Serial.println("=> No pin swings: is the Maker Line powered (its LEDs light over black)? Is the AN wire on an ADC pin?");
  else
  {
    Serial.printf("=> The AN wire is most likely on GPIO %d.\n", ADC[best]);
    if (ADC[best] < 32)
      Serial.println("   WARNING: that is an ADC2 pin, which the ESP32 cannot read while Wi-Fi is on.\n"
                     "   Move the Maker Line's cable to a port on GPIO 32-39 (e.g. Grove 5: 33/32), run 'a' again.");
    else
      Serial.printf("   Set #define PIN_LINE_AN %d in config.h.\n", ADC[best]);
  }
}

// Which of the 5 digital line pins actually change? If none do, the Maker Line is not wired
// through D1-D5 (it may still be on its single analog AN wire, which this firmware cannot use).
static void wiringCheck() {
#if LINE_MODE == LINE_MODE_ANALOG
  Serial.printf("LINE_MODE is ANALOG (AN wire on GPIO %d). Slide across the tape for 6 s...\n", PIN_LINE_AN);
  float vmin = 9, vmax = 0; int usValid = 0, usTotal = 0; uint32_t t0 = millis(), lu = 0;
  while (millis() - t0 < 6000) {
    float v = anVolts(); vmin = min(vmin, v); vmax = max(vmax, v);
    if (millis() - lu > 100) { lu = millis(); usTotal++; if (distanceCm() > 0) usValid++; }
    delay(5);
  }
  Serial.printf("  AN went from %.2f V to %.2f V%s\n", vmin, vmax,
                vmax - vmin > 0.8 ? " - wired OK" : " - NOT CHANGING: wrong pin (run 'a') or Maker Line unpowered");
  Serial.printf("Ultrasonic: %d of %d readings had an echo\n", usValid, usTotal);
#else
  Serial.println("Slide the robot slowly back and forth across the tape for 6 s...");
  uint8_t seenHigh = 0, seenLow = 0;
  int usValid = 0, usTotal = 0;
  uint32_t t0 = millis(), lastUs = 0;
  while (millis() - t0 < 6000) {
    for (int i = 0; i < 5; i++) { if (digitalRead(LINE_PINS[i]) == HIGH) seenHigh |= 1 << i; else seenLow |= 1 << i; }
    if (millis() - lastUs > 100) { lastUs = millis(); usTotal++; if (distanceCm() > 0) usValid++; }
    delay(5);
  }
  int changed = 0;
  for (int i = 0; i < 5; i++) {
    bool ch = (seenHigh & seenLow) & (1 << i);
    changed += ch;
    Serial.printf("  D%d on GPIO %2d: %s\n", i + 1, LINE_PINS[i], ch ? "changes - wired OK" :
                  (seenHigh & (1 << i)) ? "stuck HIGH" : "stuck LOW (not connected?)");
  }
  if (changed == 0)
    Serial.println("=> No digital line outputs detected. The Maker Line is probably still on its analog AN wire\n"
                   "   (or unpowered). Wire D1-D5 as in docs/TABLE_SETUP.md section 3.");
  else if (changed < 5)
    Serial.println("=> Some D pins never changed: check those wires, or slide further so every sensor crosses the tape.");
  else
    Serial.println("=> All 5 digital outputs are wired.");
  Serial.printf("Ultrasonic: %d of %d readings had an echo%s\n", usValid, usTotal,
                usValid == 0 ? " -> NO ECHO: check TRIG/ECHO wiring and power (hold a hand in front and retry)" : "");
#endif
}

static void wifiCheck() {
  Serial.printf("Joining Wi-Fi \"%s\" ...\n", WIFI_SSID);
  WiFi.mode(WIFI_STA); WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  uint32_t t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 15000) { delay(300); Serial.print("."); }
  Serial.println();
  if (WiFi.status() != WL_CONNECTED) { Serial.println("FAILED: check name/password and that the hotspot is 2.4 GHz."); return; }
  Serial.printf("Connected. Robot IP %s, signal %d dBm\n", WiFi.localIP().toString().c_str(), WiFi.RSSI());
  HTTPClient http; WiFiClient plain; WiFiClientSecure secure; secure.setInsecure();
  String url = String(BACKEND_URL) + "/health";
  bool https = url.startsWith("https://");
  http.setTimeout(8000);
  if (https ? http.begin(secure, url) : http.begin(plain, url)) {
    int code = http.GET();
    Serial.printf("GET %s -> %d %s\n", url.c_str(), code, code > 0 ? http.getString().c_str() : http.errorToString(code).c_str());
    if (code <= 0) Serial.println("Backend not reachable: is uvicorn running with --host 0.0.0.0, and is Windows Firewall allowing port 8000?");
    http.end();
  }
}

void setup() {
  Serial.begin(115200);
  delay(300);
#if MOTOR_DRIVER == DRIVER_2PWM
  pwmSetup(PIN_L_IN1, 0); pwmSetup(PIN_L_IN2, 1); pwmSetup(PIN_R_IN1, 2); pwmSetup(PIN_R_IN2, 3);
#else
  pinMode(PIN_L_IN1, OUTPUT); pinMode(PIN_L_IN2, OUTPUT); pinMode(PIN_R_IN1, OUTPUT); pinMode(PIN_R_IN2, OUTPUT);
  pwmSetup(PIN_L_EN, 0); pwmSetup(PIN_R_EN, 1);
#endif
  drive(0, 0);
#if LINE_MODE == LINE_MODE_ANALOG
  pinMode(PIN_LINE_AN, INPUT);
#else
  for (int i = 0; i < 5; i++) pinMode(LINE_PINS[i], INPUT);
#endif
  pinMode(PIN_US_TRIG, OUTPUT); pinMode(PIN_US_ECHO, INPUT);
  pinMode(PIN_ESTOP, INPUT_PULLUP);
  Serial.println("\nOjanBot hardware test. Commands: a k l u w f b L R p P m v x");
}

void loop() {
  if (!Serial.available()) return;
  char c = Serial.read();
  if (c == '\n' || c == '\r') return;
  switch (c) {
    case 'l': {
      Serial.println("D1..D5 (left..right). Centred on the line = '..#..'. On a station bar or T-top: '#####'"
                     " (at least both ends). On a side branch: '###..' or '..###'.");
      uint32_t t0 = millis(); while (millis() - t0 < 10000) { printBits(lineBits()); delay(150); }
      break;
    }
    case 'u': {
      Serial.println("Distance in cm (-1 = nothing in range). Hold a box 5, 8 and 12 cm in front.");
      uint32_t t0 = millis(); while (millis() - t0 < 10000) { Serial.println(distanceCm(), 1); delay(150); }
      break;
    }
    case 'w': wifiCheck(); break;
    case 'f': Serial.println("Forward 1 s: both wheels should roll FORWARD."); runFor(BASE_SPEED, BASE_SPEED, 1000); break;
    case 'b': Serial.println("Backward 1 s."); runFor(-BASE_SPEED, -BASE_SPEED, 1000); break;
    case 'L': Serial.println("LEFT wheel forward 1 s. Wrong wheel = swap the L/R pins. Backwards = INVERT_LEFT 1."); runFor(BASE_SPEED, 0, 1000); break;
    case 'R': Serial.println("RIGHT wheel forward 1 s. Backwards = INVERT_RIGHT 1."); runFor(0, BASE_SPEED, 1000); break;
    case 'p':
      Serial.printf("Pivot left %d ms. Should be a quarter turn: too little -> raise PIVOT_90_MS.\n", PIVOT_90_MS);
      runFor(-PIVOT_SPEED, PIVOT_SPEED, PIVOT_90_MS); break;
    case 'P':
      Serial.println("4 x 90 degrees with pauses; it should end facing exactly where it started.");
      for (int i = 0; i < 4; i++) { runFor(-PIVOT_SPEED, PIVOT_SPEED, PIVOT_90_MS); delay(400); }
      break;
    case 'm': followToMark(); break;
    case 'v': speedCheck(); break;
    case 'k': wiringCheck(); break;
    case 'a': findAnalogPin(); break;
    case 'x': drive(0, 0); Serial.println("Stopped."); break;
    default: Serial.println("Commands: a k l u w f b L R p P m v x");
  }
}
