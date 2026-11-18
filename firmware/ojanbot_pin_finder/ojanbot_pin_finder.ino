// ============================================================================
//  OjanBot pin finder: where are the Maker Line and the ultrasonic actually plugged in?
//  Needs no config.h. Upload, open Serial Monitor at 115200 (Newline), then:
//    s  6 s: slide the robot slowly back and forth across the black tape.
//       Lists every pin that changes (digital) and every ADC pin whose voltage swings (analog AN wire).
//    e  tries each Grove-port pair as ultrasonic TRIG/ECHO (both ways round). Hold a hand ~10 cm in front.
//  Motors are never driven. Motor pins 12/13/14/27 and the flash/serial pins are never touched.
// ============================================================================
#include <Arduino.h>

static const int IN_PINS[] = {2, 4, 5, 15, 16, 17, 18, 19, 21, 22, 23, 25, 26, 32, 33, 34, 35, 36, 39};
static const int N_IN = sizeof(IN_PINS) / sizeof(IN_PINS[0]);
static const int ADC_PINS[] = {32, 33, 34, 35, 36, 39, 25, 26, 4, 2, 15};   // ADC-capable
static const int N_ADC = sizeof(ADC_PINS) / sizeof(ADC_PINS[0]);
// Robo ESP32 Grove pairs (and a few common hand-wired pairs)
static const int PAIRS[][2] = {{17, 16}, {21, 22}, {26, 25}, {33, 32}, {5, 18}, {19, 23}, {4, 15}};
static const int N_PAIRS = sizeof(PAIRS) / sizeof(PAIRS[0]);

static void scanLine() {
  Serial.println("Slide the robot slowly back and forth across the tape for 6 s...");
  for (int i = 0; i < N_IN; i++) pinMode(IN_PINS[i], INPUT);
  uint32_t hi = 0, lo = 0;
  int amin[N_ADC], amax[N_ADC];
  for (int i = 0; i < N_ADC; i++) { amin[i] = 4095; amax[i] = 0; }
  uint32_t t0 = millis();
  while (millis() - t0 < 6000) {
    for (int i = 0; i < N_IN; i++) (digitalRead(IN_PINS[i]) ? hi : lo) |= 1UL << i;
    for (int i = 0; i < N_ADC; i++) { int v = analogRead(ADC_PINS[i]); amin[i] = min(amin[i], v); amax[i] = max(amax[i], v); }
    delay(3);
  }
  int found = 0;
  Serial.println("Digital pins that changed (these are line-sensor D outputs):");
  for (int i = 0; i < N_IN; i++) if ((hi & lo) & (1UL << i)) { Serial.printf("  GPIO %d\n", IN_PINS[i]); found++; }
  if (!found) Serial.println("  none");
  Serial.println("ADC pins whose voltage swung by more than 0.5 V (could be the AN analog wire):");
  int af = 0;
  for (int i = 0; i < N_ADC; i++) if (amax[i] - amin[i] > 620) {
    Serial.printf("  GPIO %d: %.2f V .. %.2f V\n", ADC_PINS[i], amin[i] * 3.3 / 4095, amax[i] * 3.3 / 4095); af++;
  }
  if (!af) Serial.println("  none");
  if (!found && !af) Serial.println("=> Nothing changes: the Maker Line is unpowered or not connected to the ESP32. Check its VCC/GND.");
  else if (!found) Serial.println("=> Only an analog signal: the Maker Line is on its AN wire. D1-D5 still need wiring.");
  else Serial.printf("=> %d digital line pins found. Tell Claude which GPIOs, left to right.\n", found);
}

static float ping(int trig, int echo) {
  pinMode(trig, OUTPUT); pinMode(echo, INPUT);
  digitalWrite(trig, LOW); delayMicroseconds(3);
  digitalWrite(trig, HIGH); delayMicroseconds(10); digitalWrite(trig, LOW);
  unsigned long us = pulseIn(echo, HIGH, 12000);
  pinMode(trig, INPUT);
  return us ? us / 58.0f : -1;
}

static void scanEcho() {
  Serial.println("Hold a hand about 10 cm in front of the ultrasonic sensor...");
  delay(1500);
  bool any = false;
  for (int p = 0; p < N_PAIRS; p++) for (int dir = 0; dir < 2; dir++) {
    int trig = PAIRS[p][dir], echo = PAIRS[p][1 - dir];
    int ok = 0; float last = -1;
    for (int k = 0; k < 5; k++) { float d = ping(trig, echo); if (d > 1 && d < 200) { ok++; last = d; } delay(30); }
    if (ok >= 3) { Serial.printf("  ECHO FOUND: TRIG = GPIO %d, ECHO = GPIO %d (about %.0f cm)\n", trig, echo, last); any = true; }
  }
  if (!any) Serial.println("  No pair answered: the ultrasonic is unpowered, not plugged into a Grove port, or on other pins.");
}

void setup() {
  Serial.begin(115200);
  delay(300);
  Serial.println("\nOjanBot pin finder. Commands: s (line sensor), e (ultrasonic)");
}

void loop() {
  if (!Serial.available()) return;
  char c = Serial.read();
  if (c == 's') scanLine();
  else if (c == 'e') scanEcho();
  else if (c != '\n' && c != '\r') Serial.println("Commands: s (line sensor), e (ultrasonic)");
}
