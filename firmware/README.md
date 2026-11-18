# EDi-01 firmware (OjanBot ESP32)

| Sketch | Purpose |
|---|---|
| `ojanbot_hw_test/` | Check wiring and calibrate (line sensor, wheels, ultrasonic, 90° turn, stopping on a mark, Wi-Fi) |
| `ojanbot_synapse/` | The demo firmware: follows the tape, recognises station bars and T-junctions, turns at junctions, scans rooms, stops for obstacles, talks to the backend |

Both use the same `config.h`. Edit `ojanbot_synapse/config.h`, then copy it over `ojanbot_hw_test/config.h` so the two stay identical.

**Status:** checked by compiling against stub headers and by a kinematic simulation of the double-loop table: passing the right T-junction, stopping at a station, room scan occupied/free, re-guide, obstacle at START with turn-round and a reroute through both T-junctions (right turn, then left turn at the top of the T), an obstacle in the middle corridor with turn-round mid-corridor, a deliberately wrong map (stops safely), and speed miscalibrated by ±20% (navigation still correct). It has **not yet run on your robot**: the pins in `config.h` are placeholders until you confirm them.

## Install (Arduino IDE 2)

1. **Boards Manager:** install "esp32 by Espressif Systems" (2.x or 3.x both work).
2. **Library Manager:** install "ArduinoJson" by Benoit Blanchon, version 7.
3. Board: **ESP32 Dev Module**. Port: the robot's USB port. Upload speed 921600 (lower it to 115200 if uploads fail).
4. Open `ojanbot_hw_test/ojanbot_hw_test.ino`, upload, open Serial Monitor at **115200**, and follow `docs/TABLE_SETUP.md` step 3.
5. Click **Verify** on both sketches first: that is the real compile (here they were only checked against stub headers). Then open `ojanbot_synapse/ojanbot_synapse.ino`, set `WIFI_SSID`, `WIFI_PASSWORD` and `BACKEND_URL` in `config.h`, and upload.

The ESP32's current OjanBot joystick page (Wi-Fi access point 192.168.4.1) is replaced by this firmware. To get it back, re-flash the original OjanBot sketch.

## Pins (Cytron OjanBot with Robo / Maker ESP32)

| Function | GPIO |
|---|---|
| Left motor (MOTOR 1: M1A, M1B), PWM-PWM | 12, 13 |
| Right motor (MOTOR 2: M2A, M2B), PWM-PWM | 14, 27 |
| Maker Line D1 … D5 (digital, powered at 3.3 V) | 15, 2, 4, 5, 18 |
| Ultrasonic TRIG / ECHO | 21 / 22 (ECHO divider if the sensor runs on 5 V) |
| Emergency stop | BOOT button (GPIO 0) |
| Status LED | disabled (GPIO 2 is Maker Line D2) |

Wheel direction: `INVERT_LEFT` / `INVERT_RIGHT`. Exact wiring and the `k` wiring check: `docs/TABLE_SETUP.md` section 3.

## How a journey runs

```
backend NAVIGATE {start_action: GO|LEFT|RIGHT|U_TURN, scan_side: LEFT|RIGHT,
                  features: [{t: S|J, p: C|L|R, a: PASS|LEFT|RIGHT|U_TURN|STOP, d: cm, m: max cm}, ...]}
robot   ACK -> look ahead -> follow the line; at each expected feature:
          sensor pattern  C = tape at both outer sensors (station bar, or top of a T)
                          L / R = tape at one outer sensor together with the centre (side branch)
          accepted only after ~50% (bar) / 70% (branch) of the expected distance and if it is one
          tape-width long (a bend lasts longer and is ignored); not found by "m" cm -> safe stop
        -> LEFT/RIGHT: wheels onto the junction, turn until the line, inside a 45-150 degree window
        -> STOP: wheels onto the bar, turn 90° to scan_side, ultrasonic, turn back -> ARRIVED {room_scan}
obstacle ahead: BLOCKED -> clears within 5 s: OBSTACLE_CLEARED, carry on
                        -> still there: turn round on the line -> PATH_BLOCKED {marks_passed, on_mark}
                        -> backend REROUTE (the other way round the loop)
```

Safety built in: no steering around obstacles; stops after 6 s without the backend; emergency stop (dashboard or BOOT button) holds until Resume; a turn that cannot find the line inside its angle window stops with an error; a missed feature, a bar where a branch should be, or a line continuing past the top of a T stops with an error.

To re-run the logic check on a computer with g++: `firmware/sim/run_sim.sh` (stub Arduino headers + a model of the table).
