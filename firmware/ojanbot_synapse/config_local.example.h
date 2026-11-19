// Your own settings. Copy this file to config_local.h in BOTH folders:
//   firmware/ojanbot_synapse/config_local.h  and  firmware/ojanbot_hw_test/config_local.h
// Anything defined here wins over config.h. config_local.h is ignored by git, so pulling updates keeps it.
#pragma once

#define WIFI_SSID      "Luc_iPhone"                 // hotspot name, exactly as the phone shows it
#define WIFI_PASSWORD  "your-hotspot-password"
#define BACKEND_URL    "http://172.20.10.4:8000"    // laptop IPv4 (ipconfig) + :8000

// Calibration results (hw test v / p / m) - uncomment and set when you have them:
// #define CM_PER_S        14.0
// #define PIVOT_90_MS     430
// #define MARK_CENTER_MS  450
// #define SENSOR_AHEAD_CM 4.5
// #define INVERT_LEFT     0
// #define INVERT_RIGHT    0
// #define PIN_LINE_AN     15
