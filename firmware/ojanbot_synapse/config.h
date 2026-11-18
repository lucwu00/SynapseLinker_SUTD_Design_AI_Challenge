// ============================================================================
//  Synapse Linker - EDi-01 (OjanBot ESP32) settings.  Edit this file only.
//  Every value marked  >>> CHECK  must be confirmed on YOUR robot with the
//  hardware test sketch (firmware/ojanbot_hw_test) before running a route.
// ============================================================================
#pragma once

// ---------------------------------------------------------------- network
// Demo day: the system laptop runs the backend and a Windows Mobile Hotspot.
// The ESP32 joins that hotspot (2.4 GHz only!).
#define WIFI_SSID      "SYNAPSE-DEMO"          // >>> CHECK  hotspot name
#define WIFI_PASSWORD  "change-me-123"         // >>> CHECK  hotspot password
// Laptop IP on its own hotspot is usually 192.168.137.1. No trailing slash.
// A https:// Render URL also works but adds 0.3-1 s delay per poll; use the laptop on demo day.
#define BACKEND_URL    "http://192.168.137.1:8000"
#define ROBOT_ID       "EDi-01"

#define POLL_INTERVAL_MS     600     // how often to ask for the next command
#define HTTP_TIMEOUT_MS      2500
#define COMM_TIMEOUT_MS      6000    // no successful poll for this long while moving -> stop motors

// ---------------------------------------------------------------- motors
// Two common driver wirings. Pick the one your OjanBot board uses:
//   DRIVER_2PWM  : each motor has IN1/IN2 (DRV8833, MX1508, L9110S, TB6612 in PWM-PWM mode)
//   DRIVER_EN_DIR: each motor has EN (PWM) + IN1 + IN2 (L298N style)
#define DRIVER_2PWM    1
#define DRIVER_EN_DIR  2
#define MOTOR_DRIVER   DRIVER_2PWM             // Cytron Robo ESP32: two PWM inputs per motor

// Cytron Robo ESP32 datasheet: M1A = D12, M1B = D13, M2A = D14, M2B = D27.
// If the robot turns when told to go straight, the motors are on the other terminals: swap L and R.
#define PIN_L_IN1  12                          // MOTOR 1 terminal (M1A)
#define PIN_L_IN2  13                          //                  (M1B)
#define PIN_R_IN1  14                          // MOTOR 2 terminal (M2A)
#define PIN_R_IN2  27                          //                  (M2B)
#define PIN_L_EN   -1                          // only for DRIVER_EN_DIR
#define PIN_R_EN   -1

// If a wheel turns backwards when the test sketch says "forward", flip it here
#define INVERT_LEFT   0                        // >>> CHECK
#define INVERT_RIGHT  0                        // >>> CHECK

#define PWM_FREQ_HZ   20000
#define PWM_BITS      8                        // duty 0..255

// ---------------------------------------------------------------- Cytron Maker Line
// Two ways to wire it:
//   LINE_MODE_ANALOG : the kit's single AN wire (no rewiring). Stations and T-tops are seen directly
//                      (all sensors on); side branches are found by distance from the last station bar,
//                      so CM_PER_S must be calibrated (hw test 'v').
//   LINE_MODE_DIGITAL: five wires D1-D5. Also sees side branches directly (more robust).
#define LINE_MODE_DIGITAL 1
#define LINE_MODE_ANALOG  2
#define LINE_MODE   LINE_MODE_ANALOG
#define PIN_LINE_AN  36                        // >>> CHECK: hw test 'a' finds the pin the AN wire is on
#define AN_NO_LINE  0.10                       // below 10% of 3.3 V = no line (Maker Line datasheet)
#define AN_CROSS    0.90                       // above 90% = all sensors on (station bar / top of a T)

// Digital wiring (only used with LINE_MODE_DIGITAL):
// The robot needs the 5 DIGITAL outputs D1-D5 (not the single AN analog output): it has to see
// which side the tape is on to tell station bars from junctions. Wiring: docs/TABLE_SETUP.md section 3.
// Power the Maker Line from 3.3 V so D1-D5 swing 0-3.3 V (ESP32-safe).
// D1 = leftmost sensor when you stand BEHIND the robot looking the way it drives.
// NOTE: GPIO 2, 5 and 15 are ESP32 boot-strapping pins. If an upload fails with "Failed to connect",
//       stand the robot on white (sensors LOW) or unplug the D2 wire (GPIO 2) while uploading.
#define PIN_LINE_D1  15
#define PIN_LINE_D2   2                        // shared with the NodeMCU's blue LED: status LED is disabled
#define PIN_LINE_D3   4
#define PIN_LINE_D4   5
#define PIN_LINE_D5  18
#define LINE_ACTIVE_HIGH  1                    // >>> CHECK 1 = output HIGH when the sensor is over black tape

// ---------------------------------------------------------------- HC-SR04 ultrasonic (front)
// TRIG 21 / ECHO 22 (Robo ESP32: Maker port / Grove 2 pins - leave nothing else plugged in there).
// ESP32 pins take 3.3 V max. A 3.3 V sensor (HC-SR04P, RCWL-1601, Cytron Maker Ultrasonic powered
// at 3.3 V) connects directly. A classic 5 V HC-SR04 needs a divider on ECHO:
//     ECHO --[1 k]--+--> GPIO 22        (TRIG can go straight to GPIO 21)
//                   |
//                 [2 k]
//                   |
//                  GND
#define PIN_US_TRIG  21
#define PIN_US_ECHO  22

// ---------------------------------------------------------------- other
#define PIN_ESTOP     0      // BOOT button on the ESP32 board: press = emergency stop (needs operator RESUME)
#define PIN_LED      -1      // status LED disabled: GPIO 2 is used by Maker Line D2. (Any free pin: slow blink =
                             // idle, solid = moving, fast blink = stopped/fault.)

// ---------------------------------------------------------------- driving (tune on the table)
#define BASE_SPEED        120   // 0-255 while following the line. Start low; the table is small.
#define MAX_SPEED         170
#define KP                 38   // steering per unit of line error (error range -2..+2)
#define KD                 60
#define CM_PER_S         14.0   // >>> CALIBRATE speed at BASE_SPEED on the line (hw test 'v'): used to know
                                //     roughly how far it has driven, so bends are not mistaken for junctions
#define LINE_LOST_MS      450   // no sensor on black for this long -> stop and report ERROR

// ---------------------------------------------------------------- turning on the spot
#define PIVOT_SPEED       115
#define PIVOT_90_MS       430   // >>> CALIBRATE time for a 90 degree pivot at PIVOT_SPEED (hw test 'p')
#define LEAVE_LINE_MS     160   // a "turn until the line" must first spend this long off the line
#define PIVOT_TIMEOUT_MS 3000   // never spin longer than this
// A turn only accepts the line inside an angle window, so it cannot lock onto the wrong tape:
#define TURN_MIN_MS   (PIVOT_90_MS / 2)          // left/right: ignore lines before ~45 degrees
#define TURN_MAX_MS   (PIVOT_90_MS * 17 / 10)    //             give up after ~150 degrees
#define UTURN_MIN_MS  (PIVOT_90_MS * 13 / 10)    // U-turn:     ignore lines before ~115 degrees
#define UTURN_MAX_MS  (PIVOT_90_MS * 27 / 10)    //             give up after ~240 degrees

// ---------------------------------------------------------------- recognising tape features
// Station = short bar ACROSS the line (tape on both sides). Junction = a branch on ONE side, or
// the top of a T (tape on both sides, and no line straight ahead).
#define SENSOR_AHEAD_CM   4.5   // >>> MEASURE distance from the sensor bar to the wheel axle
#define FEATURE_CONFIRM     2   // readings (5 ms apart) that must look like a feature
#define FEATURE_END_READS   3   // readings back to a plain line that end the feature
// Ignore feature-like patterns until this share of the expected distance is driven:
#define GATE_FRACTION_BAR     0.50   // stations / T-tops (a bend cannot light both sides, so this can be loose)
#define GATE_FRACTION_BRANCH  0.70   // side branches (a bend CAN look like one; keep CM_PER_S calibrated)
#define FEATURE_MAX_CM         4.0   // a real crossing is one tape width; longer = a bend, ignored
// After the sensors reach a feature, keep driving this long so the WHEELS (axle) stop on it.
// About SENSOR_AHEAD_CM / (0.75 * CM_PER_S) seconds.  >>> CALIBRATE with hw test 'm'
#define MARK_CENTER_MS    450
#define CHECK_LINE_AHEAD    1   // 1 = after stopping, check the line continues (station) or ends (top of a T)

// ---------------------------------------------------------------- obstacle and room scan
#define OBSTACLE_CM          8    // stop if something is closer than this in front while driving
#define OBSTACLE_CONFIRM     3    // consecutive close readings needed (~70 ms apart)
#define OBSTACLE_WAIT_MS  5000    // wait this long for it to clear before turning round
#define CLEAR_CONFIRM        4    // consecutive far readings needed to call it cleared
#define OCCUPIED_CM         10    // room scan: something closer than this = room occupied
#define SCAN_MEASURE_MS    600    // how long to measure while facing the room
#define US_MAX_CM           60    // ignore echoes beyond this (and timeouts)
