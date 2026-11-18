// ============================================================================
//  Synapse Linker - EDi-01 firmware for the OjanBot ESP32 (line-following guide robot)
//
//  Track: black tape graph (the 60 x 60 cm table is a double loop: an outer rectangle plus a
//  middle corridor joined by two T-junctions). The robot has no map; the backend plans the route
//  and sends the list of tape FEATURES the robot will cross, in order:
//      station  = a short bar ACROSS the line   -> sensors see tape on both sides ("C")
//      junction = a branch on ONE side ("L"/"R"), or the top of a T ("C", and no line ahead)
//  with what to do at each (PASS / LEFT / RIGHT / U_TURN / STOP) and the taped distance to it.
//  The robot tells them apart by the sensor pattern, by which feature it expects next, and by how
//  far it has driven (patterns before ~55% of the expected distance are ignored: bends can look
//  like a branch; driving well past the expected distance = missed marker -> safe stop).
//
//    GET  /robot/EDi-01/next-command     POST /robot/status
//  At the destination: wheels onto the bar, turn 90 degrees to the room, ultrasonic check,
//  turn back, report ARRIVED {room_scan: OCCUPIED|FREE}.
//  Obstacle ahead: stop (BLOCKED). Cleared within 5 s -> carry on. Still there -> turn round on
//  the line, report PATH_BLOCKED {marks_passed, on_mark}, wait for a REROUTE. Never steers round.
//  Safe stops: emergency stop (dashboard / BOOT button) until Resume; 6 s without the backend;
//  line lost; turn cannot find the line inside its angle window; unexpected or missed feature.
//
//  Libraries: ESP32 board package 2.x or 3.x, ArduinoJson 7.x. Board: "ESP32 Dev Module".
//  Settings live in config.h. Test with firmware/ojanbot_hw_test and a DUMMY weight first.
// ============================================================================
#include <esp_arduino_version.h>   // ESP_ARDUINO_VERSION_MAJOR on core 2.x and 3.x
#include <WiFi.h>
#include <HTTPClient.h>
#include <WiFiClientSecure.h>
#include <ArduinoJson.h>
#include "config.h"

// Stop with a clear message if this folder still has an old config.h next to a newer sketch.
#if !defined(FEATURE_CONFIRM) || !defined(SENSOR_AHEAD_CM) || !defined(PIN_LINE_D5) || !defined(LINE_MODE)
#error "config.h is out of date: copy firmware/ojanbot_synapse/config.h from the repo into this sketch's folder"
#endif

// ---------------------------------------------------------------- shared types
enum Act : uint8_t { A_PASS, A_STOP, A_LEFT, A_RIGHT, A_UTURN, A_GO };
static const uint8_t MAX_MARKS = 24;

struct Feature {
  char kind;              // 'S' station, 'J' junction
  char pattern;           // 'C' both sides, 'L' / 'R' one side
  uint8_t act;
  float distCm;           // taped distance from the previous feature (0 = unknown)
  float maxCm;            // stop as "missed" after driving this far
};

struct Command {
  char type[12];          // NAVIGATE REROUTE STOP RESUME CANCEL
  char task[32];
  uint8_t startAct;
  uint8_t nMarks;
  Feature marks[MAX_MARKS];
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
  filter["features"] = true; filter["mark_actions"] = true;
  filter["scan_room_on_arrival"] = true; filter["scan_side"] = true;
  JsonDocument doc;
  if (deserializeJson(doc, payload, DeserializationOption::Filter(filter))) { Serial.println("[poll] bad JSON"); return; }
  const char* type = doc["command"] | "NONE";
  if (!strcmp(type, "NONE")) return;

  Command c = {};
  strlcpy(c.type, type, sizeof c.type);
  strlcpy(c.task, doc["task_id"] | "", sizeof c.task);
  c.startAct = actOf(doc["start_action"] | "GO");
  JsonArray fs = doc["features"].as<JsonArray>();
  for (JsonVariant v : fs) {
    if (c.nMarks >= MAX_MARKS) break;
    Feature& f = c.marks[c.nMarks++];
    f.kind = (v["t"] | "S")[0];
    f.pattern = (v["p"] | "C")[0];
    f.act = actOf(v["a"] | "PASS");
    f.distCm = v["d"] | 0.0f;
    f.maxCm = v["m"] | 0.0f;
  }
  if (c.nMarks == 0) {                       // older backend: actions only, no distances
    JsonArray m = doc["mark_actions"].as<JsonArray>();
    for (JsonVariant v : m) {
      if (c.nMarks >= MAX_MARKS) break;
      c.marks[c.nMarks++] = Feature{'S', 'C', actOf(v.as<const char*>()), 0.0f, 0.0f};
    }
  }
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
static int anaErr = 0;                       // analog mode: line position, -20 (left) .. +20 (right)
static uint8_t lineBits() {                  // bit i = sensor D(i+1) over black
#if LINE_MODE == LINE_MODE_ANALOG
  // Maker Line AN: < 10% Vcc no line, 10-90% line position left..right, > 90% all sensors (bar / top of T).
  // Turned into the same 5-bit picture: 00000, a single bit at the line position, or 11111.
  float v = analogRead(PIN_LINE_AN) / 4095.0f;
  if (v < AN_NO_LINE) return 0;
  if (v > AN_CROSS) return 0b11111;
  float p = (v - 0.5f) / 0.4f;               // -1 .. +1
  p = constrain(p, -1.0f, 1.0f);
  anaErr = (int)(p * 20);
  return 1 << (int)lroundf(2 + 2 * p);
#else
  uint8_t b = 0;
  for (int i = 0; i < 5; i++) if ((digitalRead(LINE_PINS[i]) == HIGH) == (LINE_ACTIVE_HIGH == 1)) b |= 1 << i;
  return b;
#endif
}
#define ANALOG_LINE (LINE_MODE == LINE_MODE_ANALOG)
static int countBits(uint8_t b) { int n = 0; for (int i = 0; i < 5; i++) n += (b >> i) & 1; return n; }
static bool centreOn(uint8_t b) { return b & 0b00100; }
static bool lineAhead(uint8_t b) { return b & 0b01110; }

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
enum State : uint8_t { IDLE, START_ACT, START_CHECK, FOLLOW, CENTER, TURN_AT_NODE, BLOCKED, TURN_AROUND,
                       WAIT_REROUTE, SCAN_OUT, SCAN_MEASURE, SCAN_BACK, ARRIVED, ESTOP, FAULT };
static const char* STATE_NAME[] = {"IDLE", "START_ACT", "START_CHECK", "FOLLOW", "CENTER", "TURN_AT_NODE",
                                   "BLOCKED", "TURN_AROUND", "WAIT_REROUTE", "SCAN_OUT", "SCAN_MEASURE",
                                   "SCAN_BACK", "ARRIVED", "ESTOP", "FAULT"};

static State state = IDLE, frozenState = IDLE;
static char task[32] = "";
static Feature feats[MAX_MARKS];
static uint8_t nFeats = 0, passed = 0, startAct = A_GO;
static bool scanReq = false, scanLeft = true;

// virtual clock: stands still during an emergency stop, so every timer resumes where it was
static uint32_t pausedTotal = 0, estopAt = 0;
static uint32_t vnow() { return millis() - pausedTotal; }

static uint32_t phaseStart = 0, lastLineSeen = 0, lastUs = 0, offSince = 0, lastStepAt = 0;
static int lastErr = 0, prevErr = 0, closeHits = 0, farHits = 0;
static bool blockedAtStart = false;
static float scanMin = 999;
static uint8_t pendingAct = A_PASS;
static int curL = 0, curR = 0;               // last wheel commands, for the distance estimate
static float travelled = 0;                  // cm the AXLE has driven since the last feature node

// feature recognition
static bool inFeature = false;
static uint8_t featReads = 0, endReads = 0, featUnion = 0;
static uint32_t featStart = 0;
static float featStartCm = 0;
static float blockedAtCm = -1;               // how far past the last node it was when it turned round

static void setWheels(int l, int r) { curL = l; curR = r; drive(l, r); }
static void halt() { curL = curR = 0; stopMotors(); }

static void enter(State s) {
  if (s != state) Serial.printf("[state] %s -> %s\n", STATE_NAME[state], STATE_NAME[s]);
  state = s;
  phaseStart = vnow();
  offSince = 0;
  closeHits = farHits = 0;
  if (s == FOLLOW) { lastLineSeen = vnow(); inFeature = false; featReads = endReads = 0; }
  if (s == IDLE || s == WAIT_REROUTE || s == ARRIVED || s == FAULT || s == ESTOP || s == BLOCKED ||
      s == START_CHECK) halt();
}

// Freeze the state machine as it is (no enter(): its timers must survive until RESUME)
static void estop() {
  if (state == ESTOP) return;
  frozenState = state; estopAt = millis(); halt();
  Serial.printf("[state] %s -> ESTOP (frozen)\n", STATE_NAME[state]);
  state = ESTOP;
}

static void fault(const char* why) {
  halt();
  report("ERROR", task, -1, "", false, why);
  enter(FAULT);
}

static bool moving() {
  return state == START_ACT || state == START_CHECK || state == FOLLOW || state == CENTER || state == TURN_AT_NODE ||
         state == TURN_AROUND || state == SCAN_OUT || state == SCAN_MEASURE || state == SCAN_BACK || state == BLOCKED;
}

// Turn on the spot until the centre sensor is back on a line, accepting it only between minMs and maxMs
// of turning (the angle window), so a turn cannot stop on the wrong tape. true = done.
static bool pivotToLine(int dir, uint32_t minMs, uint32_t maxMs) {
  uint8_t b = lineBits();
  uint32_t t = vnow(), el = t - phaseStart;
  if (el > maxMs || el > PIVOT_TIMEOUT_MS) { fault("Turn did not find the line in its angle window"); return false; }
  setWheels(-dir * PIVOT_SPEED, dir * PIVOT_SPEED);       // dir +1 = turn left
  if (!centreOn(b)) { if (!offSince) offSince = t; return false; }
  if (el < minMs) return false;
  if (offSince && t - offSince >= LEAVE_LINE_MS) { halt(); return true; }
  if (offSince && t - offSince < LEAVE_LINE_MS) offSince = 0;   // flicker while still on the start line
  return false;
}
static uint32_t turnMin(uint8_t a) { return a == A_UTURN ? UTURN_MIN_MS : TURN_MIN_MS; }
static uint32_t turnMax(uint8_t a) { return a == A_UTURN ? UTURN_MAX_MS : TURN_MAX_MS; }
static int turnDir(uint8_t a) { return a == A_RIGHT ? -1 : 1; }

static void startRoute(const Command& c) {
  strlcpy(task, c.task, sizeof task);
  nFeats = c.nMarks; memcpy(feats, c.marks, sizeof(Feature) * nFeats);
  passed = 0; startAct = c.startAct; scanReq = c.scan; scanLeft = c.scanLeft; lastErr = prevErr = 0;
  travelled = 0;
  // After turning round mid-corridor the backend cannot know how far back the node is; the robot does.
  if (nFeats && feats[0].distCm <= 0 && blockedAtCm > 0) feats[0].distCm = blockedAtCm;
  blockedAtCm = -1;
  report("ACK", task);
  if (nFeats == 0) {                         // already at the destination
    if (scanReq) { enter(SCAN_OUT); } else { report("ARRIVED", task); enter(ARRIVED); }
    return;
  }
  enter(START_ACT);
}

static void handleCommand(const Command& c) {
  if (!strcmp(c.type, "STOP")) { estop(); return; }
  if (!strcmp(c.type, "RESUME")) {
    if (state == ESTOP) {
      pausedTotal += millis() - estopAt;
      state = frozenState;                   // keep the frozen phase timers
      Serial.printf("[state] resumed %s\n", STATE_NAME[state]);
      if (state == FOLLOW || state == CENTER) lastLineSeen = vnow();
      lastStepAt = millis();
    }
    return;
  }
  if (state == ESTOP) { Serial.println("[cmd] ignored during emergency stop"); return; }
  if (!strcmp(c.type, "CANCEL")) {
    if (!c.task[0] || !strcmp(c.task, task)) { halt(); enter(IDLE); task[0] = 0; }
    return;
  }
  if (!strcmp(c.type, "NAVIGATE")) {
    if (!strcmp(c.task, task) && state != IDLE && state != FAULT) { report("ACK", task); return; }   // resend
    if (moving()) { Serial.println("[cmd] busy - NAVIGATE ignored"); return; }
    startRoute(c);
    return;
  }
  if (!strcmp(c.type, "REROUTE")) { startRoute(c); return; }   // new route for the same task, from where it stands
}

// A reading "looks like a feature" when tape covers 4+ sensors, or an OUTER sensor and the CENTRE
// together (a bend can light an outer sensor, but not the centre as well).
static bool featureLike(uint8_t b) {
  return countBits(b) >= 4 || ((b & 0b00001) && (b & 0b00100)) || ((b & 0b10000) && (b & 0b00100));
}
// What the whole crossing looked like: tape at BOTH outer sensors = bar / top of a T; one side = branch.
static char patternOf(uint8_t u) {
  bool l = u & 0b00001, r = u & 0b10000;
  return (l && r) ? 'C' : l ? 'L' : r ? 'R' : '?';
}

// Decide what to do with a finished crossing. Returns false if it stopped with a fault.
static bool onCrossing(char seen, float lengthCm) {
#if ANALOG_LINE
  // Side branches cannot be seen on the analog wire; a PASS one that is still pending when the next
  // bar arrives has simply been driven past.
  while (seen == 'C' && passed + 1 < nFeats && feats[passed].pattern != 'C' &&
         (feats[passed].act == A_PASS || feats[passed].act == A_GO)) {
    Serial.printf("[feature] %u/%u junction passed (by distance)\n", passed + 1, nFeats);
    passed++;
  }
#endif
  const Feature& f = feats[passed];
  if (lengthCm > FEATURE_MAX_CM) {           // tape lasted too long to be a crossing: a bend
    Serial.printf("[feature] ignored %c over %.1f cm (bend)\n", seen, lengthCm);
    return true;
  }
  if (f.pattern == 'C' && seen != 'C') {     // stations / T-tops light both sides; one side = bend
    Serial.printf("[feature] ignored %c while expecting a bar\n", seen);
    return true;
  }
  if (f.pattern != 'C' && seen != f.pattern) {
    if (seen == 'C') {                       // a full bar where a side branch should be: map or count is wrong
      char m[64]; snprintf(m, sizeof m, "Saw a bar where branch %c (#%u) was expected", f.pattern, passed + 1);
      fault(m); return false;
    }
    Serial.printf("[feature] ignored %c while expecting branch %c\n", seen, f.pattern);
    return true;
  }
  Serial.printf("[feature] %u/%u %s (%c) %s\n", passed + 1, nFeats, f.kind == 'J' ? "junction" : "station", seen,
                f.act == A_STOP ? "STOP" : f.act == A_LEFT ? "LEFT" : f.act == A_RIGHT ? "RIGHT" :
                f.act == A_UTURN ? "U_TURN" : "PASS");
  travelled = (travelled - featStartCm) - SENSOR_AHEAD_CM;   // distance now counts from this node
  passed++;
  if (f.act == A_PASS || f.act == A_GO) return true;
  pendingAct = f.act;
  enter(CENTER);
  phaseStart = featStart;                    // centring time counts from when the sensors reached it
  return true;
}

static void followLine(bool detect, bool checkObstacle) {
  uint32_t t = vnow();
  uint8_t b = lineBits();
  int n = countBits(b);

#if ANALOG_LINE
  bool holdSteer = false;
  if (detect && passed < nFeats && feats[passed].pattern != 'C' && !inFeature) {
    // Side branch: the analog wire cannot show it, so go by distance from the last bar.
    const Feature& f = feats[passed];
    float reach = f.distCm - SENSOR_AHEAD_CM;           // sensors over the junction
    holdSteer = fabsf(travelled - reach) < 3.0f;          // the branch tape would pull the steering
    if (f.act == A_PASS || f.act == A_GO) {
      if (travelled >= reach) {
        Serial.printf("[feature] %u/%u junction passed (by distance)\n", passed + 1, nFeats);
        passed++; travelled -= f.distCm;
      }
    } else if (travelled >= f.distCm) {                 // wheels over the junction: turn
      Serial.printf("[feature] %u/%u junction %s (by distance)\n", passed + 1, nFeats,
                    f.act == A_LEFT ? "LEFT" : f.act == A_RIGHT ? "RIGHT" : "U_TURN");
      passed++; pendingAct = f.act; travelled = 0; halt(); enter(TURN_AT_NODE); return;
    }
  }
#endif
  if (detect && passed < nFeats) {
    const Feature& f = feats[passed];
    // the sensors reach the node when the axle is SENSOR_AHEAD_CM short of it
    float frac = f.pattern == 'C' ? GATE_FRACTION_BAR : GATE_FRACTION_BRANCH;
    float gate = f.distCm > 0 ? f.distCm * frac - SENSOR_AHEAD_CM : -1e9f;
#if ANALOG_LINE
    gate = -1e9f;      // on the AN wire only a bar / T-top reads "all on", so no bend can look like one
#endif
    bool like = featureLike(b);
    if (!inFeature) {
      if (like && travelled >= gate) {
        if (++featReads >= FEATURE_CONFIRM) {
          inFeature = true; featStart = t; featStartCm = travelled; featUnion = b; endReads = 0;
        }
      } else featReads = 0;
      float maxCm = f.maxCm;
#if ANALOG_LINE
      maxCm = maxCm * 1.25f + 4;  // distances after a junction passed by dead reckoning are less certain
#endif
      if (!inFeature && f.maxCm > 0 && travelled > maxCm) {
        char m[60]; snprintf(m, sizeof m, "Missed feature #%u (drove %.0f cm)", passed + 1, travelled);
        fault(m); return;
      }
    } else {
      featUnion |= b;
      if (!like) {
        if (++endReads >= FEATURE_END_READS) {
          inFeature = false; featReads = 0;
          if (!onCrossing(patternOf(featUnion), travelled - featStartCm)) return;
          if (state != FOLLOW) return;
        }
      } else endReads = 0;
    }
  }

  if (checkObstacle && t - lastUs >= 70) {
    lastUs = t;
    float d = distanceCm();
    closeHits = (d > 0 && d < OBSTACLE_CM) ? closeHits + 1 : 0;
    if (closeHits >= OBSTACLE_CONFIRM) {
      halt(); blockedAtStart = false; report("BLOCKED", task); enter(BLOCKED); return;
    }
  }

  if (n == 0) {
    if (state == CENTER) lastErr = prevErr = 0;   // past the top of a T there is no line: drive straight on
    else if (t - lastLineSeen > LINE_LOST_MS) { fault("Line lost"); return; }
  } else {
    lastLineSeen = t;
    if (!featureLike(b)) {                   // on a feature the position is meaningless: keep steering
#if ANALOG_LINE
      if (!holdSteer) lastErr = anaErr;
#else
      int sum = 0;
      for (int i = 0; i < 5; i++) if (b & (1 << i)) sum += (i - 2);
      lastErr = (sum * 10) / n;              // -20..+20 (tenths)
#endif
    }
  }
  int corr = (KP * lastErr + KD * (lastErr - prevErr)) / 10;
  prevErr = lastErr;
  int base = (state == CENTER) ? BASE_SPEED * 3 / 4 : BASE_SPEED;
  setWheels(constrain(base + corr, -MAX_SPEED, MAX_SPEED), constrain(base - corr, -MAX_SPEED, MAX_SPEED));
}

static void afterCentring() {
  halt();
  uint8_t b = lineBits();
  const Feature& f = feats[passed - 1];
  if (CHECK_LINE_AHEAD) {
    bool ahead = lineAhead(b);
    if (f.kind == 'J' && f.pattern == 'C' && ahead) { fault("Expected the top of a T but the line goes on"); return; }
    if (f.kind == 'S' && pendingAct == A_STOP && !ahead)
      Serial.println("[feature] note: no line ahead of this station (dead end?)");
  }
  travelled = 0;
  if (pendingAct == A_STOP) {
    if (scanReq) { enter(SCAN_OUT); return; }
    report("ARRIVED", task); enter(ARRIVED); return;
  }
  enter(TURN_AT_NODE);
}

static void controlStep() {
  uint32_t t = vnow();
  // distance estimate (axle), only while driving forward along the line
  uint32_t ms = millis(), dt = ms - lastStepAt; lastStepAt = ms;
  if ((state == FOLLOW || state == CENTER) && dt < 100)
    travelled += CM_PER_S * ((curL + curR) / 2.0f / BASE_SPEED) * dt / 1000.0f;

  switch (state) {
    case IDLE: case ARRIVED: case WAIT_REROUTE: case FAULT: case ESTOP:
      halt();
      break;

    case START_ACT:
      if (startAct == A_UTURN || startAct == A_LEFT || startAct == A_RIGHT) {
        if (pivotToLine(turnDir(startAct), turnMin(startAct), turnMax(startAct))) enter(START_CHECK);
      } else enter(START_CHECK);
      break;

    case START_CHECK:                        // look before leaving the node: blocked here = still on it
      halt();
      if (t - lastUs >= 70) {
        lastUs = t;
        float d = distanceCm();
        closeHits = (d > 0 && d < OBSTACLE_CM) ? closeHits + 1 : 0;
        if (closeHits >= OBSTACLE_CONFIRM) { blockedAtStart = true; report("BLOCKED", task); enter(BLOCKED); break; }
      }
      if (t - phaseStart >= 70 * (OBSTACLE_CONFIRM + 1)) { blockedAtStart = false; travelled = 0; enter(FOLLOW); }
      break;

    case FOLLOW:
      followLine(true, true);
      break;

    case CENTER:                             // drive on so the wheels sit over the feature
      if (t - phaseStart >= MARK_CENTER_MS) afterCentring();
      else followLine(false, false);
      break;

    case TURN_AT_NODE:
      if (pivotToLine(turnDir(pendingAct), turnMin(pendingAct), turnMax(pendingAct))) { travelled = 0; enter(FOLLOW); }
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
      if (pivotToLine(1, UTURN_MIN_MS, UTURN_MAX_MS)) {
        blockedAtCm = blockedAtStart ? -1 : max(travelled, 0.0f);
        report("PATH_BLOCKED", task, passed, "", true,
               blockedAtStart ? "Blocked before leaving the node; turned round" : "Obstacle did not clear; turned round",
               blockedAtStart);
        enter(WAIT_REROUTE);
      }
      break;

    case SCAN_OUT:                           // timed 90 degree turn towards the room
      setWheels(scanLeft ? -PIVOT_SPEED : PIVOT_SPEED, scanLeft ? PIVOT_SPEED : -PIVOT_SPEED);
      if (t - phaseStart >= PIVOT_90_MS) { halt(); scanMin = 999; enter(SCAN_MEASURE); }
      break;

    case SCAN_MEASURE:
      if (t - lastUs >= 60) {
        lastUs = t;
        float d = distanceCm();
        if (d > 0 && d < scanMin) scanMin = d;
      }
      if (t - phaseStart >= SCAN_MEASURE_MS) { state = SCAN_BACK; phaseStart = vnow(); offSince = vnow(); }
      break;

    case SCAN_BACK:                          // turn back until the line is under the centre (within ~150 deg)
      if (pivotToLine(scanLeft ? -1 : 1, 0, TURN_MAX_MS)) {
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
#if ANALOG_LINE
  pinMode(PIN_LINE_AN, INPUT);
#else
  for (int i = 0; i < 5; i++) pinMode(LINE_PINS[i], INPUT);
#endif
  pinMode(PIN_US_TRIG, OUTPUT); pinMode(PIN_US_ECHO, INPUT);
  pinMode(PIN_ESTOP, INPUT_PULLUP);
#if PIN_LED >= 0
  pinMode(PIN_LED, OUTPUT);
#endif
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
    halt();
    Serial.println("[safety] backend unreachable - stopped");
    enter(FAULT);
  }

  if (millis() - lastStep >= 5) { lastStep = millis(); controlStep(); }

  // LED: solid while moving, fast blink when stopped by safety, slow blink idle
  bool fast = state == FAULT || state == ESTOP || state == BLOCKED || state == WAIT_REROUTE;
  uint32_t per = moving() && !fast ? 0 : (fast ? 150 : 800);
#if PIN_LED >= 0
  digitalWrite(PIN_LED, per == 0 ? HIGH : ((millis() / per) % 2));
#else
  (void)per;
#endif

  if (millis() - lastLog > 2000) {
    lastLog = millis();
    Serial.printf("[%s] task=%s features %u/%u driven %.1f cm wifi=%d line=0x%02x\n", STATE_NAME[state], task,
                  passed, nFeats, travelled, (int)wifiUp, lineBits());
  }
}
