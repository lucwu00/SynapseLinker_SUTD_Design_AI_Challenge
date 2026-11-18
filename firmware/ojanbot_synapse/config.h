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
#define MOTOR_DRIVER   DRIVER_2PWM             // >>> CHECK

#define PIN_L_IN1  25                          // >>> CHECK  left motor
#define PIN_L_IN2  26
#define PIN_R_IN1  27                          // >>> CHECK  right motor
#define PIN_R_IN2  14
#define PIN_L_EN   -1                          // only for DRIVER_EN_DIR
#define PIN_R_EN   -1

// If a wheel turns backwards when the test sketch says "forward", flip it here
#define INVERT_LEFT   0                        // >>> CHECK
#define INVERT_RIGHT  0                        // >>> CHECK

#define PWM_FREQ_HZ   20000
#define PWM_BITS      8                        // duty 0..255

// ---------------------------------------------------------------- Cytron Maker Line (5 digital outputs)
// Power the Maker Line from 3.3 V so its outputs are safe for ESP32 pins.
// D1 = leftmost sensor when you stand BEHIND the robot looking the way it drives.
#define PIN_LINE_D1  36                        // >>> CHECK (36/39/34/35 are input-only pins - fine for sensors)
#define PIN_LINE_D2  39
#define PIN_LINE_D3  34
#define PIN_LINE_D4  35
#define PIN_LINE_D5  32
#define LINE_ACTIVE_HIGH  1                    // >>> CHECK 1 = output HIGH when the sensor is over black tape

// ---------------------------------------------------------------- HC-SR04 ultrasonic (front)
// ECHO is 5 V: use a divider (1 k from ECHO, 2 k to GND, take the middle) into the ESP32.
#define PIN_US_TRIG  5                         // >>> CHECK
#define PIN_US_ECHO  18                        // >>> CHECK

// ---------------------------------------------------------------- other
#define PIN_ESTOP     0      // BOOT button on the ESP32 board: press = emergency stop (needs operator RESUME)
#define PIN_LED       2      // on-board LED: slow blink = idle, solid = moving, fast blink = stopped/fault

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
