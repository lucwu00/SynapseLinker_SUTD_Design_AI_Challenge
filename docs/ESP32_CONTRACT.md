# ESP32 OjanBot integration contract (HTTP polling)

**Status: implemented in the backend and unit-tested; firmware in `firmware/ojanbot_synapse` (simulated, not yet run on the robot).** Without the robot, EDi-01 runs on the simulation adapter.

## Enable

1. Run `start_backend_robot.bat` (sets `SYNAPSE_ROBOT_ADAPTER=esp32` and the table map), or set `"EDi-01": {"kind": "physical", "adapter": "esp32"}` in `config/backend_config.json`.
2. The backend must listen on all interfaces: `uvicorn backend.main:app --host 0.0.0.0 --port 8000`.
3. Laptop and ESP32 on the same Wi-Fi (demo day: the laptop's Mobile Hotspot, laptop = 192.168.137.1).

## Line-following routes (tabletop track)

The robot has no map. Each NAVIGATE / REROUTE lists the tape features it will cross:

| Field | Meaning |
|---|---|
| `start_action` | `GO`, `LEFT`, `RIGHT` or `U_TURN` before following the line |
| `features[]` | In crossing order. `t`: `S` station bar / `J` junction. `p`: expected sensor pattern, `C` tape on both sides (bar, or top of a T), `L`/`R` branch on one side. `a`: `PASS`, `LEFT`, `RIGHT`, `U_TURN`, `STOP` (last). `d`: taped cm from the previous feature (0 = unknown). `m`: stop as "missed" after this many cm |
| `scan_side` | `LEFT` or `RIGHT`: the side the room is on when it stops |
| `scan_room_on_arrival` | Turn 90° to `scan_side`, measure, turn back, report `room_scan` |
| `mark_actions` | The actions only, for older firmware |

After a reroute where the robot turned round **mid-corridor**, the first feature is the one it is driving back towards (`d` = 0). If it turned round **on** the node it started from (`on_mark: true`), that node is not listed again.

## Firmware loop

```
every 0.6 s:
  GET http://<laptop-ip>:8000/robot/EDi-01/next-command
  -> {"command": "NONE"}                                   nothing to do (also acts as heartbeat)
  -> {"command": "STOP"}                                   stop motors immediately, stay stopped
  -> {"command": "RESUME"}                                 continue current task
  -> {"command": "CANCEL", "task_id": "TASK_004"}          stop and drop the task
  -> {"command": "REROUTE", ...same fields as NAVIGATE...} new route from the junction where you stopped
                                                            (first instruction may be U_TURN)
  -> {"command": "NAVIGATE", "task_id": "TASK_004", "patient_id": "ORTHO_001",
      "destination_station": "XRAY", "room_id": "ROOM_B", "scan_room_on_arrival": true,
      "route_id": "R-1a2b3c4d",
      "waypoints": [{"node": "START", "x_m": 0, "y_m": 0, "marker": "M0"}, ...],
      "junction_instructions": [{"at_node": "J1", "at_marker": "M1", "action": "LEFT"},
                                {"at_node": "N_XRAY", "at_marker": "S-XRAY", "action": "STOP"}]}
```

Report every change with `POST /robot/status`:

```json
{"robot_id": "EDi-01", "task_id": "TASK_004", "event": "ACK", "marker": "M0"}
```

Events:
- `ACK`: the robot received the command.
- `ARRIVED`: the robot reached the destination. When `scan_room_on_arrival` is true, include `"room_scan": "OCCUPIED"` or `"FREE"`, read by pointing the ultrasonic sensor into the room doorway.
- `PATH_BLOCKED`: the obstacle is still there after `obstacle_wait_s` (5 s). Turn round on the line first, then send `"marks_passed"` (marks crossed on this route), `"turned_around": true` and `"on_mark"` (true if you never left the mark you started on). Wait for `REROUTE` or `CANCEL`. (Older firmware may send `"marker"` = last junction instead.)
- `BLOCKED`: the ultrasonic sensor sees an obstacle, so the robot stops.
- `OBSTACLE_CLEARED`: the path is clear again.
- `ESTOP_PRESSED`: the physical button was pressed.
- `ERROR`: anything else; include a `message`.

## Line following

The robot is not assumed to know its exact position. Routes come as one instruction per junction marker (`LEFT`, `RIGHT`, `STRAIGHT`, `STOP`) for a line-following robot that recognises markers, such as coloured tape or RFID tags. Turns are computed from the map coordinates, assuming the robot faces along the first route segment. After a reroute, the first instruction is relative to the direction the robot was facing when it stopped, and can be `U_TURN`.

## Safety requirements (firmware side)

- Use a conservative speed. The map default is 0.15 m/s.
- Stop on obstacle and report `BLOCKED`. Do not attempt to steer around it.
- **Stop by itself if a poll fails for more than 6 seconds.** The backend cannot stop a robot it cannot reach; it marks the task `FAILED` (`COMM_TIMEOUT`).
- Never resume after STOP or the physical e-stop until the robot receives `RESUME`.
- If a `task_id` arrives again, the robot keeps its current task and does not restart it. The backend resends an unacknowledged command after 5 s.
- Reject NAVIGATE commands with empty `junction_instructions`.
- Test with a dummy weight equal to the phone before mounting the real phone.
