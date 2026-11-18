# 60 × 60 cm demo table: double-loop track, calibration, demo run

![Table layout](table_layout_60cm.png)

All positions are in **cm from the bottom-left corner of the table** (the corner nearest the presenter, on the presenter's left). x runs to the right, y runs away from the presenter. The same numbers are in `config/table_map_60cm.json`, which the backend loads by default. To use a different physical layout later, edit that file (or point `SYNAPSE_MAP` at a new one): nodes are station bars and T-junctions, edges are taped corridors, `via` points are corners. The ML models and the patient-flow router do not change.

## 1. Materials

| Item | Notes |
|---|---|
| White board 60 × 60 cm (foam board, or white paper taped flat) | The green cutting mat is too dark for the line sensor; cover it |
| 19 mm black electrical tape (matte, not glossy) | About 2.5 m |
| 3 "person" blocks, about 4 × 4 × 8 cm (cardboard or a wooden block) | Flat faces echo better than a figurine |
| 1 "trolley" box, about 4 × 4 × 6 cm | Scene 9 only |
| Station labels | Flat cards on the table, or standing signs at least 3 cm outside the tape, beside a straight piece |
| Dummy weight about the phone's weight | Use it for every test before you mount the real phone |

## 2. Lay the tape

1. **Outer rectangle:** tape centre line **10 cm in from every table edge**, so the sides run along x = 10, x = 50, y = 10 and y = 50 (40 × 40 cm). Round each corner with an **8 cm radius**: the curve starts 8 cm before the corner. Lay each curve as 4–5 short overlapping pieces, smooth, no kinks.
2. **Middle corridor:** a straight piece along **y = 30** from the left side to the right side. Where it meets each side it makes a **T-junction**, at (10, 30) and (50, 30). Butt the tape cleanly into the side; do not let it stick out past the side.
3. **Station bars:** 8 cm bars of tape **across** the line, centred on it:

| Station | Bar centre (cm) | Room in the system | Person block (if occupied) |
|---|---|---|---|
| START / Registration | (30, 10), middle of the bottom side | robot home | none |
| Station 1 | (50, 38), right side, 8 cm above the right T-junction | X-ray Room 2 | (39, 38) |
| Station 2 | (30, 50), middle of the top side | X-ray Room 1 | (30, 39) |
| Station 3 | (10, 22), left side, 8 cm below the left T-junction | Consultation Room 1 | (21, 22) |

4. **Junctions get no bar.** The robot tells them apart by shape: a bar has tape on **both** sides of the line; a junction seen from a side has tape on **one** side; arriving along the middle corridor, the robot meets the side as the top of a T (tape on both sides, and no line straight ahead).
5. **Scene 9 trolley box:** on the tape at **(40, 10)**, 10 cm right of START, just before the corner. Put it down only for scene 9. (A box placed right after a corner, as in the sketch, cannot be seen in time: the robot is still turning when it gets there.)
6. **Keep clear:** no other tape crossings, nothing taller than 1 cm on or within 3 cm of the tape except the trolley box.

### Fit check (do this first)

The 10 cm inset assumes the robot's half-diagonal is at most 8 cm (a robot up to about 11 × 11 cm). Measure yours corner to corner: if half of that plus 2 cm is more than 10 cm, the robot can hang over the edge when it turns at START. Then move the whole track 1–2 cm in and update the coordinates in the map (or ask for the map to be regenerated).

## 3. Wire the robot (Cytron OjanBot, Robo/Maker ESP32)

Motors stay on the board's motor terminals: MOTOR 1 (M1A = GPIO 12, M1B = GPIO 13) is the left wheel, MOTOR 2 (M2A = GPIO 14, M2B = GPIO 27) the right wheel.

**Maker Line: use all five digital outputs.** The kit usually connects only the single analog wire (AN); this firmware cannot use that, because it needs to see *which side* the tape is on. Disconnect the AN cable and run six female-female jumper wires from the Maker Line's pin header to the ESP32 board's GPIO header:

| Maker Line pin | ESP32 header pin | Notes |
|---|---|---|
| VCC | 3V3 | 3.3 V, not 5 V, so D1–D5 never exceed 3.3 V |
| GND | GND | |
| D1 (leftmost sensor, looking the way the robot drives) | D15 | boot-strapping pin |
| D2 | D2 | boot-strapping pin; also the blue LED, so the status LED is disabled in firmware |
| D3 (centre) | D4 | |
| D4 | D5 | boot-strapping pin |
| D5 (rightmost) | D18 | |
| AN, CAL | not connected | |

**Ultrasonic:**

| Sensor pin | ESP32 header pin | Notes |
|---|---|---|
| VCC | 3V3 for a 3.3 V sensor (HC-SR04P, Cytron Maker Ultrasonic); 5V/VIN for a classic HC-SR04 | |
| GND | GND | |
| TRIG | D21 | direct |
| ECHO | D22 | 3.3 V sensor: direct. 5 V HC-SR04: through a divider, 1 kΩ from ECHO to D22 and 2 kΩ from D22 to GND |

Nothing else may be plugged into the Maker port / Grove 2 (they share 21 and 22).

**Upload tip:** if an upload fails with "Failed to connect", the Maker Line is holding GPIO 2 high. Stand the robot on white paper, or pull the D2 wire, during the upload.

**Check the wiring:** with `ojanbot_hw_test` running, type `k` and slide the robot across the tape for 6 s. It reports, for each of D1–D5, "changes – wired OK" or "stuck", and whether the ultrasonic gets any echo. If no D pin changes, the Maker Line is still on its analog wire or unpowered.

## 4. Calibrate the robot (hardware test sketch, about 25 minutes)

Flash `firmware/ojanbot_hw_test` (see `firmware/README.md`), open Serial Monitor at 115200 baud and put the dummy weight on the robot.

| Step | Command | Pass when | If not, change in `config.h` |
|---|---|---|---|
| 0 Wiring | `k`, slide across the tape | All five D pins "changes", ultrasonic has echoes | See section 3 |
| 1 Line sensor | `l`, slide the robot across the tape | `..#..` when centred; on a station bar `#####` ("BAR"); over a junction branch `###..` or `..###` | Left/right reversed: swap D1↔D5 pins. All inverted: `LINE_ACTIVE_HIGH 0`. Bar not "BAR": make it longer |
| 2 Wheels | `L`, `R`, `f` (wheels lifted first) | Each named wheel rolls forward | Wrong wheel: swap the L and R pins. Backwards: `INVERT_LEFT` / `INVERT_RIGHT` 1 |
| 3 Ultrasonic | `u`, hold a box 5, 8 and 12 cm away | Readings within about 1 cm | Check the ECHO divider and pins |
| 4 Quarter turn | `p`, then `P` | `p` turns 90°; `P` ends facing where it started | Adjust `PIVOT_90_MS` |
| 5 Speed | `v` on the middle corridor | You measure the distance driven in 2 s | Set `CM_PER_S` = distance ÷ 2. Within ±20% is enough for navigation |
| 6 Stop on a bar | Robot 10 cm before a station bar, `m` | Stops with the wheel axle over the bar (±1 cm) | Short: raise `MARK_CENTER_MS`. Past: lower it. This decides whether the room scan points at the person block |
| 7 Sensor offset | Ruler | Measure sensor bar → wheel axle | Set `SENSOR_AHEAD_CM`. Must be at least 4.5 cm so turns on a bar never stop on the bar itself |
| 8 Wi-Fi | `w` | `GET .../health -> 200` | Hotspot must be 2.4 GHz; allow Python through Windows Firewall |

Then flash `firmware/ojanbot_synapse` with the same `config.h`.

## 5. Demo day: who runs what

```
System laptop (Windows)                    EDi-01 (OjanBot + phone)       Virtual-robot laptop
- Mobile Hotspot "SYNAPSE-DEMO" (2.4 GHz)  - ESP32 joins the hotspot      - joins the hotspot
- start_backend_robot.bat                  - phone joins the hotspot      - browser full screen:
- wall display  localhost:8000/#/          - phone browser full screen:     192.168.137.1:8000/#/robot/EDi-02
- presenter     localhost:8000/#/presenter   192.168.137.1:8000/#/robot/EDi-01
```

1. Turn on **Settings › Network › Mobile hotspot** on the system laptop (band 2.4 GHz; name and password as in `config.h`). Its address on its own hotspot is normally **192.168.137.1**; `start_backend_robot.bat` prints it.
2. Put EDi-01 **on the START bar, wheels on the bar, facing right**, then run `start_backend_robot.bat`.
3. Switch the robot on. On the presenter console, EDi-01 shows as connected.
4. **Before scene 8 and scene 9, put the robot back on START facing right.** Those scenes reset the clinic, and the system then assumes the robot is home.

### What the audience sees

- **Scene 8, room occupied:** person block at (39, 38). EDi-01 drives right, round the corner, straight past the right T-junction, stops at Station 1, turns to face the room, sees the block, turns back. The system re-guides it on to Station 2 (X-ray Room 1).
- **Scene 9, corridor blocked:** trolley box at (40, 10). EDi-01 is sent to Station 1, looks ahead before leaving START, sees the box and waits 5 s. Remove it within 5 s and it carries on. Leave it, and it turns round, passes START and Station 3, **turns right at the left T-junction into the middle corridor, turns left at the right T-junction**, and stops at Station 1.
- **Completely blocked:** with the real robot, put a second box on the middle corridor and a third on the top corridor. Each time it stops, turns round and is rerouted; when there is no way left it stops and staff are alerted. (In simulation, click those corridors on the presenter's floor map.)
- **Emergency stop:** the presenter's Emergency stop button, or the BOOT button on the ESP32. It stays stopped until Resume.

## 6. Troubleshooting

| Serial message / symptom | Likely cause |
|---|---|
| `Missed feature #n` | Bar or junction not seen: bar too short or shiny tape, or `CM_PER_S` far too high |
| `Saw a bar where branch L/R was expected` | Robot lost count, or the tape layout does not match the map |
| `Expected the top of a T but the line goes on` | Robot is at a station bar, not the T: count or layout mismatch |
| `ignored L while expecting a bar` (just a note) | A corner lit an outer sensor; this is normal and ignored |
| `Turn did not find the line in its angle window` | `PIVOT_90_MS` wrong, or the junction tape is not at a right angle |
| `Line lost` on curves | `BASE_SPEED` too high or `KP` too low; corners must be smooth |
| Room always FREE | Person block not 11 cm in and level with the bar, or stop off-centre (step 6) |
| Stops by itself while driving | Something within 8 cm ahead: a sign, a hand, the table edge on a corner |
| Stops and the task fails after a few seconds | Wi-Fi dropped for 6 s: move the laptop closer, keep the hotspot on 2.4 GHz |

Without the robot, run `start_backend.bat` instead (EDi-01 then moves on screen only).
