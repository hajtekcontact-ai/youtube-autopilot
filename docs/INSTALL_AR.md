# دليل التثبيت والإعداد الشامل (Installation Guide)

مرحبًا بك في **YouTube AI Autopilot Cloud**. هذا الدليل يشرح بالتفصيل كيفية إعداد وتثبيت النظام محلياً أو على خادم سحابي بالكامل وبميزانية $0.

---

## 📋 المتطلبات الأساسية للنظام

1. **نظام التشغيل**: Linux (Ubuntu 22.04+ / Debian 12+ / Kali Linux) أو macOS أو Windows مع WSL2.
2. **Python**: إصدار 3.10 أو أحدث (مختبر بالكامل على Python 3.12 و 3.14).
3. **FFmpeg**: مثبت في النظام مع برامج الترميز `libx264` و `aac`.
4. **الخطوط**: خطوط UTF-8 تدعم الإنجليزية والعربية (`fonts-dejavu-core`, `fonts-noto`).
5. **Git**: لإدارة النسخ والنشر التلقائي عبر GitHub Actions.

---

## 🛠️ خطوات التثبيت خطوة بخطوة

### 1. استنساخ المستودع (Clone Repository)
```bash
cd /home/kali/Downloads/AI
cd youtube-autopilot
```

### 2. تثبيت الحزم النظامية (Debian / Ubuntu / Kali)
```bash
sudo apt-get update
sudo apt-get install -y ffmpeg fonts-dejavu-core fonts-noto ca-certificates curl
```

### 3. إنشاء البيئة الافتراضية (Virtual Environment)
```bash
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### 4. إعداد ملف المتغيرات البيئية (.env)
انسخ القالب الافتراضي:
```bash
cp .env.example .env
```

قم بفتح ملف `.env` وتعبئة البيانات المطلوبة:
```ini
# YouTube OAuth 2.0 Credentials
YOUTUBE_CLIENT_ID=your_client_id.apps.googleusercontent.com
YOUTUBE_CLIENT_SECRET=your_client_secret
YOUTUBE_REDIRECT_URI=http://localhost:5000/oauth/callback

# Gemini API (اختياري - لتوليد نصوص ذكية مجاناً)
GEMINI_API_KEY=your_gemini_api_key

# لوحة التحكم الإدارية
ADMIN_USERNAME=admin
ADMIN_PASSWORD_HASH=
DASHBOARD_SECRET_KEY=your_random_secret_key

# التشفير وإعدادات التشغيل
ENCRYPTION_KEY=your_fernet_encryption_key
TIMEZONE=America/New_York
```

لإنشاء مفتاح تشفير عشوائي، يمكنك تشغيل:
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### 5. تهيئة قاعدة البيانات المحلية
```bash
source venv/bin/activate
python main.py init-db
```

### 6. التحقق من صحة النظام (Health Check)
```bash
python main.py health
```

---

## 🚀 تشغيل لوحة التحكم باللغة العربية (Web Dashboard)
لتشغيل واجهة الإدارة التفاعلية باللغة العربية:
```bash
source venv/bin/activate
python main.py serve
```
ثم افتح المتصفح على:
👉 **`http://localhost:5000`**

- **اسم المستخدم الافتراضي**: `admin`
- **كلمة المرور الافتراضية**: يتم تعيينها عند أول تشغيل أو عبر الإعداد الأولي (`python main.py setup`).

---

## 🐳 التشغيل باستخدام Docker (اختياري)
إذا كنت تفضل استخدام الحاويات:
```bash
docker compose up -d --build
```
وسيتم تشغيل لوحة التحكم على المنفذ `5000` مع عزل كامل وإعادة تشغيل تلقائية عند الفشل.
