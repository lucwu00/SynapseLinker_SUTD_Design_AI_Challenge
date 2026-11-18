// ============================================================================
//  Synapse Linker - EDi-01 firmware for the OjanBot ESP32 (line-following guide robot)
//
//  What it does
//   * Follows a black tape loop and counts the short cross "marks" (one per station).
//   * Gets each journey from the backend:  GET  /robot/EDi-01/next-command
//     and reports what happened:          POST /robot/status
//   * At the destination mark it stops with the wheels over the mark, turns 90 degrees
//     towards the room, measures with the ultrasonic sensor (room OCCUPIED / FREE),
//     turns back onto the line and reports ARRIVED.
//   * Obstacle in front: stops (BLOCKED). Cleared within 5 s -> carries on. Still there ->
//     turns round on the line, reports PATH_BLOCKED and waits for a new route (REROUTE).
//     It never steers around obstacles by itself.
//   * Emergency stop (dashboard STOP or the BOOT button) freezes everything until an
//     operator presses Resume. Lost Wi-Fi/backend for 6 s while moving -> stops for good.
//
//  Libraries: ESP32 board package 2.x or 3.x, ArduinoJson 7.x (Library Manager).
//  Board: "ESP32 Dev Module". Settings live in config.h.
//  Test the hardware first with firmware/ojanbot_hw_test and a DUMMY weight instead of the phone.
// ============================================================================
#include <WiFi.h>
#include <HTTPClient.h>
#include <WiFiClientSecure.h>
#include <ArduinoJson.h>
#include "config.h"

// ---------------------------------------------------------------- shared types
enum Act : uint8_t { A_PASS, A_STOP, A_LEFT, A_RIGHT, A_UTURN, A_GO };
static const uint8_t MAX_MARKS = 24;

struct Command {
  char type[12];          // NAVIGATE REROUTE STOP RESUME CANCEL
  char task[32];
  uint8_t startAct;
  uint8_t nMarks;
  uint8_t marks[MAX_MARKS];
  bool scan;
  bool scanLeft;
};

struct Status {
  char event[20];
  char task[32];
  int16_t marksPassed;    // -1 = not sent
  char roomScan[10];      // "" = not sent
  bool turned;
  bool onMark;            // PATH_BLOCKED before leaving the mark it stood on
  char msg[64];
};

static QueueHandle_t cmdQ, statusQ;
static volatile uint32_t lastCommOkMs = 0;
static volatile bool wifiUp = false;

static uint8_t actOf(const char* s) {
  if (!s) return A_PASS;
  if (!strcmp(s, "STOP")) return A_STOP;
  if (!strcmp(s, "LEFT")) return A_LEFT;
  if (!strcmp(s, "RIGHT")) return A_RIGHT;
  if (!strcmp(s, "U_TURN")) return A_UTURN;
  if (!strcmp(s, "GO") || !strcmp(s, "STRAIGHT")) return A_GO;
  return A_PASS;
}

static void report(const char* event, const char* task, int marksPassed = -1, const char* roomScan = "",
                   bool turned = false, const char* msg = "", bool onMark = false) {
  Status s = {};
  strlcpy(s.event, event, sizeof s.event);
  strlcpy(s.task, task ? task : "", sizeof s.task);
  s.marksPassed = marksPassed;
  strlcpy(s.roomScan, roomScan ? roomScan : "", sizeof s.roomScan);
  s.turned = turned;
  s.onMark = onMark;
  strlcpy(s.msg, msg ? msg : "", sizeof s.msg);
  if (xQueueSend(statusQ, &s, 0) != pdTRUE) Serial.println("[status] outbox full, dropped");
  Serial.printf("[status] %s %s marks=%d scan=%s %s\n", event, s.task, marksPassed, s.roomScan, s.msg);
}

// ============================================================================
//  NETWORK TASK (core 0): poll commands, post statuses. Never touches the motors.
// ============================================================================
static bool isHttps() { return strncmp(BACKEND_URL, "https://", 8) == 0; }

static bool httpBegin(HTTPClient& http, WiFiClient& plain, WiFiClientSecure& secure, const String& url) {
  http.setTimeout(HTTP_TIMEOUT_MS);
  http.setConnectTimeout(HTTP_TIMEOUT_MS);
  return isHttps() ? http.begin(secure, url) : http.begin(plain, url);
}

static bool postStatus(const Status& s, WiFiClient& plain, WiFiClientSecure& secure) {
  JsonDocument d;
  d["robot_id"] = ROBOT_ID;
  if (s.task[0]) d["task_id"] = s.task;
  d["event"] = s.event;
  if (s.marksPassed >= 0) { d["marks_passed"] = s.marksPassed; d["turned_around"] = s.turned; d["on_mark"] = s.onMark; }
  if (s.roomScan[0]) d["room_scan"] = s.roomScan;
  if (s.msg[0]) d["message"] = s.msg;
  String body; serializeJson(d, body);
  HTTPClient http;
  if (!httpBegin(http, plain, secure, String(BACKEND_URL) + "/robot/status")) return false;
  http.addHeader("Content-Type", "application/json");
  int code = http.POST(body);
  http.end();
  if (code >= 200 && code < 300) return true;
  if (code >= 400 && code < 500) { Serial.printf("[status] rejected %d: %s\n", code, body.c_str()); return true; }  // don't retry forever
  return false;
}

static void pollCommand(WiFiClient& plain, WiFiClientSecure& secure) {
  HTTPClient http;
  if (!httpBegin(http, plain, secure, String(BACKEND_URL) + "/robot/" ROBOT_ID "/next-command")) return;
  int code = http.GET();
  if (code != 200) { if (code > 0) Serial.printf("[poll] HTTP %d\n", code); http.end(); return; }
  String payload = http.getString();
  http.end();
  lastCommOkMs = millis();

  JsonDocument filter;                       // keep only what the robot needs (routes also carry waypoints)
  filter["command"] = true; filter["task_id"] = true; filter["start_action"] = true;
  filter["mark_actions"] = true; filter["scan_room_on_arrival"] = true; filter["scan_side"] = true;
  JsonDocument doc;
  if (deserializeJson(doc, payload, DeserializationOption::Filter(filter))) { Serial.println("[poll] bad JSON"); return; }
  const char* type = doc["command"] | "NONE";
  if (!strcmp(type, "NONE")) return;

  Command c = {};
  strlcpy(c.type, type, sizeof c.type);
  strlcpy(c.task, doc["task_id"] | "", sizeof c.task);
  c.startAct = actOf(doc["start_action"] | "GO");
  JsonArray m = doc["mark_actions"].as<JsonArray>();
  for (JsonVariant v : m) { if (c.nMarks < MAX_MARKS) c.marks[c.nMarks++] = actOf(v.as<const char*>()); }
  c.scan = doc["scan_room_on_arrival"] | false;
  c.scanLeft = strcmp(doc["scan_side"] | "LEFT", "RIGHT") != 0;
  Serial.printf("[cmd] %s %s start=%u marks=%u scan=%d\n", c.type, c.task, c.startAct, c.nMarks, c.scan);
  // STOP must never be lost behind other commands
  if (!strcmp(c.type, "STOP")) xQueueSendToFront(cmdQ, &c, portMAX_DELAY);
  else xQueueSend(cmdQ, &c, pdMS_TO_TICKS(200));
}

static void networkTask(void*) {
  WiFiClient plain;
  WiFiClientSecure secure;
  secure.setInsecure();                      // demo only: skips certificate check for the Render URL
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);                      // lower latency
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  uint32_t lastPoll = 0, lastTry = millis();
  for (;;) {
    if (WiFi.status() != WL_CONNECTED) {
      wifiUp = false;
      if (millis() - lastTry > 5000) { Serial.println("[wifi] reconnecting"); WiFi.disconnect(); WiFi.begin(WIFI_SSID, WIFI_PASSWORD); lastTry = millis(); }
      vTaskDelay(pdMS_TO_TICKS(100));
      continue;
    }
    if (!wifiUp) { wifiUp = true; Serial.printf("[wifi] connected, IP %s -> %s\n", WiFi.localIP().toString().c_str(), BACKEND_URL); }

    Status s;                                // send everything waiting in the outbox, in order
    while (xQueuePeek(statusQ, &s, 0) == pdTRUE) {
      if (!postStatus(s, plain, secure)) break;
      xQueueReceive(statusQ, &s, 0);
      lastCommOkMs = millis();
    }
    if (millis() - lastPoll >= POLL_INTERVAL_MS) { lastPoll = millis(); pollCommand(plain, secure); }
    vTaskDelay(pdMS_TO_TICKS(20));
  }
}

// ============================================================================
//  MOTORS
// ============================================================================
#if ESP_ARDUINO_VERSION_MAJOR >= 3
static void pwmSetup(int pin, int ch) { if (pin >= 0) ledcAttach(pin, PWM_FREQ_HZ, PWM_BITS); }
static void pwmWrite(int pin, int ch, int duty) { if (pin >= 0) ledcWrite(pin, duty); }
#else
static void pwmSetup(int pin, int ch) { if (pin >= 0) { ledcSetup(ch, PWM_FREQ_HZ, PWM_BITS); ledcAttachPin(pin, ch); } }
static void pwmWrite(int pin, int ch, int duty) { if (pin >= 0) ledcWrite(ch, duty); }
#endif

static void motorsInit() {
#if MOTOR_DRIVER == DRIVER_2PWM
  pwmSetup(PIN_L_IN1, 0); pwmSetup(PIN_L_IN2, 1); pwmSetup(PIN_R_IN1, 2); pwmSetup(PIN_R_IN2, 3);
#else
  pinMode(PIN_L_IN1, OUTPUT); pinMode(PIN_L_IN2, OUTPUT); pinMode(PIN_R_IN1, OUTPUT); pinMode(PIN_R_IN2, OUTPUT);
  pwmSetup(PIN_L_EN, 0); pwmSetup(PIN_R_EN, 1);
#endif
}

static void motor(bool left, int speed) {   // speed -255..255, + = forward
  if ((left && INVERT_LEFT) || (!left && INVERT_RIGHT)) speed = -speed;
  speed = constrain(speed, -255, 255);
  int a = left ? PIN_L_IN1 : PIN_R_IN1, b = left ? PIN_L_IN2 : PIN_R_IN2;
#if MOTOR_DRIVER == DRIVER_2PWM
  int ca = left ? 0 : 2, cb = left ? 1 : 3;
  if (speed >= 0) { pwmWrite(a, ca, speed); pwmWrite(b, cb, 0); }
  else            { pwmWrite(a, ca, 0);     pwmWrite(b, cb, -speed); }
#else
  digitalWrite(a, speed > 0); digitalWrite(b, speed < 0);
  pwmWrite(left ? PIN_L_EN : PIN_R_EN, left ? 0 : 1, abs(speed));
#endif
}
static void drive(int l, int r) { motor(true, l); motor(false, r); }
static void stopMotors() { drive(0, 0); }

// ============================================================================
//  SENSORS
// ============================================================================
static const int LINE_PINS[5] = {PIN_LINE_D1, PIN_LINE_D2, PIN_LINE_D3, PIN_LINE_D4, PIN_LINE_D5};
static uint8_t lineBits() {                  // bit i = sensor D(i+1) over black
  uint8_t b = 0;
  for (int i = 0; i < 5; i++) if ((digitalRead(LINE_PINS[i]) == HIGH) == (LINE_ACTIVE_HIGH == 1)) b |= 1 << i;
  return b;
}
static int countBits(uint8_t b) { int n = 0; for (int i = 0; i < 5; i++) n += (b >> i) & 1; return n; }
static bool centreOn(uint8_t b) { return b & 0b00100; }

static float distanceCm() {                  // <0 = no echo / out of range
  digitalWrite(PIN_US_TRIG, LOW); delayMicroseconds(3);
  digitalWrite(PIN_US_TRIG, HIGH); delayMicroseconds(10);
  digitalWrite(PIN_US_TRIG, LOW);
  unsigned long us = pulseIn(PIN_US_ECHO, HIGH, (unsigned long)(US_MAX_CM * 58.0 * 1.3));
  if (us == 0) return -1;
  float cm = us / 58.0f;
  if (cm > US_MAX_CM) return -1;
  return cm < 1.0f ? 1.0f : cm;              // an echo this short means "touching", never "far"
}

// ============================================================================
//  CONTROL (core 1): state machine
// ============================================================================
enum State : uint8_t { IDLE, START_ACT, START_CHECK, FOLLOW, CENTER, TURN_AT_MARK, BLOCKED, TURN_AROUND, WAIT_REROUTE,
                       SCAN_OUT, SCAN_MEASURE, SCAN_BACK, ARRIVED, ESTOP, FAULT };
static const char* STATE_NAME[] = {"IDLE", "START_ACT", "START_CHECK", "FOLLOW", "CENTER", "TURN_AT_MARK", "BLOCKED", "TURN_AROUND",
                                   "WAIT_REROUTE", "SCAN_OUT", "SCAN_MEASURE", "SCAN_BACK", "ARRIVED", "ESTOP", "FAULT"};

static State state = IDLE, frozenState = IDLE;
static char task[32] = "";
static uint8_t marks[MAX_MARKS], nMarks = 0, marksPassed = 0, startAct = A_GO;
static bool scanReq = false, scanLeft = true;

// virtual clock: stands still during an emergency stop, so every timer resumes where it was
static uint32_t pausedTotal = 0, estopAt = 0;
static uint32_t vnow() { return millis() - pausedTotal; }

static uint32_t phaseStart = 0, lastLineSeen = 0, lastMarkAt = 0, lastUs = 0, offSince = 0;
static int lastErr = 0, closeHits = 0, farHits = 0, markReads = 0, pivotDir = 1;
static bool prevMark = false, blockedAtStart = false;
static float scanMin = 999;
static uint8_t pendingTurn = A_PASS;

static void enter(State s) {
  if (s != state) Serial.printf("[state] %s -> %s\n", STATE_NAME[state], STATE_NAME[s]);
  state = s;
  phaseStart = vnow();
  offSince = 0;
  closeHits = farHits = 0;
  if (s == FOLLOW) { lastLineSeen = vnow(); prevMark = countBits(lineBits()) >= MARK_MIN_SENSORS; markReads = 0; }
  if (s == IDLE || s == WAIT_REROUTE || s == ARRIVED || s == FAULT || s == ESTOP || s == BLOCKED) stopMotors();
}

// Freeze the state machine as it is (no enter(): its timers must survive until RESUME)
static void estop() {
  if (state == ESTOP) return;
  frozenState = state; estopAt = millis(); stopMotors();
  Serial.printf("[state] %s -> ESTOP (frozen)\n", STATE_NAME[state]);
  state = ESTOP;
}

static void fault(const char* why) {
  stopMotors();
  report("ERROR", task, -1, "", false, why);
  enter(FAULT);
}

static bool moving() {
  return state == START_ACT || state == START_CHECK || state == FOLLOW || state == CENTER || state == TURN_AT_MARK || state == TURN_AROUND ||
         state == SCAN_OUT || state == SCAN_MEASURE || state == SCAN_BACK || state == BLOCKED;
}

// Turn on the spot until the centre sensor is back on a line (after first leaving it). true = done.
// minMs: ignore any line seen before this (a U-turn must not stop on the cross bar of a mark at ~90 degrees).
static bool pivotToLine(int dir, uint32_t minMs = 0) {
  uint8_t b = lineBits();
  uint32_t t = vnow();
  if (t - phaseStart > PIVOT_TIMEOUT_MS) { fault("Turn timed out - line not found"); return false; }
  drive(-dir * PIVOT_SPEED, dir * PIVOT_SPEED);           // dir +1 = turn left
  if (t - phaseStart < minMs) { if (!centreOn(b) && !offSince) offSince = t; return false; }
  if (!centreOn(b)) { if (!offSince) offSince = t; return false; }
  if (offSince && t - offSince >= LEAVE_LINE_MS) { stopMotors(); return true; }
  if (offSince && t - offSince < LEAVE_LINE_MS) offSince = 0;   // flicker while still on the start line
  return false;
}

static void startRoute(const Command& c) {
  strlcpy(task, c.task, sizeof task);
  nMarks = c.nMarks; memcpy(marks, c.marks, nMarks);
  marksPassed = 0; startAct = c.startAct; scanReq = c.scan; scanLeft = c.scanLeft; lastErr = 0;
  report("ACK", task);
  if (nMarks == 0) {                         // already at the destination
    if (scanReq) { enter(SCAN_OUT); } else { report("ARRIVED", task); enter(ARRIVED); }
    return;
  }
  enter(START_ACT);
}

static void handleCommand(const Command& c) {
  if (!strcmp(c.type, "STOP")) {
    estop();
    return;
  }
  if (!strcmp(c.type, "RESUME")) {
    if (state == ESTOP) {
      pausedTotal += millis() - estopAt;
      State s = frozenState; state = s;      // keep the frozen phase timers
      Serial.printf("[state] resumed %s\n", STATE_NAME[s]);
      if (s == FOLLOW || s == CENTER) lastLineSeen = vnow();
    }
    return;
  }
  if (state == ESTOP) { Serial.println("[cmd] ignored during emergency stop"); return; }
  if (!strcmp(c.type, "CANCEL")) {
    if (!c.task[0] || !strcmp(c.task, task)) { stopMotors(); enter(IDLE); task[0] = 0; }
    return;
  }
  if (!strcmp(c.type, "NAVIGATE")) {
    if (!strcmp(c.task, task) && state != IDLE && state != FAULT) { report("ACK", task); return; }   // resend: same task
    if (moving()) { Serial.println("[cmd] busy - NAVIGATE ignored"); return; }
    startRoute(c);
    return;
  }
  if (!strcmp(c.type, "REROUTE")) { startRoute(c); return; }   // new route for the same task, from where it stands
}

// Line following with mark counting and obstacle check (may switch state).
static void followLine(bool countMarks, bool checkObstacle) {
  uint32_t t = vnow();
  uint8_t b = lineBits();
  int n = countBits(b);

  if (countMarks) {
    bool isMark = n >= MARK_MIN_SENSORS;
    markReads = isMark ? markReads + 1 : 0;
    bool confirmed = markReads >= MARK_CONFIRM_READS;
    if (confirmed && !prevMark && t - lastMarkAt > MARK_COOLDOWN_MS) {
      prevMark = true; lastMarkAt = t; marksPassed++;
      if (marksPassed > nMarks) { fault("Unexpected extra mark - robot lost count"); return; }
      uint8_t a = marks[marksPassed - 1];
      Serial.printf("[mark] %u/%u action %u\n", marksPassed, nMarks, a);
      if (a != A_PASS && a != A_GO) { pendingTurn = a; enter(CENTER); return; }
    } else if (!isMark) prevMark = false;
  }

  if (checkObstacle && t - lastUs >= 70) {
    lastUs = t;
    float d = distanceCm();
    closeHits = (d > 0 && d < OBSTACLE_CM) ? closeHits + 1 : 0;
    if (closeHits >= OBSTACLE_CONFIRM) {
      stopMotors(); blockedAtStart = false; report("BLOCKED", task); enter(BLOCKED); return;
    }
  }

  if (n == 0) {
    if (t - lastLineSeen > LINE_LOST_MS) { fault("Line lost"); return; }
  } else {
    lastLineSeen = t;
    if (n < MARK_MIN_SENSORS) {              // on a mark the position is meaningless: keep last steering
      int sum = 0;
      for (int i = 0; i < 5; i++) if (b & (1 << i)) sum += (i - 2);
      lastErr = (sum * 10) / n;              // -20..+20 (tenths)
    }
  }
  static int prevErr = 0;
  int corr = (KP * lastErr + KD * (lastErr - prevErr)) / 10;
  prevErr = lastErr;
  int base = (state == CENTER) ? BASE_SPEED * 3 / 4 : BASE_SPEED;
  drive(constrain(base + corr, -MAX_SPEED, MAX_SPEED), constrain(base - corr, -MAX_SPEED, MAX_SPEED));
}

static void arriveAtStop() {
  stopMotors();
  if (scanReq) { enter(SCAN_OUT); return; }
  report("ARRIVED", task);
  enter(ARRIVED);
}

static void controlStep() {
  uint32_t t = vnow();
  switch (state) {
    case IDLE: case ARRIVED: case WAIT_REROUTE: case FAULT: case ESTOP:
      stopMotors();
      break;

    case START_ACT:
      if (startAct == A_UTURN || startAct == A_LEFT || startAct == A_RIGHT) {
        if (pivotToLine(startAct == A_RIGHT ? -1 : 1, startAct == A_UTURN ? UTURN_MIN_MS : TURN_MIN_MS)) enter(START_CHECK);
      } else enter(START_CHECK);
      break;

    case START_CHECK:                        // look before leaving the mark: blocked here = still on the mark
      stopMotors();
      if (t - lastUs >= 70) {
        lastUs = t;
        float d = distanceCm();
        closeHits = (d > 0 && d < OBSTACLE_CM) ? closeHits + 1 : 0;
        if (closeHits >= OBSTACLE_CONFIRM) { blockedAtStart = true; report("BLOCKED", task); enter(BLOCKED); break; }
      }
      if (t - phaseStart >= 70 * (OBSTACLE_CONFIRM + 1)) { blockedAtStart = false; enter(FOLLOW); }
      break;

    case FOLLOW:
      followLine(true, true);
      break;

    case CENTER:                             // drive on so the wheels sit over the mark
      if (t - phaseStart >= MARK_CENTER_MS) {
        stopMotors();
        if (pendingTurn == A_STOP) arriveAtStop();
        else enter(TURN_AT_MARK);
      } else followLine(false, false);
      break;

    case TURN_AT_MARK:
      if (pivotToLine(pendingTurn == A_RIGHT ? -1 : 1, pendingTurn == A_UTURN ? UTURN_MIN_MS : TURN_MIN_MS)) enter(FOLLOW);
      break;

    case BLOCKED:
      if (t - lastUs >= 70) {
        lastUs = t;
        float d = distanceCm();
        farHits = (d < 0 || d >= OBSTACLE_CM + 2) ? farHits + 1 : 0;   // small hysteresis
        if (farHits >= CLEAR_CONFIRM) { report("OBSTACLE_CLEARED", task); enter(FOLLOW); break; }
      }
      if (t - phaseStart >= OBSTACLE_WAIT_MS) { Serial.println("[obstacle] still there - turning round"); enter(TURN_AROUND); }
      break;

    case TURN_AROUND:
      if (pivotToLine(1, UTURN_MIN_MS)) {
        report("PATH_BLOCKED", task, marksPassed, "", true,
               blockedAtStart ? "Blocked before leaving the mark; turned round" : "Obstacle did not clear; turned round",
               blockedAtStart);
        enter(WAIT_REROUTE);
      }
      break;

    case SCAN_OUT:                           // timed 90 degree turn towards the room
      drive(scanLeft ? -PIVOT_SPEED : PIVOT_SPEED, scanLeft ? PIVOT_SPEED : -PIVOT_SPEED);
      if (t - phaseStart >= PIVOT_90_MS) { stopMotors(); scanMin = 999; enter(SCAN_MEASURE); }
      break;

    case SCAN_MEASURE:
      if (t - lastUs >= 60) {
        lastUs = t;
        float d = distanceCm();
        if (d > 0 && d < scanMin) scanMin = d;
      }
      if (t - phaseStart >= SCAN_MEASURE_MS) { offSince = vnow(); state = SCAN_BACK; phaseStart = vnow(); }
      break;

    case SCAN_BACK:                          // turn back the other way until the line is under the centre
      if (pivotToLine(scanLeft ? -1 : 1)) {
        bool occ = scanMin < OCCUPIED_CM;
        char msg[40]; snprintf(msg, sizeof msg, "nearest %.0f cm", scanMin > 900 ? -1.0f : scanMin);
        report("ARRIVED", task, -1, occ ? "OCCUPIED" : "FREE", false, msg);
        enter(ARRIVED);
      }
      break;
  }
}

// ---------------------------------------------------------------- setup / loop
void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.println("\nSynapse Linker EDi-01 firmware");
  motorsInit(); stopMotors();
  for (int i = 0; i < 5; i++) pinMode(LINE_PINS[i], INPUT);
  pinMode(PIN_US_TRIG, OUTPUT); pinMode(PIN_US_ECHO, INPUT);
  pinMode(PIN_ESTOP, INPUT_PULLUP);
  pinMode(PIN_LED, OUTPUT);
  cmdQ = xQueueCreate(8, sizeof(Command));
  statusQ = xQueueCreate(16, sizeof(Status));
  lastCommOkMs = millis();
  xTaskCreatePinnedToCore(networkTask, "net", 12288, nullptr, 1, nullptr, 0);
}

void loop() {
  static uint32_t lastStep = 0, btnDown = 0, lastLog = 0;
  Command c;
  while (xQueueReceive(cmdQ, &c, 0) == pdTRUE) handleCommand(c);

  // BOOT button = emergency stop (debounced)
  if (digitalRead(PIN_ESTOP) == LOW) {
    if (!btnDown) btnDown = millis();
    if (millis() - btnDown > 40 && state != ESTOP) { estop(); report("ESTOP_PRESSED", task); }
  } else btnDown = 0;

  // lost the backend while moving: stop and stay stopped (the backend marks the task failed)
  if (moving() && state != ESTOP && millis() - lastCommOkMs > COMM_TIMEOUT_MS) {
    stopMotors();
    Serial.println("[safety] backend unreachable - stopped");
    enter(FAULT);
  }

  if (millis() - lastStep >= 5) { lastStep = millis(); controlStep(); }

  // LED: solid while moving, fast blink when stopped by safety, slow blink idle
  bool fast = state == FAULT || state == ESTOP || state == BLOCKED || state == WAIT_REROUTE;
  uint32_t per = moving() && !fast ? 0 : (fast ? 150 : 800);
  digitalWrite(PIN_LED, per == 0 ? HIGH : ((millis() / per) % 2));

  if (millis() - lastLog > 2000) {
    lastLog = millis();
    Serial.printf("[%s] task=%s marks %u/%u wifi=%d line=0x%02x\n", STATE_NAME[state], task, marksPassed, nMarks,
                  (int)wifiUp, lineBits());
  }
}
