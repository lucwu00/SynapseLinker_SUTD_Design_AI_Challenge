#pragma once
#include "Arduino.h"
#define WIFI_STA 1
#define WIFI_OFF 0
typedef int wl_status_t;
#define WL_CONNECTED 3
#define WL_NO_SSID_AVAIL 1
#define WL_CONNECT_FAILED 4
#define WL_DISCONNECTED 6
struct IP { String toString(){return "1.2.3.4";} };
struct WiFiT { void mode(int){} void setSleep(bool){} void begin(const char*,const char*){} wl_status_t status(){return 3;} void disconnect(bool=false){} IP localIP(){return IP();} int RSSI(){return 0;} };
extern WiFiT WiFi;
struct WiFiClient {};
