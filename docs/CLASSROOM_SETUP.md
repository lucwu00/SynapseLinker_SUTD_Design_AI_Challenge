# Classroom demo setup: track, rooms, obstacles, rerouting

You don't need the classroom's floor plan. **The map describes the tape track you lay down**: junction markers, corridors and room stops. You decide the layout and measure it on the day, which takes about 15 minutes.

## 1. Lay the track (recommended layout)

```
      Room A        Room B        Room C           A = Consultation Room 1
        |             |             |              B = X-ray Room 1
START--J1------------J2------------J3              C = X-ray Room 2
        |                           |
        J4-------------------------J5              The loop J1-J4-J5-J3 is the detour
```

- Use dark electrical tape for the line the OjanBot follows. At each junction (J1–J5) and room stop (A, B, C), put a marker the robot can recognise: a coloured tape patch or an RFID tag.
- "Rooms" can be three tables or chair areas with a printed sign: *Consultation Room 1*, *X-ray Room 1*, *X-ray Room 2*.
- Rooms B and C must offer the **same service**, so the robot can re-guide between them. That's what makes the "room occupied" scene work.
- The detour loop is essential: without a second path, a blocked corridor can only end in "staff alerted".

## 2. Measure and write the map

Copy `config/classroom_map.example.json` to `config/classroom_map.json`. With a tape measure, from the START mark:
- `x` is the distance along the main corridor; `y` is toward the rooms (negative toward the J4–J5 loop). Use metres.
- Set each node's `marker` to the marker ID on the floor.
- If a corridor is curved, add `"distance_m"` to that edge.
- If your layout is different, add or remove nodes and edges. The planner works on any connected graph.

Then click **Reset demo** (or restart the backend). The dashboard's "Classroom track" panel draws your map. Check it matches the floor.

## 3. Room occupancy: how the system knows a room is busy

| Source | How | Status |
|---|---|---|
| Robot scan on arrival | Ultrasonic sensor pointed into the room doorway. Reports `room_scan: OCCUPIED/FREE` with `ARRIVED` | Firmware to write (contract in ESP32_CONTRACT.md) |
| IoT room sensor | Small ESP32 + PIR or ultrasonic in each room → `POST /rooms/ROOM_B/status {"occupied": true, "source": "IoT sensor"}` | Optional |
| Staff | Dashboard "Sensor" and "Close" buttons | Works now |
| Service start/finish | Marked automatically when staff start/complete a service in that room | Works now |

Occupied only means "someone is in the room". It does not mean the room is clinically ready. Staff still open and close rooms.

## 4. What happens

**Room occupied (scene 8)**
1. The system believes X-ray Room 1 is free and guides the patient there.
2. On arrival the robot scans the room: occupied.
3. The backend marks it occupied and checks all X-ray rooms.
4. It re-guides the patient to the nearest free one, X-ray Room 2.
5. If none is free, the router picks another permitted service. If there is none, the patient is asked to wait and is guided automatically when a room frees.

**Corridor blocked (scene 9)**
1. The robot detects an obstacle about 25 cm ahead and stops. It never steers around it.
2. If the obstacle clears within `obstacle_wait_s` (5 s), the robot continues.
3. Otherwise it backs up to the last junction marker and reports `PATH_BLOCKED`.
4. The backend marks that corridor blocked and plans a new route around the loop. It sends `REROUTE`, whose first instruction may be `U_TURN`.

**Completely blocked**

If no corridor leads to any free room of that service, the robot stops, the patient's screen says "A staff member is coming", and staff are alerted.

**Clearing a corridor**

The planner avoids a blocked corridor until staff clear it, or until the obstacle is removed in simulation.

## 5. Running it before the robot firmware is ready

Everything above runs in simulation:
- Click a corridor on the dashboard map to drop a simulated obstacle.
- Use "Person walks in (unreported)" to put someone in a room without telling the system.

For a hybrid live demo, run the simulated robot on the dashboard while a teammate walks the physical robot along the same tape, and narrate that the motion is simulated.

## 6. Safety checklist (physical robot)

- Speed is 0.15 m/s or slower. Test with a dummy weight equal to the phone first.
- Obstacle means stop. Do not swerve or push.
- The e-stop button is reachable. The robot never resumes by itself after an emergency stop.
- If a poll fails for 6 s, the firmware stops the motors by itself.
- Keep cables, bags and feet off the tape during the demo, except the planned obstacle.
