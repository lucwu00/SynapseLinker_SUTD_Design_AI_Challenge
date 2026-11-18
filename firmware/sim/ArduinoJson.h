#pragma once
#include "Arduino.h"
struct JsonVariant { float operator|(float d) const {return d;} template<class T> T as() const { return T(); } template<class T> JsonVariant& operator=(const T&){return *this;}
  JsonVariant operator[](const char*) const {return JsonVariant();} 
  const char* operator|(const char* d) const {return d;} bool operator|(bool d) const {return d;} 
  JsonVariant* begin(){return nullptr;} JsonVariant* end(){return nullptr;} };
typedef JsonVariant JsonArray;
template<> inline JsonVariant JsonVariant::as<JsonVariant>() const { return *this; }
struct JsonDocument { JsonVariant operator[](const char*){return JsonVariant();} };
struct DeserializationOption { struct Filter { Filter(JsonDocument&){} }; };
struct DeserializationError { bool e=false; operator bool() const {return e;} };
inline DeserializationError deserializeJson(JsonDocument&, const String&, DeserializationOption::Filter){return {};}
inline void serializeJson(JsonDocument&, String&){}
