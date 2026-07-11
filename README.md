# Taklif & Shikoyat + Tug'ilgan kun Bot (birlashtirilgan)

Ikkita loyiha bitta botga birlashtirildi:

- **Taklif/Shikoyat bot** — Mini App orqali xodim/mehmonlardan taklif va shikoyatlarni qabul qiladi, admin panel, chat, Google Sheets eksport.
- **Tug'ilgan kun bot** — Excel fayl (admin panel ichidan yuklanadi) asosida har kuni soat **10:00** da (Toshkent vaqti) tug'ilgan kunlarni Telegram **kanalga** avtomatik post qiladi, va **09:30** da adminlarga eslatma yuboradi.

Ikkalasi ham bitta Telegram bot tokenidan foydalanadi va bitta Railway loyihasida, bitta Supabase (PostgreSQL) bazasida ishlaydi.

---

## 1. Supabase (baza)

1. https://supabase.com — yangi loyiha oching (agar hali yo'q bo'lsa), kuchli parol o'rnating va uni saqlab qo'ying.
2. Loyiha dashboardida yuqoridagi **"Connect"** tugmasini bosing.
3. **Muhim:** "Direct connection" emas, **"Session pooler"** bo'limini tanlang. Sababi: Supabase'ning to'g'ridan-to'g'ri manzili (`db.xxx.supabase.co`) faqat IPv6 orqali ishlaydi, Railway esa IPv6 chiquvchi ulanishlarni qo'llab-quvvatlamaydi — shu sababli Direct connection satridan foydalansangiz, bot Railway'da bazaga ulana olmaydi.
4. Session pooler satri quyidagicha ko'rinadi:
   ```
   postgresql://postgres.abcdxyzproject:[YOUR-PASSWORD]@aws-0-eu-central-1.pooler.supabase.com:5432/postgres
   ```
   `[YOUR-PASSWORD]` o'rniga haqiqiy parolni yozing (agar parolda `@ # % &` kabi belgilar bo'lsa, ularni URL-encode qiling, masalan `@` → `%40`). Shu to'liq satr — sizning `DATABASE_URL` qiymatingiz.
5. Boshqa hech narsa qo'lda yaratish shart emas — kod o'zi jadval va ustunlarni (`messages`, `contacts`, `replies`, `chats`, `employees`) birinchi ishga tushganda avtomatik yaratadi (`init_db()`).

## 2. Railway (deploy)

1. Ushbu papkani (yoki GitHub reponi) Railway'da **"Deploy from GitHub repo"** orqali ulang. Repo tuzilishi:
   ```
   bot.py
   index.html
   birthday.jpg
   requirements.txt
   Procfile
   ```
2. Railway loyihasiga quyidagi **Environment Variables** larni kiriting:

   | Nomi | Qiymati | Izoh |
   |---|---|---|
   | `BOT_TOKEN` | Telegram bot tokeni | @BotFather dan olinadi |
   | `DATABASE_URL` | Supabase connection string | 1-qadamdan |
   | `BIRTHDAY_CHANNEL_ID` | `@kanal_username` yoki `-100xxxxxxxxxx` | Tug'ilgan kun posti yuboriladigan kanal |
   | `GOOGLE_PRIVATE_KEY` | Google service account private key | Google Sheets uchun (agar ishlatilsa) |
   | `RAILWAY_PUBLIC_DOMAIN` | Railway avtomatik beradi | Webhook uchun — odatda o'zi to'ldiriladi |
   | `PORT` | Railway avtomatik beradi | Qo'lda kerak emas |

   > **Muhim:** `BOT_TOKEN` avval kodda ochiq (hardcoded) turgan edi — xavfsizlik uchun endi u faqat Environment Variable orqali o'qiladi. Railway'ga albatta shu o'zgaruvchini qo'shing, aks holda bot ishlamaydi.

3. Kanal ID'ni olish: botni kanalga **admin** qilib qo'shing, so'ng kanalga istalgan post yuboring va `https://api.telegram.org/bot<BOT_TOKEN>/getUpdates` orqali `chat.id` ni ko'ring (odatda `-100` bilan boshlanadi). Agar kanal public bo'lsa, oddiy `@kanal_username` ham ishlaydi.
4. Railway avtomatik deploy qiladi (`Procfile`: `web: python bot.py`). Deploy tugagach, bot webhook'ni o'zi o'rnatadi (`RAILWAY_PUBLIC_DOMAIN` orqali).
5. **Muhim:** `index.html` ichidagi `SERVER_URL` o'zgaruvchisini (703-qator atrofida) yangi Railway domeningizga moslang, agar u eski domenga (`web-production-dd6e7.up.railway.app`) qarab tursa:
   ```js
   const SERVER_URL = 'https://SIZNING-YANGI-DOMENINGIZ.up.railway.app';
   ```
   Bu faylni GitHub Pages yoki boshqa statik hosting'ga qo'yasiz (Mini App frontendi sifatida), `MINI_APP_URL` ham `bot.py` ichida shunga mos bo'lishi kerak.

## 3. Tug'ilgan kun ma'lumotlarini yuklash

Excel fayl endi repo ichida saqlanmaydi — admin panel orqali yuklanadi va Supabase'dagi `employees` jadvaliga yoziladi:

1. Botning Mini App'ida **Admin Panel** ga kiring.
2. Yuqori qismdagi **🎂** tugmasini bosing.
3. **"Faylni tanlash"** orqali `.xlsx` faylni tanlang. Ustunlar: `FIO`, `Filial`, `Tug'ilgan_sana`.
4. **"Yuklash"** tugmasini bosing — yangi fayl eski ma'lumotlarni to'liq almashtiradi.

Har kuni:
- **09:30** (Toshkent) — barcha adminlarga bugungi tug'ilgan kunlar ro'yxati Telegram xabar sifatida yuboriladi (agar bo'lmasa — "bugun yo'q" deb xabar beradi).
- **10:00** (Toshkent) — agar bugun tug'ilgan kun bo'lsa, kanalga `birthday.jpg` rasmi bilan avtomatik post yuboriladi.

## 4. Doimiy "Murojaatlar" tugmasi va avtomatik yangilanish

Endi botda ikkita muammo hal qilindi:

1. **`/start` bosish shart emas** — bot ishga tushganda (`set_chat_menu_button`) barcha foydalanuvchilar uchun xabar yozish maydoni yonida doimiy **"Murojaatlar"** tugmasi o'rnatiladi. Bu tugma Telegram'ning "Menu Button" xususiyati — u har doim mavjud, xabarlar tarixida "yo'qolib" qolmaydi. Mini App'ning o'zi foydalanuvchi admin yoki oddiy xodim ekanini avtomatik aniqlaydi (`/role/<id>` orqali), shuning uchun bitta tugma hammaga yetarli.

2. **Eski (keshlangan) versiya ko'rinishi muammosi** — Telegram WebView ba'zan `index.html`ning eski nusxasini keshlab qo'yadi, shuning uchun yangilanishdan keyin xato yoki eski interfeys ko'rinishi mumkin edi. Endi bot har safar ishga tushganda (`APP_VERSION`) avtomatik yangi versiya raqami yaratadi va uni Mini App havolasiga (`?v=...`) qo'shadi. Har safar Railway'da qayta deploy qilinganda bu raqam o'zgaradi, shuning uchun Telegram uni "yangi sahifa" deb hisoblab, keshni chetlab o'tadi — foydalanuvchilar hech narsa qilmasdan eng so'nggi versiyani ko'radi.

**Diqqat:** bu faqat `bot.py`dagi tugmalarga (menu button, /start, admin panel va h.k.) tegishli. Agar biror joyda o'zingiz qo'lda link ulashsangiz (masalan guruhga), shu havolaga ham `?v=...` qo'shishni unutmang, aks holda eski versiya ko'rsatilishi mumkin.

## 5. Doimiy ishlashi haqida

- Railway hobby/starter rejasida ilova **24/7 doimiy** ishlaydi (uxlab qolmaydi), agar loyiha "sleep" siyosati yoqilmagan bo'lsa — buni Railway dashboard'dan tekshiring.
- Scheduler (`APScheduler`) ilova jarayoni ichida ishlaydi, shuning uchun ilova qayta ishga tushsa (deploy, restart) ham keyingi kunlik vaqtga avtomatik moslashadi — qo'shimcha cron sozlash shart emas.
- Baza Supabase'da bo'lgani uchun Railway qayta deploy qilinganda yoki konteyner qayta tiklanganda ham barcha ma'lumotlar (murojaatlar, xodimlar ro'yxati) saqlanib qoladi.

## 6. Diqqat qilinadigan narsa

`index.html` ichida "Taraqqiyot" (auto-tarjima) funksiyasi to'g'ridan-to'g'ri `api.anthropic.com` ga so'rov yuboradi va API kalitisiz ishlaydi — bu faqat Claude Artifacts muhitida ishlaydi. Mustaqil hostingda (GitHub Pages va h.k.) bu funksiya ishlamaydi; agar tarjima kerak bo'lsa, buni serverga (`bot.py`) ko'chirib, Anthropic API kalitini Railway environment variable sifatida saqlash tavsiya etiladi.
