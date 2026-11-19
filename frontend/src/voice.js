// Voice / typed questions on the robot screen.
// Not a chatbot: questions are matched to a fixed set of intents, and every answer comes either from the
// patient's live journey (next step, wait, steps left) or from the clinic FAQ below. It never gives
// medical advice; urgent symptoms call a staff member. English, Chinese, Malay and Tamil.
//
// The FAQ is DEMO CONTENT for a fictional clinic. Replace it with the real clinic's information (and
// have staff approve it) before showing it to patients.

const norm = (s) => ` ${s.toLowerCase().replace(/[^\p{L}\p{M}\p{N}\s']/gu, " ").replace(/\s+/g, " ").trim()} `;
const has = (text, words) => words.some((w) => (/^[\x00-\x7f]+$/.test(w) ? text.includes(` ${w}`) : text.includes(w)));

// ---------------------------------------------------------------- safety first
const URGENT = {
  en: ["chest pain", "can't breathe", "cannot breathe", "hard to breathe", "breathless", "faint", "fainting", "dizzy",
       "bleeding", "fell down", "i fell", "collapse", "very painful", "severe pain", "emergency", "heart attack", "stroke"],
  zh: ["胸口痛", "胸痛", "呼吸困难", "喘不过气", "头晕", "晕倒", "流血", "跌倒", "很痛", "紧急"],
  ms: ["sakit dada", "sesak nafas", "susah bernafas", "pening", "pengsan", "berdarah", "jatuh", "sangat sakit", "kecemasan"],
  ta: ["நெஞ்சு வலி", "மூச்சு விட முடியவில்லை", "மூச்சுத் திணறல்", "தலைசுற்றல்", "மயக்கம்", "இரத்தப்போக்கு", "இரத்தம் வருகிறது", "விழுந்து", "அவசர"],
};
const MEDICAL = {
  en: ["medicine", "medication", "dose", "dosage", "painkiller", "pain killer", "antibiotic", "side effect", "my result", "results",
       "diagnosis", "is it broken", "fracture", "surgery", "operation", "should i", "can i eat", "can i drink", "fast before", "fasting",
       "allergic", "allergy", "pregnant", "how bad", "infection", "swelling", "pain"],
  zh: ["吃药", "药", "剂量", "止痛", "副作用", "结果", "报告", "诊断", "手术", "骨折", "怀孕", "过敏", "发炎", "肿", "痛", "可以吃", "空腹"],
  ms: ["ubat", "dos", "tahan sakit", "kesan sampingan", "keputusan", "diagnosis", "pembedahan", "patah", "hamil", "alahan", "bengkak", "sakit", "boleh makan", "puasa"],
  ta: ["மருந்து", "அளவு", "வலி நிவாரணி", "பக்க விளைவு", "முடிவு", "அறுவை", "எலும்பு முறிவு", "கர்ப்ப", "ஒவ்வாமை", "வீக்கம்", "வலி", "சாப்பிடலாமா", "உண்ணாவிரதம்"],
};

// ---------------------------------------------------------------- journey intents (all four languages)
const INTENTS = {
  help: { en: ["help", "nurse", "staff", "someone", "assistance", "wheelchair"], zh: ["帮助", "帮忙", "护士", "工作人员", "轮椅"],
          ms: ["tolong", "bantuan", "jururawat", "kakitangan", "kerusi roda"], ta: ["உதவி", "செவிலியர்", "ஊழியர்", "சக்கர நாற்காலி"] },
  repeat: { en: ["repeat", "again", "say that", "pardon", "didn't hear", "what did you say"], zh: ["再说", "重复", "没听清"],
            ms: ["ulang", "sekali lagi"], ta: ["மீண்டும்", "திரும்ப"] },
  guide: { en: ["take me", "bring me", "guide me", "lead me", "show me the way", "go there", "let's go"], zh: ["带我", "带路"],
           ms: ["bawa saya", "tunjuk jalan"], ta: ["அழைத்து", "கூட்டிச்"] },
  wait: { en: ["how long", "wait", "waiting", "when will", "when is my", "my turn", "how many people", "ahead of me"],
          zh: ["多久", "等多久", "等候", "轮到"], ms: ["berapa lama", "tunggu", "giliran"], ta: ["எவ்வளவு நேரம்", "காத்திரு", "முறை"] },
  left: { en: ["what else", "steps left", "left to do", "how many more", "anything else", "after this", "remaining", "what's left", "finish", "done today"],
          zh: ["还有什么", "还要", "剩下", "之后"], ms: ["lagi", "selepas ini", "berapa langkah"], ta: ["மீதம்", "இதற்குப் பிறகு"] },
  next: { en: ["where do i go", "where should i go", "next", "where to", "where am i going", "lost", "which room", "where is my", "where now"],
          zh: ["下一步", "去哪", "哪里", "迷路"], ms: ["seterusnya", "ke mana", "di mana", "sesat"], ta: ["அடுத்து", "எங்கே", "வழி தெரியவில்லை"] },
  hello: { en: ["hi", "hey", "hello", "good morning", "good afternoon", "good evening", "morning"], zh: ["你好", "您好", "哈喽", "早安", "早上好"],
           ms: ["hai", "helo", "hello", "selamat pagi", "selamat petang", "assalamualaikum"], ta: ["வணக்கம்", "ஹலோ"] },
  where: { en: ["where am i", "where is this", "what is this place", "which clinic"], zh: ["我在哪", "这是哪里", "这里是哪里"],
           ms: ["saya di mana", "di mana saya", "tempat apa ini"], ta: ["நான் எங்கே", "இது எந்த இடம்"] },
  who: { en: ["who are you", "what are you", "what can you do", "your name"], zh: ["你是谁", "你会做什么"],
         ms: ["siapa awak", "apa awak boleh"], ta: ["நீ யார்", "நீங்கள் யார்"] },
  thanks: { en: ["thank", "thanks", "bye", "goodbye"], zh: ["谢谢", "再见"], ms: ["terima kasih", "selamat tinggal"], ta: ["நன்றி"] },
  lang: { en: ["chinese", "mandarin", "malay", "tamil", "english"], zh: ["英文", "英语", "马来", "淡米尔"],
          ms: ["inggeris", "cina", "tamil"], ta: ["ஆங்கிலம்", "சீன", "மலாய்"] },
};

// ---------------------------------------------------------------- clinic FAQ (demo content - see top)
// k: words that point to the question (the patient's language, plus English, which people mix in).
// a: the answer in each language. Translations are drafts: have native speakers check them.
// over: the answer is itself safe for questions that also sound medical ("where do I collect my medicine").
export const FAQ = [
  { k: { en: ["toilet", "restroom", "washroom", "bathroom", "loo", "pee"], zh: ["厕所", "洗手间", "卫生间", "小便"],
         ms: ["tandas", "bilik air"], ta: ["கழிப்பறை", "கழிவறை"] },
    a: { en: "The toilets are next to the Registration counter. There is an accessible toilet beside them.",
         zh: "厕所在登记柜台旁边，旁边也有无障碍厕所。",
         ms: "Tandas terletak di sebelah kaunter Pendaftaran. Tandas mesra OKU juga ada di sebelahnya.",
         ta: "கழிப்பறைகள் பதிவு கவுண்டருக்கு அருகில் உள்ளன. அதன் அருகே மாற்றுத்திறனாளிகளுக்கான கழிப்பறையும் உள்ளது." } },
  { k: { en: ["pay", "payment", "nets", "credit card", "paynow", "cash", "medisave", "insurance", "bill", "cost", "how much", "subsidy", "charges"],
         zh: ["付款", "付钱", "费用", "多少钱", "保健储蓄", "保险", "津贴", "账单", "收费"],
         ms: ["bayar", "kos", "berapa harga", "insurans", "subsidi", "bil", "caj"], ta: ["கட்டண", "பணம்", "செலவு", "எவ்வளவு ஆகும்", "காப்பீ", "மானிய"] },
    a: { en: "You pay at the Payment counter near the end of your visit, by NETS, credit card, PayNow or cash. Staff there can also help with Medisave, insurance and subsidy questions, and give you a cost estimate.",
         zh: "就诊结束前请到付款柜台付款，可用 NETS、信用卡、PayNow 或现金。柜台人员也可以帮您处理保健储蓄（Medisave）、保险和津贴的问题，并提供费用估算。",
         ms: "Anda membuat bayaran di kaunter Pembayaran menjelang akhir lawatan, melalui NETS, kad kredit, PayNow atau tunai. Kakitangan di sana juga boleh membantu tentang Medisave, insurans dan subsidi, serta memberi anggaran kos.",
         ta: "உங்கள் வருகையின் இறுதியில் கட்டணக் கவுண்டரில் NETS, கடன் அட்டை, PayNow அல்லது ரொக்கமாகச் செலுத்தலாம். மெடிசேவ், காப்பீடு, மானியம் பற்றிய கேள்விகளுக்கும் செலவு மதிப்பீட்டுக்கும் அங்குள்ள ஊழியர்கள் உதவுவார்கள்." } },
  { over: true,
    k: { en: ["mc", "medical certificate", "medical leave", "sick leave", "memo", "letter for work"], zh: ["病假", "医药证明", "医疗证明", "病假单"],
         ms: ["cuti sakit", "sijil"], ta: ["சான்றிதழ்", "விடுப்பு"] },
    a: { en: "Your doctor gives a medical certificate during the consultation if you need one. If you forgot to ask, tell the staff at the Payment counter.",
         zh: "如果您需要病假单，医生会在看诊时给您。如果忘了，请告诉付款柜台的工作人员。",
         ms: "Doktor akan memberi sijil cuti sakit (MC) semasa konsultasi jika anda memerlukannya. Jika terlupa, beritahu kakitangan di kaunter Pembayaran.",
         ta: "தேவைப்பட்டால், மருத்துவர் ஆலோசனையின்போது மருத்துவச் சான்றிதழ் (MC) தருவார். மறந்துவிட்டால், கட்டணக் கவுண்டரில் உள்ள ஊழியரிடம் சொல்லுங்கள்." } },
  { k: { en: ["water", "drink", "food", "hungry", "cafe", "café", "canteen", "coffee", "snack"], zh: ["喝水", "饮水", "水", "饿", "咖啡", "食物", "餐厅", "吃东西"],
         ms: ["air minum", "minum", "makan", "lapar", "kafe", "kopi"], ta: ["தண்ணீர்", "குடிக்க", "சாப்பாடு", "பசி", "காபி", "உணவு"] },
    a: { en: "There is a water point next to the waiting area, and a café on level 1. If you have a blood test or X-ray coming up, check with staff before you leave the clinic.",
         zh: "候诊区旁边有饮水处，一楼有咖啡馆。如果您接下来要验血或照X光，离开诊所前请先问工作人员。",
         ms: "Ada tempat air minuman di sebelah ruang menunggu, dan kafe di aras 1. Jika anda akan menjalani ujian darah atau X-ray, tanya kakitangan dahulu sebelum meninggalkan klinik.",
         ta: "காத்திருப்புப் பகுதிக்கு அருகில் குடிநீர் உள்ளது, தளம் 1-இல் உணவகம் உள்ளது. இரத்தப் பரிசோதனை அல்லது எக்ஸ்-ரே வரவிருந்தால், மருத்துவமனையை விட்டுச் செல்லும் முன் ஊழியரிடம் கேளுங்கள்." } },
  { k: { en: ["family", "caregiver", "husband", "wife", "son", "daughter", "accompany", "come with me", "come in"],
         zh: ["家人", "陪", "看护", "儿子", "女儿", "太太", "丈夫"], ms: ["keluarga", "penjaga", "temani", "anak", "isteri", "suami"],
         ta: ["குடும்ப", "பராமரிப்பாளர்", "உடன் வர", "மகன்", "மகள்", "மனைவி", "கணவர்"] },
    a: { en: "One family member or caregiver can stay with you. For the X-ray, they wait outside the room.",
         zh: "可以有一位家人或看护陪您。照X光时，他们需要在房外等候。",
         ms: "Seorang ahli keluarga atau penjaga boleh menemani anda. Semasa X-ray, mereka menunggu di luar bilik.",
         ta: "ஒரு குடும்ப உறுப்பினர் அல்லது பராமரிப்பாளர் உங்களுடன் இருக்கலாம். எக்ஸ்-ரே எடுக்கும்போது அவர்கள் அறைக்கு வெளியே காத்திருப்பார்கள்." } },
  { over: true,
    k: { en: ["x-ray", "xray", "x ray", "metal", "jewellery", "jewelry", "change clothes", "gown"], zh: ["x光", "照光", "首饰", "金属", "换衣服"],
         ms: ["x-ray", "xray", "logam", "barang kemas", "gaun", "tukar baju"], ta: ["எக்ஸ்-ரே", "எக்ஸ்ரே", "நகை", "உலோக", "உடை மாற்"] },
    a: { en: "Before your X-ray, staff may ask you to remove metal items such as jewellery, belts, keys and your phone, and you may change into a gown. Tell the radiographer if you might be pregnant.",
         zh: "照X光前，工作人员可能会请您取下首饰、皮带、钥匙和手机等金属物品，也可能需要换上病人袍。如果您可能怀孕，请告诉放射师。",
         ms: "Sebelum X-ray, kakitangan mungkin meminta anda menanggalkan barang logam seperti barang kemas, tali pinggang, kunci dan telefon, dan anda mungkin perlu memakai gaun. Beritahu juru radiografi jika anda mungkin hamil.",
         ta: "எக்ஸ்-ரேக்கு முன், நகைகள், பெல்ட், சாவி, கைபேசி போன்ற உலோகப் பொருட்களை அகற்றச் சொல்லலாம்; மருத்துவ உடையை அணியவும் சொல்லலாம். நீங்கள் கர்ப்பமாக இருக்கக்கூடும் என்றால் கதிரியக்கப் பணியாளரிடம் சொல்லுங்கள்." } },
  { over: true,
    k: { en: ["blood test", "blood taking", "needle"], zh: ["验血", "抽血"], ms: ["ujian darah", "ambil darah"], ta: ["இரத்தப் பரிசோதனை", "ரத்தப் பரிசோதனை"] },
    a: { en: "The blood test takes a few minutes at the Blood test counter. Your doctor will have told you if you need to fast. If you are not sure, ask the staff there.",
         zh: "验血只需几分钟，在验血柜台进行。医生会告诉您是否需要空腹。如果不确定，请问那里的工作人员。",
         ms: "Ujian darah mengambil masa beberapa minit di kaunter Ujian darah. Doktor akan memberitahu jika anda perlu berpuasa. Jika tidak pasti, tanya kakitangan di sana.",
         ta: "இரத்தப் பரிசோதனைக் கவுண்டரில் சில நிமிடங்களில் முடிந்துவிடும். உண்ணாமல் இருக்க வேண்டுமா என்பதை மருத்துவர் சொல்லியிருப்பார். உறுதியாகத் தெரியாவிட்டால் அங்குள்ள ஊழியரிடம் கேளுங்கள்." } },
  { over: true,
    k: { en: ["pharmacy", "collect medicine", "collect my medicine", "prescription", "medicine collection"], zh: ["药房", "拿药", "取药", "配药"],
         ms: ["farmasi", "ambil ubat"], ta: ["மருந்தக", "மருந்து வாங்க"] },
    a: { en: "After payment, go to the Pharmacy and show your queue number. The pharmacist will explain how to take your medicine.",
         zh: "付款后，请到药房并出示您的排队号码。药剂师会告诉您如何服药。",
         ms: "Selepas pembayaran, pergi ke Farmasi dan tunjukkan nombor giliran anda. Ahli farmasi akan menerangkan cara mengambil ubat anda.",
         ta: "பணம் செலுத்திய பிறகு, மருந்தகத்துக்குச் சென்று உங்கள் வரிசை எண்ணைக் காட்டுங்கள். மருந்தை எப்படி உட்கொள்வது என்று மருந்தாளர் விளக்குவார்." } },
  { k: { en: ["appointment", "next visit", "follow up", "follow-up", "reschedule", "change my appointment", "book", "healthhub"],
         zh: ["预约", "复诊", "下次", "改期"], ms: ["temujanji", "lawatan seterusnya", "tukar tarikh"], ta: ["சந்திப்பு", "அடுத்த வருகை", "தேதி மாற்"] },
    a: { en: "Staff at the Payment counter will book your next appointment. To change it later, use the HealthHub app or call the clinic.",
         zh: "付款柜台的工作人员会帮您预约下次复诊。以后若要更改，可使用 HealthHub 应用程序或致电诊所。",
         ms: "Kakitangan di kaunter Pembayaran akan menempah temujanji anda yang seterusnya. Untuk menukarnya kemudian, gunakan aplikasi HealthHub atau hubungi klinik.",
         ta: "உங்கள் அடுத்த சந்திப்பைக் கட்டணக் கவுண்டர் ஊழியர்கள் பதிவு செய்வார்கள். பின்னர் மாற்ற, HealthHub செயலியைப் பயன்படுத்துங்கள் அல்லது மருத்துவமனையை அழையுங்கள்." } },
  { k: { en: ["park", "parking", "car park", "carpark", "taxi", "grab", "bus", "mrt", "transport"], zh: ["停车", "德士", "出租车", "巴士", "地铁"],
         ms: ["letak kereta", "parkir", "teksi", "bas"], ta: ["வாகன நிறுத்த", "பார்க்கிங்", "டாக்சி", "பேருந்து"] },
    a: { en: "The car park is charged at the exit gantry. The taxi stand is at the main entrance. Ask the staff at the counter for parking concessions.",
         zh: "停车费在出口闸门付款。德士站在正门。如需停车优惠，请问柜台的工作人员。",
         ms: "Bayaran letak kereta dibuat di palang keluar. Perhentian teksi terletak di pintu masuk utama. Tanya kakitangan di kaunter tentang konsesi letak kereta.",
         ta: "வாகன நிறுத்தக் கட்டணம் வெளியேறும் வாயிலில் செலுத்தப்படும். டாக்சி நிறுத்தம் முதன்மை நுழைவாயிலில் உள்ளது. வாகன நிறுத்தச் சலுகைக்குக் கவுண்டரில் கேளுங்கள்." } },
  { k: { en: ["wifi", "wi-fi", "internet"], zh: ["无线", "上网", "网络"], ms: ["internet"], ta: ["வைஃபை", "இணைய"] },
    a: { en: "Free Wireless@SG Wi-Fi is available in the clinic.", zh: "诊所提供免费 Wireless@SG 无线网络。",
         ms: "Wi-Fi percuma Wireless@SG tersedia di klinik.", ta: "மருத்துவமனையில் இலவச Wireless@SG வைஃபை உள்ளது." } },
  { k: { en: ["charge my phone", "phone charger", "power point"], zh: ["充电"], ms: ["mengecas", "cas telefon"], ta: ["சார்ஜ்"] },
    a: { en: "There are charging points in the waiting area.", zh: "候诊区有充电处。", ms: "Ada tempat mengecas telefon di ruang menunggu.",
         ta: "காத்திருப்புப் பகுதியில் சார்ஜ் செய்யும் இடங்கள் உள்ளன." } },
  { k: { en: ["why", "so long", "so slow", "taking long", "skip the queue", "who goes first", "why did they go first", "order"],
         zh: ["为什么", "这么久", "太慢", "插队"], ms: ["kenapa", "mengapa", "lama sangat", "lambat"], ta: ["ஏன்", "தாமதம்"] },
    a: { en: "Patients are seen by appointment time and by clinical priority, which staff decide. Waiting times are estimates and can change. I suggest the station that is likely to be quickest for you.",
         zh: "病人按预约时间和临床优先次序看诊，这由工作人员决定。等候时间只是估计，可能会改变。我会建议对您来说可能最快的站点。",
         ms: "Pesakit dilihat mengikut masa temujanji dan keutamaan klinikal yang ditentukan oleh kakitangan. Masa menunggu hanyalah anggaran dan boleh berubah. Saya mencadangkan stesen yang mungkin paling cepat untuk anda.",
         ta: "நோயாளிகள் சந்திப்பு நேரம் மற்றும் ஊழியர்கள் முடிவு செய்யும் மருத்துவ முன்னுரிமைப்படி பார்க்கப்படுகிறார்கள். காத்திருப்பு நேரங்கள் மதிப்பீடுகள் மட்டுமே, மாறலாம். உங்களுக்கு விரைவாக இருக்கக்கூடிய இடத்தை நான் பரிந்துரைக்கிறேன்." } },
  { k: { en: ["smoke", "smoking"], zh: ["抽烟", "吸烟"], ms: ["merokok", "rokok"], ta: ["புகை"] },
    a: { en: "Smoking is not allowed anywhere in the hospital.", zh: "医院内全面禁烟。",
         ms: "Merokok tidak dibenarkan di mana-mana kawasan hospital.", ta: "மருத்துவமனையில் எங்கும் புகைபிடிக்கக் கூடாது." } },
  { k: { en: ["opening hours", "what time do you close", "closing time", "open on"], zh: ["开放时间", "几点关门", "营业时间", "几点开"],
         ms: ["waktu buka", "tutup pukul", "buka pukul"], ta: ["திறக்கும் நேரம்", "மூடும் நேரம்"] },
    a: { en: "The clinic runs from 8:30 am to 5 pm on weekdays. Please ask staff about Saturday sessions.",
         zh: "诊所于平日上午8时30分至下午5时开放。星期六的门诊请询问工作人员。",
         ms: "Klinik dibuka dari 8.30 pagi hingga 5 petang pada hari bekerja. Sila tanya kakitangan tentang sesi hari Sabtu.",
         ta: "மருத்துவமனை வார நாட்களில் காலை 8:30 முதல் மாலை 5 மணி வரை இயங்கும். சனிக்கிழமை பற்றி ஊழியரிடம் கேளுங்கள்." } },
];

const pick = (lang, key) => (INTENTS[key][lang] || []).concat(lang === "en" ? [] : INTENTS[key].en);

/**
 * text: what the patient said or typed. ctx: { lang, p (patient), say (current headline+line), waitText,
 * stepsLeft (array of place names), canGuide, isQueued }. Returns { reply, action } where action is one of
 * "help" | "guide" | "repeat" | { lang: "zh" } | null.
 */
export function answer(text, ctx, X) {
  const lang = ctx.lang, t = norm(text);
  if (!t.trim()) return { reply: X.notHeard, action: null };

  if (has(t, (URGENT[lang] || []).concat(URGENT.en))) return { reply: X.urgent, action: "help" };
  if (has(t, pick(lang, "lang")) && /chinese|mandarin|华|中文|cina|சீன/.test(t)) return { reply: "好的，我会用华文。", action: { lang: "zh" } };
  if (has(t, pick(lang, "lang")) && /malay|melayu|马来|மலாய்/.test(t)) return { reply: "Baik, saya akan guna Bahasa Melayu.", action: { lang: "ms" } };
  if (has(t, pick(lang, "lang")) && /tamil|淡米尔|தமிழ்/.test(t)) return { reply: "சரி, நான் தமிழில் பேசுகிறேன்.", action: { lang: "ta" } };
  if (has(t, pick(lang, "lang")) && /english|英文|英语|inggeris|ஆங்கிலம்/.test(t)) return { reply: "Okay, I will speak English.", action: { lang: "en" } };

  // a short greeting on its own ("hey", "good morning") - longer questions that start with one go on below
  const greet = pick(lang, "hello").find((w) => t.startsWith(` ${w} `) || t.startsWith(` ${w}`) && !/^[\x00-\x7f]+$/.test(w));
  if (greet && t.trim().split(" ").length <= 4 && !has(t, pick(lang, "help")))
    return { reply: X.hello, action: null };
  if (has(t, pick(lang, "where"))) return { reply: ctx.p ? ctx.say : X.here, action: null };

  if (has(t, pick(lang, "help"))) return { reply: ctx.p ? X.calling : X.noPatientHelp, action: ctx.p ? "help" : null };
  if (has(t, pick(lang, "repeat"))) return { reply: ctx.say, action: "repeat" };
  if (has(t, pick(lang, "who"))) return { reply: X.who, action: null };
  if (has(t, pick(lang, "thanks"))) return { reply: X.welcome, action: null };

  // the clinic FAQ wins over the journey words it shares ("x-ray", "pay"), unless the question is about *my* next step
  const words = (f) => (f.k[lang] || []).concat(lang === "en" ? [] : f.k.en);
  const faq = FAQ.map((f) => ({ f, n: words(f).filter((k) => has(t, [k])).length })).sort((a, b) => b.n - a.n)[0];
  const hit = faq && faq.n > 0 ? faq.f : null;
  const medical = has(t, (MEDICAL[lang] || []).concat(lang === "en" ? [] : MEDICAL.en)) && !(hit && hit.over);
  const general = ["why", "is there", "do you have", "can i pay", "can my", "为什么", "有没有", "kenapa", "mengapa", "ada tak", "ஏன்", "உள்ளதா"];
  if (hit && !medical && has(t, general)) return { reply: hit.a[lang], action: null };

  if (!ctx.p && (has(t, pick(lang, "next")) || has(t, pick(lang, "wait")) || has(t, pick(lang, "left"))))
    return { reply: X.noPatient, action: null };
  if (ctx.p && has(t, pick(lang, "guide"))) return ctx.canGuide ? { reply: X.okGuide, action: "guide" } : { reply: ctx.say, action: null };
  if (ctx.p && has(t, pick(lang, "wait"))) return { reply: ctx.waitText || ctx.say, action: null };
  if (ctx.p && has(t, pick(lang, "left")))
    return { reply: ctx.stepsLeft.length ? X.left.replace("{n}", ctx.stepsLeft.length).replace("{list}", ctx.stepsLeft.join(", ")) : X.nothingLeft, action: null };
  const mine = / my | i |我|saya|என் |நான்/.test(t);
  if (ctx.p && has(t, pick(lang, "next")) && !(hit && !mine)) return { reply: ctx.say, action: null };

  if (medical) return { reply: X.medical, action: null };
  if (hit) return { reply: hit.a[lang], action: null };
  return { reply: X.notSure, action: null };
}

/** Browser speech recognition (Chrome, Edge, Safari). Needs https or localhost. */
export const Recognition = typeof window !== "undefined" ? (window.SpeechRecognition || window.webkitSpeechRecognition) : null;
export const secureOk = typeof window !== "undefined" && window.isSecureContext;
