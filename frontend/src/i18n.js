// Patient-facing text in Singapore's four official languages.
// Translations are drafts for the demo: have native speakers check them before showing patients.

export const LANGS = [
  { code: "en", name: "English", speech: "en-SG" },
  { code: "zh", name: "华文", speech: "zh-CN" },
  { code: "ms", name: "Melayu", speech: "ms-MY" },
  { code: "ta", name: "தமிழ்", speech: "ta-IN" },
];

export const PLACE = {
  en: { REGISTRATION: "Registration", XRAY: "X-ray", PHLEBOTOMY: "Blood test", ECG: "ECG", CONSULT: "Consultation",
        REVIEW: "Doctor review", CAST_ROOM: "Cast room", DRESSING: "Dressing room", PROCEDURE: "Procedure room",
        PAYMENT: "Payment", PHARMACY: "Pharmacy" },
  zh: { REGISTRATION: "登记处", XRAY: "X光", PHLEBOTOMY: "验血", ECG: "心电图", CONSULT: "医生问诊",
        REVIEW: "医生复诊", CAST_ROOM: "打石膏室", DRESSING: "换药室", PROCEDURE: "治疗室",
        PAYMENT: "付款处", PHARMACY: "药房" },
  ms: { REGISTRATION: "Pendaftaran", XRAY: "X-ray", PHLEBOTOMY: "Ujian darah", ECG: "ECG", CONSULT: "Konsultasi doktor",
        REVIEW: "Semakan doktor", CAST_ROOM: "Bilik balutan plaster", DRESSING: "Bilik balutan luka",
        PROCEDURE: "Bilik prosedur", PAYMENT: "Kaunter bayaran", PHARMACY: "Farmasi" },
  ta: { REGISTRATION: "பதிவு", XRAY: "எக்ஸ்-ரே", PHLEBOTOMY: "இரத்தப் பரிசோதனை", ECG: "ஈசிஜி", CONSULT: "மருத்துவர் ஆலோசனை",
        REVIEW: "மருத்துவர் மறு ஆய்வு", CAST_ROOM: "மாவுக்கட்டு அறை", DRESSING: "காயக் கட்டு அறை",
        PROCEDURE: "சிகிச்சை அறை", PAYMENT: "கட்டணம் செலுத்துமிடம்", PHARMACY: "மருந்தகம்" },
};

export const TURN = {
  en: { LEFT: "Turn left", RIGHT: "Turn right", STRAIGHT: "Go straight", U_TURN: "Turn around", STOP: "You have arrived", at: "at marker" },
  zh: { LEFT: "左转", RIGHT: "右转", STRAIGHT: "直走", U_TURN: "掉头", STOP: "已到达", at: "标记" },
  ms: { LEFT: "Belok kiri", RIGHT: "Belok kanan", STRAIGHT: "Jalan terus", U_TURN: "Pusing balik", STOP: "Anda sudah sampai", at: "di penanda" },
  ta: { LEFT: "இடதுபுறம் திரும்பவும்", RIGHT: "வலதுபுறம் திரும்பவும்", STRAIGHT: "நேராகச் செல்லவும்", U_TURN: "திரும்பிச் செல்லவும்", STOP: "வந்துவிட்டீர்கள்", at: "குறியீடு" },
};

export const T = {
  en: {
    hello: "Hello", next: "Next", queue: "Queue number", ahead: "{n} patients ahead of you",
    wait: "Estimated wait about {n} minutes.", follow: "Please follow me to {p}.",
    waitAt: "Please wait at {p}. Staff will call your queue number.", at: "You are at {p}.",
    afterThis: "I will show your next step when this is done.", done: "All done",
    doneLine: "Your visit is complete. Please take care.", staff: "A staff member is coming",
    staffLine: "Please stay where you are.", stopped: "Stopped for safety", stoppedLine: "A staff member will help you continue.",
    blocked: "Something is in the way", blockedLine: "I have stopped. If the path stays blocked, I will take another way.",
    noRoom: "All rooms for this service are in use. Please wait here. I will guide you when one is free.",
    readAloud: "Read aloud", guide: "Guide me there", show: "Show me the way", help: "I need help",
    waitingPatient: "I am waiting for my next patient.", enterNo: "Enter your queue number to see your next step.",
    find: "Find my next step", anyOrder: "These tests can be done in any order. Suggested order now:",
    walk: "This service is outside the demo track. Please follow the signs to {p}.",
    disclaimer: "Waiting times are estimates and may change. Staff decide clinical priority.",
  },
  zh: {
    hello: "您好", next: "下一步", queue: "排队号码", ahead: "您前面还有 {n} 位病人",
    wait: "预计等候约 {n} 分钟。", follow: "请跟我到{p}。",
    waitAt: "请在{p}等候。工作人员会叫您的排队号码。", at: "您现在在{p}。",
    afterThis: "完成后我会告诉您下一步。", done: "已完成",
    doneLine: "您今天的门诊已完成。请保重。", staff: "工作人员马上过来",
    staffLine: "请留在原地。", stopped: "为安全起见已停止", stoppedLine: "工作人员会协助您继续。",
    blocked: "前方有障碍物", blockedLine: "我已停下。如果路一直被挡住，我会走另一条路。",
    noRoom: "此项服务的房间都在使用中。请在这里等候，有空房时我会带您过去。",
    readAloud: "朗读", guide: "带我去", show: "告诉我怎么走", help: "我需要帮助",
    waitingPatient: "我在等候下一位病人。", enterNo: "请输入您的排队号码，查看下一步。",
    find: "查看下一步", anyOrder: "这些检查可按任何顺序进行。目前建议的顺序：",
    walk: "这项服务不在示范路线上。请跟随指示牌前往{p}。",
    disclaimer: "等候时间仅为估计，可能会改变。临床优先次序由医护人员决定。",
  },
  ms: {
    hello: "Helo", next: "Seterusnya", queue: "Nombor giliran", ahead: "{n} pesakit di hadapan anda",
    wait: "Anggaran masa menunggu kira-kira {n} minit.", follow: "Sila ikut saya ke {p}.",
    waitAt: "Sila tunggu di {p}. Kakitangan akan memanggil nombor giliran anda.", at: "Anda berada di {p}.",
    afterThis: "Saya akan tunjukkan langkah seterusnya selepas ini.", done: "Selesai",
    doneLine: "Lawatan anda telah selesai. Jaga diri.", staff: "Kakitangan akan datang membantu",
    staffLine: "Sila tunggu di tempat anda.", stopped: "Dihentikan demi keselamatan", stoppedLine: "Kakitangan akan membantu anda meneruskan.",
    blocked: "Ada halangan di hadapan", blockedLine: "Saya telah berhenti. Jika laluan masih terhalang, saya akan ikut jalan lain.",
    noRoom: "Semua bilik untuk perkhidmatan ini sedang digunakan. Sila tunggu di sini. Saya akan membawa anda apabila ada bilik kosong.",
    readAloud: "Baca dengan kuat", guide: "Bawa saya ke sana", show: "Tunjukkan jalan", help: "Saya perlukan bantuan",
    waitingPatient: "Saya sedang menunggu pesakit seterusnya.", enterNo: "Masukkan nombor giliran anda untuk melihat langkah seterusnya.",
    find: "Lihat langkah seterusnya", anyOrder: "Ujian ini boleh dibuat mengikut sebarang urutan. Urutan yang dicadangkan sekarang:",
    walk: "Perkhidmatan ini di luar laluan demo. Sila ikut papan tanda ke {p}.",
    disclaimer: "Masa menunggu ialah anggaran dan boleh berubah. Keutamaan klinikal ditentukan oleh kakitangan.",
  },
  ta: {
    hello: "வணக்கம்", next: "அடுத்தது", queue: "வரிசை எண்", ahead: "உங்களுக்கு முன்னால் {n} பேர் உள்ளனர்",
    wait: "காத்திருப்பு நேரம்: சுமார் {n} நிமிடங்கள்.", follow: "என்னைப் பின்தொடருங்கள்: {p}",
    waitAt: "இங்கே காத்திருக்கவும்: {p}. உங்கள் வரிசை எண் அழைக்கப்படும்.", at: "நீங்கள் இருக்கும் இடம்: {p}",
    afterThis: "இது முடிந்ததும் அடுத்த படியைக் காட்டுவேன்.", done: "முடிந்தது",
    doneLine: "உங்கள் வருகை முடிந்தது. உடல்நலத்தைப் பார்த்துக்கொள்ளுங்கள்.", staff: "ஊழியர் ஒருவர் வருகிறார்",
    staffLine: "தயவுசெய்து இங்கேயே இருங்கள்.", stopped: "பாதுகாப்புக்காக நிறுத்தப்பட்டது", stoppedLine: "தொடர ஊழியர் உதவுவார்.",
    blocked: "வழியில் தடை உள்ளது", blockedLine: "நான் நின்றுவிட்டேன். வழி தொடர்ந்து தடைபட்டால், வேறு வழியில் செல்வேன்.",
    noRoom: "இந்தச் சேவைக்கான அறைகள் அனைத்தும் பயன்பாட்டில் உள்ளன. இங்கே காத்திருக்கவும். அறை காலியானதும் அழைத்துச் செல்வேன்.",
    readAloud: "சத்தமாகப் படி", guide: "என்னை அழைத்துச் செல்லுங்கள்", show: "வழியைக் காட்டு", help: "எனக்கு உதவி தேவை",
    waitingPatient: "அடுத்த நோயாளிக்காகக் காத்திருக்கிறேன்.", enterNo: "அடுத்த படியைப் பார்க்க உங்கள் வரிசை எண்ணை உள்ளிடவும்.",
    find: "அடுத்த படியைக் காட்டு", anyOrder: "இந்தப் பரிசோதனைகளை எந்த வரிசையிலும் செய்யலாம். இப்போது பரிந்துரைக்கப்படும் வரிசை:",
    walk: "இந்தச் சேவை செயல்விளக்கப் பாதையில் இல்லை. அறிவிப்புப் பலகைகளைப் பின்பற்றிச் செல்லவும்: {p}",
    disclaimer: "காத்திருப்பு நேரங்கள் மதிப்பீடுகள் மட்டுமே; மாறலாம். மருத்துவ முன்னுரிமையை ஊழியர்கள் முடிவு செய்வார்கள்.",
  },
};

export const fill = (s, v) => s.replace(/\{(\w+)\}/g, (_, k) => (v[k] ?? ""));
