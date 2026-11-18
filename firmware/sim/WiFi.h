#pragma once
#include "Arduino.h"
#define WIFI_STA 1
#define WL_CONNECTED 3
struct IP { String toString(){return "1.2.3.4";} };
struct WiFiT { void mode(int){} void setSleep(bool){} void begin(const char*,const char*){} int status(){return 3;} void disconnect(){} IP localIP(){return IP();} int RSSI(){return 0;} };
extern WiFiT WiFi;
struct WiFiClient {};
