#pragma once
#include <cstdint>
#include <cstring>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <algorithm>
#include <cmath>
#define HIGH 1
#define LOW 0
#define OUTPUT 1
#define INPUT 0
#define INPUT_PULLUP 2
typedef uint8_t byte;
uint32_t millis(); void delay(uint32_t); void delayMicroseconds(uint32_t);
void pinMode(int,int); int digitalRead(int); void digitalWrite(int,int); int analogRead(int);
unsigned long pulseIn(int,int,unsigned long);
bool ledcAttach(int,int,int); void ledcWrite(int,int); void ledcSetup(int,int,int); void ledcAttachPin(int,int);
template<class T> T constrain(T x, T a, T b){ return x<a?a:(x>b?b:x);} 
using std::min; using std::max;
struct String : std::string { String(){} String(const char*s):std::string(s){} String(const std::string&s):std::string(s){}
  const char* c_str() const {return std::string::c_str();} bool startsWith(const char*p) const {return rfind(p,0)==0;} String operator+(const char*o) const {return String(std::string(*this)+o);} };
struct SerialT { int available(){return 0;} int peek(){return 0;} int read(){return 0;} template<class T> void print(T){} template<class T> void println(T,int){} void begin(int){} template<class...A> void printf(const char*f,A...a){ ::printf(f,a...);} void println(const char*s=""){ ::puts(s);} };
extern SerialT Serial;
typedef void* QueueHandle_t; typedef int BaseType_t;
#define pdTRUE 1
#define portMAX_DELAY 0xffffffff
#define pdMS_TO_TICKS(x) (x)
QueueHandle_t xQueueCreate(int,int); BaseType_t xQueueSend(QueueHandle_t,const void*,uint32_t);
BaseType_t xQueueSendToFront(QueueHandle_t,const void*,uint32_t); BaseType_t xQueueReceive(QueueHandle_t,void*,uint32_t);
BaseType_t xQueuePeek(QueueHandle_t,void*,uint32_t); void vTaskDelay(uint32_t);
void xTaskCreatePinnedToCore(void(*)(void*),const char*,int,void*,int,void*,int);
