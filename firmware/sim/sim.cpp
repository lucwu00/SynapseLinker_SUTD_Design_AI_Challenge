// Kinematic simulation of the firmware on the 60 x 60 cm loop (logic check, not physics).
#include "main.cpp"
#include <deque>
#include <vector>
#include <cmath>
#include <map>
SerialT Serial; WiFiT WiFi;
static uint32_t NOW = 0;
uint32_t millis(){return NOW;} void delay(uint32_t){} void delayMicroseconds(uint32_t){}
static std::map<int,int> pinv;
void pinMode(int,int){} void digitalWrite(int p,int v){pinv[p]=v;}
static double lw=0, rw=0; // wheel duty -255..255
static int pwmL1,pwmL2,pwmR1,pwmR2;
bool ledcAttach(int,int,int){return true;}
void ledcWrite(int pin,int d){ if(pin==PIN_L_IN1)pwmL1=d; if(pin==PIN_L_IN2)pwmL2=d; if(pin==PIN_R_IN1)pwmR1=d; if(pin==PIN_R_IN2)pwmR2=d; }
// queues
struct Q{ int sz; std::deque<std::vector<char>> d; };
QueueHandle_t xQueueCreate(int,int sz){ return new Q{sz}; }
BaseType_t xQueueSend(QueueHandle_t q,const void*p,uint32_t){ Q*Qq=(Q*)q; Qq->d.emplace_back((char*)p,(char*)p+Qq->sz); return 1;}
BaseType_t xQueueSendToFront(QueueHandle_t q,const void*p,uint32_t){ Q*Qq=(Q*)q; Qq->d.emplace_front((char*)p,(char*)p+Qq->sz); return 1;}
BaseType_t xQueueReceive(QueueHandle_t q,void*p,uint32_t){ Q*Qq=(Q*)q; if(Qq->d.empty())return 0; memcpy(p,Qq->d.front().data(),Qq->sz); Qq->d.pop_front(); return 1;}
BaseType_t xQueuePeek(QueueHandle_t q,void*p,uint32_t){ Q*Qq=(Q*)q; if(Qq->d.empty())return 0; memcpy(p,Qq->d.front().data(),Qq->sz); return 1;}
void vTaskDelay(uint32_t){} void xTaskCreatePinnedToCore(void(*)(void*),const char*,int,void*,int,void*,int){}

// ---- world: 60 x 60 cm double loop
const double LO=0.10, HI=0.50, MID=0.30, R=0.08, TW=0.019, MARKL=0.08, DS=0.045, PITCH=0.013, B=0.074, VMAX=0.30;
struct P{double x,y;};
static double rx=0.30, ry=0.10, th=0; // START facing +x
static std::vector<P> obst;
static bool bootButton=false;
static std::vector<P> people;
double distToRect(double x,double y){
  double cx=std::max(LO+R,std::min(HI-R,x)), cy=std::max(LO+R,std::min(HI-R,y));
  double dx=x-cx, dy=y-cy; double d=std::sqrt(dx*dx+dy*dy);
  if (d<1e-9) return std::min(std::min(x-LO,HI-x),std::min(y-LO,HI-y));
  return std::fabs(d-R);
}
struct Bar{P c; bool alongY;};   // station bars across the line
Bar bars[4]={{{0.30,LO},true},{{HI,0.38},false},{{0.30,HI},true},{{LO,0.22},false}};
bool black(double x,double y){
  if (distToRect(x,y) < TW/2) return true;
  if (std::fabs(y-MID)<TW/2 && x>LO && x<HI) return true;              // middle corridor
  for (auto&b:bars){ double along=b.alongY? std::fabs(x-b.c.x):std::fabs(y-b.c.y), across=b.alongY? std::fabs(y-b.c.y):std::fabs(x-b.c.x);
    if (along<TW/2 && across<MARKL/2) return true; }
  return false;
}
int digitalRead(int p){
  if (p==PIN_ESTOP) return bootButton ? 0 : 1;
  for(int i=0;i<5;i++) if(LINE_PINS[i]==p){ double lat=(2-i)*PITCH; // D1 leftmost
      double sx=rx+DS*cos(th)-lat*sin(th), sy=ry+DS*sin(th)+lat*cos(th); return black(sx,sy)?1:0; }
  return 0;
}
static int calls=0; unsigned long pulseIn(int,int,unsigned long){ calls++; if(getenv("DBG2") && calls%5==0) printf("pulse at (%.3f,%.3f) th=%.0f obst=%zu\n",rx,ry,fmod(th*57.3+720,360),obst.size());
  double sx=rx+0.05*cos(th), sy=ry+0.05*sin(th), best=1e9;
  auto chk=[&](P o,double half){ double dx=o.x-sx, dy=o.y-sy, d=sqrt(dx*dx+dy*dy); double a=atan2(dy,dx)-th; a=atan2(sin(a),cos(a));
     if (fabs(a)<0.26) best=std::min(best,d-half); };
  for(auto o:obst) chk(o,0.02); for(auto o:people) chk(o,0.02);
  if (getenv("DBG") && best<0.2) printf("us %.3f at (%.3f,%.3f) th=%.0f\n",best,rx,ry,th*57.3); if (best>0.6) return 0; if (best<0.005) best=0.005; return (unsigned long)(best*100*58);
}
void physics(double dt){
  static double VS=getenv("VSCALE")?atof(getenv("VSCALE")):1.0; double l=(pwmL1-pwmL2)/255.0*VMAX*VS, r=(pwmR1-pwmR2)/255.0*VMAX*VS;
  double v=(l+r)/2, w=(r-l)/B; th+=w*dt; rx+=v*cos(th)*dt; ry+=v*sin(th)*dt;
}
// feed one command
struct F{char t,p; const char*a; float d,m;};
void cmd(const char*type,const char*task,const char*start,std::vector<F> fs,bool scan=false,bool left=true){
  Command c={}; strlcpy(c.type,type,12); strlcpy(c.task,task,32); c.startAct=actOf(start);
  for(auto f:fs) c.marks[c.nMarks++]=Feature{f.t,f.p,actOf(f.a),f.d,f.m}; c.scan=scan; c.scanLeft=left; xQueueSend(cmdQ,&c,0);
}
void runUntil(const char* stateName,int maxMs){
  for(int i=0;i<maxMs;i++){ NOW++; lastCommOkMs=NOW; loop(); physics(0.001);
    if (!strcmp(STATE_NAME[state],stateName)) { printf("   ...%s at t=%.2fs pose (%.3f,%.3f) %.0fdeg\n",stateName,NOW/1000.0,rx,ry,fmod(th*57.3+720,360)); return; } }
  printf("!! timeout waiting for %s; state=%s pose (%.3f,%.3f)\n",stateName,STATE_NAME[state],rx,ry);
}
int main(){
  setup();
  puts("== Scene 8: START -> Station 1 (X-ray 2): pass right T-junction, STOP, scan LEFT; person there");
  people.push_back({0.39,0.38});
  cmd("NAVIGATE","T1","GO",{{'J','L',"PASS",36.6,44.6},{'S','C',"STOP",8,16}},true,true); runUntil("ARRIVED",20000);
  puts("== re-guide to Station 2 (X-ray 1)");
  people.clear();
  cmd("REROUTE","T1","GO",{{'S','C',"STOP",28.6,36.6}},true,true); runUntil("ARRIVED",20000);
  puts("== reset to START facing right; Scene 9: box at (40,10)");
  rx=0.30; ry=0.10; th=0; obst.push_back({0.40,0.10});
  cmd("NAVIGATE","T2","GO",{{'J','L',"PASS",36.6,44.6},{'S','C',"STOP",8,16}},true,true); runUntil("WAIT_REROUTE",15000);
  puts("== REROUTE via Station 3, left T (RIGHT), middle corridor, right T (LEFT), Station 1");
  cmd("REROUTE","T2","GO",{{'S','C',"PASS",28.6,36.6},{'J','R',"RIGHT",8,16},{'J','C',"LEFT",40,48},{'S','C',"STOP",8,16}},true,true);
  runUntil("ARRIVED",40000);
  puts("== Mid-corridor obstacle: from Station 3 (facing up) to Station 1; box on the middle corridor");
  obst.clear(); rx=0.10; ry=0.22; th=M_PI/2; obst.push_back({0.38,0.30});
  cmd("NAVIGATE","T3","GO",{{'J','R',"RIGHT",8,16},{'J','C',"LEFT",40,48},{'S','C',"STOP",8,16}},true,true);
  runUntil("WAIT_REROUTE",30000);
  puts("== REROUTE back to the left T (top of T: RIGHT), over the top past Station 2, STOP Station 1 scan RIGHT");
  cmd("REROUTE","T3","GO",{{'J','C',"RIGHT",0,48},{'S','C',"PASS",36.6,44.6},{'S','C',"STOP",28.6,36.6}},true,false);
  runUntil("ARRIVED",40000);
  puts("== E-STOP checks: BOOT button during centring on a station, dashboard STOP during the room scan turn");
  rx=0.30; ry=0.10; th=0; obst.clear(); people.clear();
  cmd("NAVIGATE","T5","GO",{{'J','L',"PASS",36.6,44.6},{'S','C',"STOP",8,16}},true,true);
  runUntil("CENTER",20000);
  bootButton=true; for(int i=0;i<60;i++){NOW++; lastCommOkMs=NOW; loop(); physics(0.001);} bootButton=false;
  { double x0=rx,y0=ry; for(int i=0;i<3000;i++){NOW++; lastCommOkMs=NOW; loop(); physics(0.001);}
    printf("   during e-stop: state=%s wheels L=%d R=%d moved %.4f m in 3 s\n",STATE_NAME[state],pwmL1-pwmL2,pwmR1-pwmR2,hypot(rx-x0,ry-y0)); }
  cmd("RESUME","","GO",{}); runUntil("SCAN_OUT",3000);
  for(int i=0;i<150;i++){NOW++; lastCommOkMs=NOW; loop(); physics(0.001);}
  cmd("STOP","","GO",{});
  { double t0=th; for(int i=0;i<3000;i++){NOW++; lastCommOkMs=NOW; loop(); physics(0.001);}
    printf("   e-stop mid-scan: state=%s turned %.2f rad in 3 s\n",STATE_NAME[state],fabs(th-t0)); }
  cmd("RESUME","","GO",{}); runUntil("ARRIVED",8000);
  puts("== Ultrasonic unplugged (no echo ever): robot still drives, scan reports FREE with nearest -1");
  puts("== Wrong map: tell it a junction comes first when it is a station -> must stop safely");
  rx=0.30; ry=0.10; th=M_PI; obst.clear();   // at START facing left: next is Station 3 bar
  cmd("NAVIGATE","T4","GO",{{'J','R',"RIGHT",28.6,36.6},{'S','C',"STOP",8,16}},false);
  runUntil("FAULT",20000);
  return 0;
}
