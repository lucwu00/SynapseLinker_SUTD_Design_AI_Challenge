# EDi-01 firmware (OjanBot ESP32)

| Sketch | Purpose |
|---|---|
| `ojanbot_hw_test/` | Check wiring and calibrate (line sensor, wheels, ultrasonic, 90° turn, stopping on a mark, Wi-Fi) |
| `ojanbot_synapse/` | The demo firmware: follows the tape loop, counts marks, scans rooms, stops for obstacles, talks to the backend |

Both use the same `config.h`. Edit `ojanbot_synapse/config.h`, then copy it over `ojanbot_hw_test/config.h` so the two stay identical.

**Status:** checked by compiling against stub headers and by a kinematic simulation of the table (start, pass and stop at marks, room scan occupied/free, re-guide, obstacle at START with turn-round and reroute, obstacle that clears, emergency stop and resume, Wi-Fi loss). It has **not yet run on your robot**: the pins in `config.h` are placeholders until you confirm them.

## Install (Arduino IDE 2)

1. **Boards Manager:** install "esp32 by Espressif Systems" (2.x or 3.x both work).
2. **Library Manager:** install "ArduinoJson" by Benoit Blanchon, version 7.
3. Board: **ESP32 Dev Module**. Port: the robot's USB port. Upload speed 921600 (lower it to 115200 if uploads fail).
4. Open `ojanbot_hw_test/ojanbot_hw_test.ino`, upload, open Serial Monitor at **115200**, and follow `docs/TABLE_SETUP.md` step 3.
5. Open `ojanbot_synapse/ojanbot_synapse.ino`, set `WIFI_SSID`, `WIFI_PASSWORD` and `BACKEND_URL` in `config.h`, and upload.

The ESP32's current OjanBot joystick page (Wi-Fi access point 192.168.4.1) is replaced by this firmware. To get it back, re-flash the original OjanBot sketch.

## Pins to confirm (send your original OjanBot sketch if unsure)

| `config.h` | Placeholder | What to check |
|---|---|---|
| `MOTOR_DRIVER`, `PIN_L_IN1/2`, `PIN_R_IN1/2` (`PIN_L_EN/R_EN`) | 25, 26, 27, 14 | The motor pins in the original OjanBot sketch, and whether it uses 2 PWM pins per motor or EN + 2 direction pins |
| `PIN_LINE_D1..D5` | 36, 39, 34, 35, 32 | Where the Maker Line D1–D5 wires go. Power the Maker Line from **3.3 V** |
| `PIN_US_TRIG`, `PIN_US_ECHO` | 5, 18 | HC-SR04 pins. ECHO is 5 V: use a divider (1 kΩ + 2 kΩ) |

## How a journey runs

```
backend NAVIGATE {start_action: GO|U_TURN, mark_actions: [PASS, ..., STOP], scan_side: LEFT|RIGHT}
robot   ACK -> look ahead -> follow the line, counting marks -> STOP mark: wheels onto the mark
        -> turn 90° to scan_side -> ultrasonic -> turn back to the line -> ARRIVED {room_scan}
obstacle ahead: BLOCKED -> clears within 5 s: OBSTACLE_CLEARED, carry on
                        -> still there: turn round on the line -> PATH_BLOCKED {marks_passed, on_mark}
                        -> backend REROUTE (the other way round the loop)
```

Safety built in: no steering around obstacles; stops after 6 s without the backend; emergency stop (dashboard or BOOT button) holds until Resume; a turn that cannot find the line within 3 s stops with an error; an unexpected extra mark stops with an error.
