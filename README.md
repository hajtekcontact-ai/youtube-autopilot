# 🚀 YouTube AI Autopilot Cloud
> **منصة سحابية آلية ومتكاملة لإنتاج وجدولة ونشر فيديوهات يوتيوب بالذكاء الاصطناعي بميزانية $0**

[![Tests](https://github.com/user/youtube-autopilot/actions/workflows/tests.yml/badge.svg)](https://github.com/user/youtube-autopilot/actions/workflows/tests.yml)
[![Daily Production](https://github.com/user/youtube-autopilot/actions/workflows/daily-production.yml/badge.svg)](https://github.com/user/youtube-autopilot/actions/workflows/daily-production.yml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%20%7C%203.14-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Budget: $0/Month](https://img.shields.io/badge/Budget-%240%2Fmonth-brightgreen.svg)]()

---

## 🌟 مميزات النظام الأساسية

1. **استقلالية تامة في السحابة (Autonomous Cloud Execution)**:
   - يعمل النظام 24/7 حتى عندما يكون حاسوبك الشخصي وبيئة Antigravity مغلقة تماماً.
   - يدعم سير عمل مجدول عبر **GitHub Actions** أو سيرفر **Oracle Cloud Always Free**.

2. **الهدف الإنتاجي اليومي (Daily Target Output)**:
   - **4 فيديوهات قصيرة (YouTube Shorts)**: عمودية (1080x1920) سريعة ومحفزة للتفاعل.
   - **1 فيديو طويل (Long-form Video)**: أفقي (1920x1080) من 5 إلى 10 دقائق يغطي مواضيع تعليمية وتقنية وعلمية شيقة.
   - مواعيد النشر اليومية بتوقيت نيويورك (`America/New_York`):
     - `09:00` (Short) | `12:00` (Short) | `15:00` (Short) | `18:00` (Long) | `21:00` (Short)

3. **لوحة تحكم تفاعلية باللغة العربية (Arabic Web Dashboard)**:
   - واجهة كاملة باللغة العربية تدعم اتجاه اليمين لليسار (RTL) وتصميم حديث (Dark/Light Mode).
   - تحكم كامل بنقرة واحدة (إيقاف مؤقت، استئناف، توقف طارئ، إعادة محاولة المهام).
   - استعراض مكتبة الفيديوهات، التحليلات، تقويم النشر، تقارير الإيرادات، ومراقبة الموارد.

4. **محرك المحتوى الذكي (AI Content Engine)**:
   - توليد أفكار حصرية ومبتكرة باستخدام **Gemini API** المجاني مع قوالب علمية احتياطية مدمجة.
   - كتابة نصوص بلكنة أمريكية طبيعية مع خطافات جذابة وتجنب العناوين المضللة (Clickbait).
   - تدقيق الحقائق والعلوم ومنع تكرار المواضيع باستخدام فحص التشابه وبصمة المحتوى (SHA-256).

5. **خط إنتاج فيديو آلي متكامل (Automated Video Pipeline)**:
   - أصوات طبيعية وواقعية بدون تكلفة عبر محرك **Microsoft Edge Neural TTS**.
   - توليد إطارات ورسوم متحركة بدقة فائقة عبر **Pillow (PIL)** بتصميمات عصرية ومتناسقة.
   - ترجمات مدمجة تلقائية ومحاذاة زمنية محسوبة مع مراعاة مناطق الأمان في الهواتف.
   - موازنة ومطابقة درجات الصوت الاحترافية وفق معايير البث (EBU R128 Loudnorm).
   - توليد صور مصغرة (Thumbnails) بدقة 1280x720 و 1080x1920 بتصميمات تزيد نسبة النقر (CTR).

6. **تكامل رسمي وآمن مع YouTube Data & Analytics API**:
   - مصادقة رسمية عبر Google OAuth 2.0 دون تخزين أي كلمات مرور.
   - تشفير المفاتيح والرموز في قاعدة البيانات عبر **Fernet (AES)**.
   - رفع بنظام الأجزاء المستأنفة (Resumable Uploads) مع التراجع الأسي عند انقطاع الاتصال.
   - الرفع كـ "خاص (Private)" افتراضياً لضمان السلامة والامتثال قبل النشر.

7. **استراتيجية ذاتية التحسين (Adaptive Bandit Strategy)**:
   - جمع تحليلات أداء القناة (المشاهدات، وقت الاحتفاظ، نسبة النقر) في فترات 48h و 7d و 28d.
   - خوارزمية **Thompson Sampling** لضبط وتيرة الفيديوهات وأنماط الرسوم تلقائياً لتحقيق أعلى أداء.

---

## 📂 هيكلة المشروع (Project Structure)

```
youtube-autopilot/
├── src/                         # شيفرة المصدر الأساسية
│   ├── content/                 # توليد الأفكار، كتابة النصوص، تدقيق الحقائق والأصالة
│   ├── video/                   # الصوت العصبي (TTS)، الإطارات الرسومية، الترجمة، الرندرة
│   ├── youtube/                 # مصادقة OAuth، الرفع المستأنف، وتوليد البيانات الوصفية
│   ├── analytics/               # جامع الإحصائيات، وخوارزمية التكيف الذاتي (Bandit)
│   ├── dashboard/               # لوحة التحكم العربية، مسارات Flask، وتكامل SocketIO
│   ├── cloud/                   # المجدول الزمني الذكي، وفحص صحة المكونات
│   ├── utils/                   # دوال المساعدة، التوقيت، والتسجيل الآمن
│   ├── pipeline.py              # المنسق العام لدورة الإنتاج خطوة بخطوة
│   ├── quality.py               # فحص معايير الجودة التقنية قبل الرفع
│   ├── quota_manager.py         # إدارة حصص API اليومية ومفتاح حظر التكاليف
│   ├── security.py              # التشفير وإدارة الأسرار والتوقف الطارئ
│   └── notifications.py         # نظام التنبيهات والتقارير اليومية
├── templates/dashboard/         # قوالب HTML للوحة التحكم العربية (13 صفحة RTL)
├── static/                      # ملفات التصميم CSS و JavaScript المتوافقة مع العربية
├── .github/workflows/           # ملفات الأتمتة السحابية (الإنتاج، التحليلات، الفحص الدوري)
├── tests/                       # حزم الاختبارات الشاملة (Unit & Integration Tests)
├── docs/                        # التوثيق الشامل باللغة العربية
│   ├── INSTALL_AR.md            # دليل التثبيت خطوة بخطوة
│   ├── OPERATIONS_AR.md         # دليل الاستخدام اليومي ولوحة التحكم
│   ├── DEPLOYMENT_AR.md         # دليل النشر السحابي والتشغيل المستقل
│   ├── SECURITY_AR.md           # دليل الأمان وحماية القناة
│   ├── COST_AND_QUOTAS_AR.md    # دليل الحصص والتكاليف ($0 شهرياً)
│   └── TROUBLESHOOTING_AR.md    # دليل استكشاف الأخطاء وحلها
├── Dockerfile                   # إعداد بيئة Docker المعزولة
├── docker-compose.yml           # تشغيل الحاوية الموحدة
├── requirements.txt             # حزم بايثون المعتمدة
└── main.py                      # نقطة الدخول الرئيسية للأوامر والتشغيل
```

---

## ⚡ البدء السريع (Quickstart)

```bash
# 1. الدخول للمجلد
cd /home/kali/Downloads/AI/youtube-autopilot

# 2. إنشاء وتفعيل البيئة الافتراضية
python3 -m venv venv
source venv/bin/activate

# 3. تثبيت المتطلبات
pip install -r requirements.txt

# 4. إعداد المتغيرات
cp .env.example .env

# 5. تهيئة قاعدة البيانات
python main.py init-db

# 6. تشغيل لوحة التحكم العربية
python main.py serve
```
افتح المتصفح على: `http://localhost:5000` للدخول إلى لوحة التحكم العربية.

---

## 🧪 أوامر الاختبار والتشغيل المباشر

- **فحص صحة النظام**:
  ```bash
  python main.py health
  ```
- **تشغيل محاكاة إنتاج تجريبية (Dry-run)**:
  ```bash
  python main.py dry-run
  ```
- **إنتاج فيديو تجريبي (Short & Long)**:
  ```bash
  python main.py test-render --type short
  python main.py test-render --type long
  ```
- **تشغيل كامل حزمة الاختبارات الآلية**:
  ```bash
  pytest tests/ -v
  ```

---

## 📜 التوثيق والدعم
للاطلاع على الشروحات المفصلة باللغة العربية، راجع مجلد [`docs/`](docs/).
- [دليل التثبيت الشامل](docs/INSTALL_AR.md)
- [دليل الاستخدام والتشغيل اليومي](docs/OPERATIONS_AR.md)
- [دليل النشر السحابي والتشغيل المستقل](docs/DEPLOYMENT_AR.md)
- [معايير الأمان وحماية القناة](docs/SECURITY_AR.md)
- [دليل الحصص والتكاليف ($0 شهرياً)](docs/COST_AND_QUOTAS_AR.md)
- [استكشاف الأخطاء والتعافي الذاتي](docs/TROUBLESHOOTING_AR.md)
