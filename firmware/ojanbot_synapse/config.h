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
#define PIVOT_SPEED       115   // in-place turns
#define PIVOT_90_MS       430   // >>> CALIBRATE time for a 90 degree pivot at PIVOT_SPEED (hw test, menu 'p')
#define LEAVE_LINE_MS     160   // a "turn until the line" must first spend this long off the line
#define PIVOT_TIMEOUT_MS 3000   // never spin longer than this - stop and report an error
#define UTURN_MIN_MS  (PIVOT_90_MS * 13 / 10)   // a U-turn ignores lines seen before ~115 degrees
#define TURN_MIN_MS   (PIVOT_90_MS / 2)         // a left/right turn ignores lines before ~45 degrees

// Marks: short black bars across the line. A mark = at least MARK_MIN_SENSORS sensors black at once.
#define MARK_MIN_SENSORS    4
#define MARK_CONFIRM_READS  2   // consecutive readings (5 ms apart) to accept a mark
#define MARK_COOLDOWN_MS  700   // ignore marks for this long after one was counted
// After the sensors cross a mark, keep driving this long so the WHEELS (axle) stop over the mark.
// = (distance from sensor bar to wheel axle) / (driving speed).  >>> CALIBRATE with hw test menu 'm'
#define MARK_CENTER_MS    450
#define LINE_LOST_MS      450   // no sensor on black for this long -> stop and report ERROR

// ---------------------------------------------------------------- obstacle and room scan
#define OBSTACLE_CM          8    // stop if something is closer than this in front while driving
#define OBSTACLE_CONFIRM     3    // consecutive close readings needed (~70 ms apart)
#define OBSTACLE_WAIT_MS  5000    // wait this long for it to clear before turning round
#define CLEAR_CONFIRM        4    // consecutive far readings needed to call it cleared
#define OCCUPIED_CM         11    // room scan: something closer than this = room occupied
#define SCAN_MEASURE_MS    600    // how long to measure while facing the room
#define US_MAX_CM           60    // ignore echoes beyond this (and timeouts)
