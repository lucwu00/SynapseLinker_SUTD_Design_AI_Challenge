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

// ---- world: the table AS BUILT - 50 x 50 cm square of tape 5 cm in from the edges, SQUARE corners,
//      middle corridor at y = 30 cm, station bars S1 (right rail), S2 (top), S3 (left rail), no bar at START.
const double LO=0.05, HI=0.55, MID=0.30, TW=0.025, MARKL=0.08, DS=0.045, PITCH=0.013, B=0.074, VMAX=0.30;
struct P{double x,y;};
static double rx=0.30, ry=0.05, th=M_PI; // START facing left
static std::vector<P> obst;
static bool bootButton=false;
static std::vector<P> people;
static bool onSeg(double x,double y,double x0,double y0,double x1,double y1){   // axis-aligned tape strip
  double h=TW/2; return x>=std::min(x0,x1)-h && x<=std::max(x0,x1)+h && y>=std::min(y0,y1)-h && y<=std::max(y0,y1)+h; }
struct Bar{P c; bool alongY;};   // station bars across the line (alongY: the bar runs along y)
Bar bars[3]={{{HI,0.42},false},{{0.21,HI},true},{{LO,0.19},false}};
bool black(double x,double y){
  if (onSeg(x,y,LO,LO,HI,LO)||onSeg(x,y,LO,HI,HI,HI)||onSeg(x,y,LO,LO,LO,HI)||onSeg(x,y,HI,LO,HI,HI)) return true;
  if (onSeg(x,y,LO,MID,HI,MID)) return true;                                   // middle corridor
  for (auto&b:bars){ double along=b.alongY? std::fabs(x-b.c.x):std::fabs(y-b.c.y), across=b.alongY? std::fabs(y-b.c.y):std::fabs(x-b.c.x);
    if (along<TW/2 && across<MARKL/2) return true; }
  return false;
}
static bool sensorBlack(int i){ double lat=(2-i)*PITCH;
  double sx=rx+DS*cos(th)-lat*sin(th), sy=ry+DS*sin(th)+lat*cos(th); return black(sx,sy); }
int analogRead(int){            // Maker Line AN: <10% none, 10-90% position left..right, >90% all on
  int n=0; double sum=0; for(int i=0;i<5;i++) if(sensorBlack(i)){n++; sum+=i;}
  double v = n==0 ? 0.03 : n==5 ? 0.97 : 0.5+0.4*((sum/n)-2)/2;
  return (int)(v*4095);
}
int digitalRead(int p){
  if (p==PIN_ESTOP) return bootButton ? 0 : 1;
  for(int i=0;i<5;i++) if(LINE_PINS[i]==p){ double lat=(2-i)*PITCH; // D1 leftmost
      double sx=rx+DS*cos(th)-lat*sin(th), sy=ry+DS*sin(th)+lat*cos(th); return black(sx,sy)?1:0; }
  return 0;
}
static int calls=0; unsigned long pulseIn(int,int,unsigned long){ calls++; if(getenv("DBG2") && calls%5==0) printf("pulse at (%.3f,%.3f) th=%.0f obst=%zu\n",rx,ry,fmod(th*57.3+720,360),obst.size());
  double sx=rx+0.05*cos(th), sy=ry+0.05*sin(th), best=1e9;
  auto chk=[&](P o,double half){ double dx=o.x-sx, dy=o.y-sy;            // box of half-width `half`, 15 deg beam
     double along=dx*cos(th)+dy*sin(th), lat=-dx*sin(th)+dy*cos(th);
     if (along>0 && fabs(lat) < half + along*tan(0.26)) best=std::min(best,along-half); };
  for(auto o:obst) chk(o,0.02); for(auto o:people) chk(o,0.02);
  if (getenv("DBG") && best<0.2) printf("us %.3f at (%.3f,%.3f) th=%.0f\n",best,rx,ry,th*57.3); if (best>0.6) return 0; if (best<0.005) best=0.005; return (unsigned long)(best*100*58);
}
void physics(double dt){
  static double VS=getenv("VSCALE")?atof(getenv("VSCALE")):1.0; double l=(pwmL1-pwmL2)/255.0*VMAX*VS, r=(pwmR1-pwmR2)/255.0*VMAX*VS;
  // VSCALE: forward speed error (CM_PER_S not calibrated); PSCALE: turn-rate error (PIVOT_90_MS not calibrated)
  static double PS=getenv("PSCALE")?atof(getenv("PSCALE")):1.0;
  double v=(l+r)/2, w=(r-l)/B/VS*PS; th+=w*dt; rx+=v*cos(th)*dt; ry+=v*sin(th)*dt;
}
// feed one command
struct F{char t,p; const char*a; float d,m;};
void cmd(const char*type,const char*task,const char*start,std::vector<F> fs,bool scan=false,bool left=true){
  Command c={}; strlcpy(c.type,type,12); strlcpy(c.task,task,32); c.startAct=actOf(start);
  for(auto f:fs) c.marks[c.nMarks++]=Feature{f.t,f.p,actOf(f.a),f.d,f.m}; c.scan=scan; c.scanLeft=left; xQueueSend(cmdQ,&c,0);
}
static void simRadio(){
#if OFFLINE_DRIVE
  static int RD=getenv("RADIO_MS")?atoi(getenv("RADIO_MS")):300; static int c=0; 
  bool wantOff=!radioWanted; if(wantOff!=radioOff){ if(++c>=RD){ radioOff=wantOff; c=0; } } else c=0;
  static int bad=0; if(!radioOff && state!=BLOCKED && (pwmL1||pwmL2||pwmR1||pwmR2) && ++bad==1) printf("!! wheels turning while radio on (line unreadable)\n");
#endif
}
void runUntil(const char* stateName,int maxMs){
  for(int i=0;i<maxMs;i++){ NOW++; lastCommOkMs=NOW; simRadio(); loop(); physics(0.001);
    if (!strcmp(STATE_NAME[state],stateName)) { printf("   ...%s at t=%.2fs pose (%.3f,%.3f) %.0fdeg\n",stateName,NOW/1000.0,rx,ry,fmod(th*57.3+720,360)); return; } }
  printf("!! timeout waiting for %s; state=%s pose (%.3f,%.3f)\n",stateName,STATE_NAME[state],rx,ry);
}
int main(){
  setup();
  puts("== Scene 1: START (facing left) -> Consult Room 1: corner RIGHT at 25 cm, Station 3 bar, scan RIGHT (free)");
  cmd("NAVIGATE","T1","GO",{{'K','R',"RIGHT",25,33},{'S','C',"STOP",14,22}},true,false); runUntil("ARRIVED",20000);
  puts("== Scene 8: Station 3 -> X-ray Room 1: pass left T, corner RIGHT, Station 2, scan RIGHT; block in the room");
  people.push_back({0.21,0.45});
  cmd("NAVIGATE","T2","GO",{{'J','R',"PASS",11,19},{'K','R',"RIGHT",25,33},{'S','C',"STOP",16,24}},true,false); runUntil("ARRIVED",25000);
  puts("== re-guide to X-ray Room 2: corner RIGHT at 34 cm, Station 1, scan RIGHT (free)");
  people.clear();
  cmd("REROUTE","T2","GO",{{'K','R',"RIGHT",34,42},{'S','C',"STOP",13,21}},true,false); runUntil("ARRIVED",25000);
  puts("== Scene 9: START facing left -> X-ray Room 2: U-turn, corner LEFT, pass right T; box at (45,5)");
  rx=0.30; ry=0.05; th=M_PI; obst.push_back({0.45,0.05});
  cmd("NAVIGATE","T3","U_TURN",{{'K','L',"LEFT",25,33},{'J','L',"PASS",25,33},{'S','C',"STOP",12,20}},true,true); runUntil("WAIT_REROUTE",20000);
  puts("== REROUTE the other way round (first mark = corner 25 cm beyond START + however far it got)");
  cmd("REROUTE","T3","GO",{{'K','R',"RIGHT",-25,33+33},{'S','C',"PASS",14,22},{'J','R',"RIGHT",11,19},{'J','C',"LEFT",50,58},{'S','C',"STOP",12,20}},true,true);
  runUntil("ARRIVED",50000);
  puts("== E-STOP: BOOT button while driving, then RESUME");
  rx=0.30; ry=0.05; th=M_PI; obst.clear(); people.clear();
  cmd("NAVIGATE","T5","GO",{{'K','R',"RIGHT",25,33},{'S','C',"STOP",14,22}},true,false);
  runUntil("FOLLOW",3000);
  for(int i=0;i<600;i++){NOW++; lastCommOkMs=NOW; simRadio(); loop(); physics(0.001);}
  bootButton=true; for(int i=0;i<60;i++){NOW++; lastCommOkMs=NOW; simRadio(); loop(); physics(0.001);} bootButton=false;
  { double x0=rx,y0=ry; for(int i=0;i<3000;i++){NOW++; lastCommOkMs=NOW; simRadio(); loop(); physics(0.001);}
    printf("   during e-stop: state=%s moved %.4f m in 3 s\n",STATE_NAME[state],hypot(rx-x0,ry-y0)); }
  cmd("RESUME","","GO",{}); runUntil("ARRIVED",20000);
  return 0;
}
