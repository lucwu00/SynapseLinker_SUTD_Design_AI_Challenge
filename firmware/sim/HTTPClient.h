#pragma once
#include "WiFi.h"
struct HTTPClient { void setTimeout(int){} void setConnectTimeout(int){} bool begin(WiFiClient&, const String&){return true;}
 void addHeader(const char*,const char*){} int POST(const String&){return 200;} int GET(){return 200;} String getString(){return "";} void end(){} String errorToString(int){return "";} };
