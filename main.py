import os
import sqlite3
import shutil
import logging
import uuid
import time
import asyncio
from datetime import datetime, timedelta
from telegram import ChatPermissions
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    CommandHandler,  # أضف هذا
    filters,
    ContextTypes
)
import google.generativeai as genai
import pytz  # إضافة مكتبة pytz لضبط التوقيت
from flask import Flask, request, jsonify

# تهيئة التسجيل (logging)
logging.basicConfig(level=logging.INFO)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


# قراءة المتغيرات من البيئة
TOKEN = os.getenv("FAQBOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ALLOWED_GROUP_ID = int(os.getenv("ALLOWED_GROUP_ID"))  # معرف المجموعة المسموح بها
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID"))  # معرف المستخدم المشرف (أنت)
CHANNEL_ID = os.getenv("CHANNEL_ID")
WEBHOOK_URL = os.getenv("WEBHOOK_URL") # عنوان URL الخاص بالويبهوك
user_violations = {}  # لتتبع عدد مخالفات كل مستخدم
# تهيئة Gemini API
try:
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel('gemini-2.0-flash')
    logging.info("✅ تم تهيئة Gemini API بنجاح!")
except Exception as e:
    logging.error(f"❌ خطأ في تهيئة Gemini API: {e}")
    
app = Flask(__name__)

# نقطة نهاية أساسية للتحقق من عمل الخادم
@app.route('/')
def home():
    return "✅ البوت يعمل بشكل صحيح!", 200
    


# إنشاء اتصال بقاعدة البيانات
try:
    conn = sqlite3.connect('faq.db', check_same_thread=False)  # إضافة check_same_thread=False
    cur = conn.cursor()
    logging.info("✅ تم الاتصال بقاعدة البيانات بنجاح!")
except Exception as e:
    logging.error(f"❌ خطأ في الاتصال بقاعدة البيانات: {e}")

# إنشاء الجداول إذا لم تكن موجودة
try:
    cur.execute('''CREATE TABLE IF NOT EXISTS faq (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    question TEXT,
                    answer TEXT,
                    category TEXT
                )''')
    cur.execute('''CREATE TABLE IF NOT EXISTS channel_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id INTEGER,
                    chat_id INTEGER,
                    text TEXT
                )''')
    conn.commit()
    logging.info("✅ تم إنشاء الجداول بنجاح!")
except Exception as e:
    logging.error(f"❌ خطأ في إنشاء الجداول: {e}")

def get_user_name(update):
    user = update.message.from_user
    if user.first_name and user.last_name:
        return f"{user.first_name} {user.last_name}"
    elif user.first_name:
        return user.first_name
    elif user.username:
        return f"@{user.username}"
    else:
        return "عزيزي الطالب"  # قيمة افتراضية إذا لم يوجد اسم
        
def get_recent_channel_messages():
    """ استرجاع آخر 5 رسائل مخزنة من القناة """
    try:
        cur.execute("SELECT text FROM channel_messages ORDER BY id DESC LIMIT 5")
        messages = cur.fetchall()
        return [msg[0] for msg in messages]  # إرجاع الرسائل كنصوص
    except Exception as e:
        logging.error(f"❌ خطأ في جلب الرسائل المخزنة من القناة: {e}")
        return []
# دالة لإضافة استفسار جديد
def add_faq(question, answer, category):
    try:
        cur.execute("INSERT INTO faq (question, answer, category) VALUES (?, ?, ?)", 
                    (question, answer, category))
        conn.commit()
        logging.info(f"✅ تم إضافة استفسار جديد: {question}")
        return True  # إرجاع True إذا تمت الإضافة بنجاح
    except Exception as e:
        logging.error(f"❌ خطأ في إضافة استفسار جديد: {e}")
        return False  # إرجاع False إذا حدث خطأ

async def store_channel_message(update: Update):
    """تخزين الرسالة فقط إذا كانت من القناة الرسمية"""
    try:
        message_id = update.message.message_id
        chat_id = update.message.chat_id
        text = update.message.text

        # 🔹 التحقق من أن الرسالة قادمة من القناة الرسمية
        if str(chat_id) == os.getenv("CHANNEL_ID"):
            cur.execute("INSERT INTO channel_messages (message_id, chat_id, text) VALUES (?, ?, ?)",
                        (message_id, chat_id, text))

            cur.execute("SELECT COUNT(*) FROM channel_messages")
            count = cur.fetchone()[0]

        if count > 5:
            # حذف أقدم الرسائل للحفاظ على العدد عند 10 فقط
            cur.execute("DELETE FROM channel_messages WHERE id IN (SELECT id FROM channel_messages ORDER BY id ASC LIMIT ?)", (count - 5,))
  
            conn.commit()
            logging.info(f"✅ تم تخزين رسالة من القناة: {text}")
        else:
            logging.info(f"⚠️ تم تجاهل رسالة لأنها ليست من القناة الرسمية. (Chat ID: {chat_id})")

    except Exception as e:
        logging.error(f"❌ خطأ في تخزين رسالة القناة: {e}")

# دالة لحذف استفسار برقمه
def delete_faq(faq_id):
    try:
        cur.execute("DELETE FROM faq WHERE id = ?", (faq_id,))
        conn.commit()
        logging.info(f"✅ تم حذف الاستفسار برقم: {faq_id}")
        return cur.rowcount > 0  # True إذا تم الحذف بنجاح
    except Exception as e:
        logging.error(f"❌ خطأ في حذف الاستفسار: {e}")
        return False

# دالة للحصول على جميع الأسئلة والأجوبة
def get_faq_data():
        try:
            cur.execute("SELECT question, answer FROM faq")
            faq_entries = cur.fetchall()  # تعديل التسمية من `data` إلى `faq_entries`
            logging.info(f"✅ تم استخراج {len(faq_entries)} سؤالًا من قاعدة البيانات.")

            cur.execute("SELECT text FROM channel_messages")
            channel_entries = [(text, "معلومة من القناة") for (text,) in cur.fetchall()]

            return faq_entries + channel_entries  # ✅ الآن `faq_entries` معرف
        except Exception as e:
            logging.error(f"❌ خطأ في جلب بيانات الأسئلة: {e}")
            return []


# دالة لإنشاء رد من Gemini
def generate_gemini_response(prompt):
    try:
        response = model.generate_content(prompt)
        if response.text:
            logging.info("✅ تم استقبال رد من Gemini بنجاح!")
            return response.text
        else:
            logging.error("❌ لم يتم استقبال نص من Gemini.")
            return "عذرًا، لم أتمكن من معالجة سؤالك. يرجى المحاولة لاحقًا."
    except Exception as e:
        logging.error(f"❌ خطأ في generate_gemini_response: {e}")
        return f"عذرًا، حدث خطأ أثناء معالجة سؤالك: {str(e)}"

# تهيئة المتغيرات العامة
user_message_count = {}  # لتتبع عدد الرسائل لكل مستخدم
last_reset_date = datetime.now(pytz.timezone('Africa/Khartoum')).date()  # تاريخ آخر إعادة تعيين

# دالة لإعادة تعيين عدد الرسائل يوميًا
def reset_message_count():
    global user_message_count, last_reset_date
    today = datetime.now(pytz.timezone('Africa/Khartoum')).date()
    if today > last_reset_date:
        user_message_count = {}  # إعادة تعيين عدد الرسائل
        last_reset_date = today  # تحديث تاريخ آخر إعادة تعيين
        logging.info("✅ تم إعادة تعيين عدد الرسائل للمستخدمين.")

# ساعات عمل البوت (بتوقيت السودان)
WORKING_HOURS_START = 6  # 8 صباحًا
WORKING_HOURS_END = 24   # 12 صباحًا

# دالة للتحقق من ساعات العمل
def is_within_working_hours():
    now = datetime.now(pytz.timezone('Africa/Khartoum'))
    logging.info(f"الوقت الحالي (بتوقيت السودان): {now.hour}:{now.minute}")
    return WORKING_HOURS_START <= now.hour < WORKING_HOURS_END

# دالة لمعالجة الرسائل
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message:
            print("⚠️ [LOG] - لا توجد رسالة في التحديث. قد يكون السبب أنه تحديث غير نصي.")
            return
        user_id = update.message.from_user.id
        chat_id = update.message.chat_id
        message = update.message.text
        print(f"🔍 [LOG] - استلمنا رسالة من المستخدم {user_id} في المجموعة {chat_id}: {message}")

        logging.info(f"تم استقبال رسالة من المستخدم: {user_id} في الدردشة: {chat_id}")

        # إذا كانت الرسالة في الخاص
        if chat_id == user_id:
            # إذا كان المستخدم هو المشرف (أنت)
            if user_id == ADMIN_USER_ID:
                # معالجة أمر الإضافة
                if message.startswith("/addfaq"):
                    try:
                        parts = [part.strip() for part in message.split("|")]
                        if len(parts) >= 3:
                            question = parts[0].replace("/addfaq", "").strip()
                            answer = parts[1].strip()
                            category = parts[2].strip()

                            if add_faq(question, answer, category):
                                await update.message.reply_text("✅ تم إضافة الاستفسار بنجاح!")
                            else:
                                await update.message.reply_text("❌ فشل في إضافة الاستفسار.")
                        else:
                            await update.message.reply_text("❌ صيغة غير صحيحة. استخدم: /addfaq سؤال | جواب | فئة")
                    except Exception as e:
                        logging.error(f"خطأ في إضافة الاستفسار: {e}")
                        await update.message.reply_text("❌ حدث خطأ أثناء الإضافة.")

                # معالجة أمر الحذف
                elif message.startswith("/deletefaq"):
                    try:
                        faq_id = message.replace("/deletefaq", "").strip()
                        if faq_id.isdigit():
                            if delete_faq(int(faq_id)):
                                await update.message.reply_text(f"✅ تم حذف الاستفسار رقم {faq_id}.")
                            else:
                                await update.message.reply_text(f"❌ لا يوجد استفسار بالرقم {faq_id}.")
                        else:
                            await update.message.reply_text("❌ الرقم غير صالح. مثال: /deletefaq 1")
                    except Exception as e:
                        logging.error(f"خطأ في حذف الاستفسار: {e}")
                        await update.message.reply_text("❌ حدث خطأ أثناء الحذف.")

                # إذا كانت رسالة عادية من المشرف
                else:
                    # يمكنك إرسال أي رسالة في الخاص وسيقوم البوت بالرد عليها
                    faq_data = get_faq_data()
                    prompt = "أنت معلم لغة إنجليزية محترف. لديك قاعدة بيانات تحتوي على الأسئلة والأجوبة التالية:\n\n"
                    for q, a in faq_data:
                        prompt += f"س: {q}\nج: {a}\n\n"
                        prompt += f"استفسار المستخدم: {message}\n\n"
                        prompt += ("أجب على استفسار المستخدم استنادًا إلى قاعدة البيانات إذا كان مرتبطًا بها. "
                        "في حال عدم العثور على إجابة مباشرة، قدّم ردًا عامًا بأسلوب أستاذ لغة إنجليزية محترف، مع الحرص على الوضوح والإيجاز. "
                        "اجعل الرد جذابًا بصريًا باستخدام الإيموجي عند الحاجة، دون مبالغة. "
                        "لتجنب الإزعاج، لا تُضمّن جملة تحفيزية أو طلب تقييم إلا إذا كان ذلك مناسبًا في سياق الرد. "
                        "إذا سُئلت عن اسمك، أجب باختصار بأنك QueriesShot، بوت متخصص في الإجابة عن الأسئلة الشائعة في تعلم اللغة الإنجليزية. و أجب بنفس لغة رسالة الإستفسار"
)

                    response = generate_gemini_response(prompt)
                    await update.message.reply_text(response, parse_mode='Markdown')

            # إذا كان المستخدم عاديًا في الخاص
            else:
                # التحقق من ساعات العمل
                if not is_within_working_hours():
                    await update.message.reply_text(
                        "*عذرًا، البوت يعمل فقط من الساعة 8 صباحًا حتى 7 مساءً بتوقيت السودان*.\n"
                        "*Sorry, the bot operates only from 8 AM to 7 PM Sudan time.*", parse_mode='Markdown', disable_web_page_preview=True)
                    return  # تجاهل الرسالة خارج ساعات العمل

                reset_message_count()

                if user_id not in user_message_count:
                    user_message_count[user_id] = 0

                if user_message_count[user_id] >= 10:
                    await update.message.reply_text("عذرًا، لقد تجاوزت الحد المسموح به من الرسائل اليومية.")
                    return

                user_message_count[user_id] += 1

                faq_data = get_faq_data()
                prompt = "أنت معلم لغة إنجليزية محترف. لديك قاعدة بيانات تحتوي على الأسئلة والأجوبة التالية:\n\n"
                for q, a in faq_data:
                    prompt += f"س: {q}\nج: {a}\n\n"
                    prompt += f"استفسار المستخدم: {message}\n\n"
                    prompt += ("أجب على استفسار المستخدم استنادًا إلى قاعدة البيانات إذا كان مرتبطًا بها. "
                    "في حال عدم العثور على إجابة مباشرة، قدّم ردًا عامًا بأسلوب أستاذ لغة إنجليزية محترف، مع الحرص على الوضوح والإيجاز. "
                    "اجعل الرد جذابًا بصريًا باستخدام الإيموجي عند الحاجة، دون مبالغة. "
                    "لتجنب الإزعاج، لا تُضمّن جملة تحفيزية أو طلب تقييم إلا إذا كان ذلك مناسبًا في سياق الرد. و أجب بنفس لغة رسالة الإستفسار ")

                # إذا كانت رسالة عادية من المشرف
                else:
                # يمكنك إرسال أي رسالة في الخاص وسيقوم البوت بالرد عليها
                    faq_data = get_faq_data()
                prompt = "أنت معلم لغة إنجليزية محترف. لديك قاعدة بيانات تحتوي على الأسئلة والأجوبة التالية:\n\n"
                for q, a in faq_data:
                    prompt += f"س: {q}\nج: {a}\n\n"
                prompt += f"استفسار المستخدم: {message}\n\n"
                prompt += ("أجب على استفسار المستخدم استنادًا إلى قاعدة البيانات إذا كان مرتبطًا بها. "
                "في حال عدم العثور على إجابة مباشرة، قدّم ردًا عامًا بأسلوب أستاذ لغة إنجليزية محترف، مع الحرص على الوضوح والإيجاز. "
                "اجعل الرد جذابًا بصريًا باستخدام الإيموجي عند الحاجة، دون مبالغة. "
                "لتجنب الإزعاج، لا تُضمّن جملة تحفيزية أو طلب تقييم إلا إذا كان ذلك مناسبًا في سياق الرد. "
                "إذا سُئلت عن اسمك، أجب باختصار بأنك QueriesShot، بوت متخصص في الإجابة عن الأسئلة الشائعة في تعلم اللغة الإنجليزية.")

                response = generate_gemini_response(prompt)
                await update.message.reply_text(response, parse_mode='Markdown')

        # إذا كانت الرسالة في المجموعة
        elif chat_id == ALLOWED_GROUP_ID:
        # تطبيق القيود المطبقة في المجموعة
            if not is_within_working_hours():
                return  # تجاهل الرسالة خارج ساعات العمل

            reset_message_count()

            if user_id not in user_message_count:
                user_message_count[user_id] = 0

            if user_message_count[user_id] >= 10:
                return

            user_message_count[user_id] += 1

                # تحقق من نية الرسالة أولاً
            intent_prompt = f"""حدد نية الرسالة التالية من الخيارات التالية فقط:
                1. إستفسار أو أي مسألة تتعلق بالقناة أو أي طلب مساعدة ذو علاقة بالتعلم
                2.  قطعة قصيرة او طويلة بالانجليزية 
                3. خطأ إملائي وغرامر
                4. مخالفة، سلوك غير لائق أو ترويج ومضايقة أو كلمات بذئية أو رسائل spam 
                5. أخرى (غير ذات صلة) أي خارج سياق القناة و هي قناة تعلم الانجليزية
                

                الرسالة: "{message}"

                الرد يجب أن يكون رقمًا فقط بين 1 و5."""

            intent = generate_gemini_response(intent_prompt).strip()
            intent = str(int(intent))
            logging.info(f"🔍 [LOG] - النية المستلمة من Gemini: {intent}")
            print(f"🔍 [LOG] - النية المستلمة من Gemini: {intent}")  # طباعة في الكونسول


        # معالجة حسب النية
            prompt = ""
            if intent == "1":  # استفسار عام
                faq_data = get_faq_data()
                prompt = "أنت معلم لغة إنجليزية محترف. لديك قاعدة بيانات تحتوي على الأسئلة والأجوبة التالية:\n\n"
                for q, a in faq_data:
                    prompt += f"س: {q}\nج: {a}\n\n"
                prompt += f' أجب على استفسار المستخدم استنادًا إلى قاعدة البيانات إذا كان مرتبطًا بها. يجب أن يكون الرد بنفس لغة الاستفسار. الرسالة الأصلية = "{message}"'
                    
                
                response = generate_gemini_response(prompt)
                await update.message.reply_text(response, parse_mode='Markdown')
            elif intent == "2":  # دراسة باللغة الإنجليزية
                recent_messages = get_recent_channel_messages()
                if recent_messages:
                    prompt += "🔹 إليك بعض الدروس الحديثة من القناة:\n"
                for msg in recent_messages:
                    prompt += f"📌 {msg}\n"
                prompt = f"""أنت مدرس لغة إنجليزية محترف. استلم قطعة مكتوبة من الطالب {user_name} ، وهدفك:
✅ قدم تقييمًا عامًّا لمستوى اللغة.
✍️ راجع النص وعدّل العبارات غير الطبيعية لتكون أكثر فصاحة وشيوعًا.
💬 استخدم تعبيرات شائعة تجعل النص يبدو طبيعياً وكأنه مكتوب من متحدث أصلي.
📄 قدّم النص المحسّن بشكل منظم، مع فصل كل قسم بمسافات واضحة.
📌 اجعل التنسيق مريحًا بصريًا باستخدام الإيموجي عند الحاجة.
⏱️ لا تجعل الرد طويلاً. كن واضحًا، مختصرًا، وفعّالًا.

الرسالة الأصلية = "{message}"
"""

                response = generate_gemini_response(prompt)
                await update.message.reply_text(response, parse_mode='Markdown')
    
    
            elif intent == "3":  # تصحيح أخطاء
                user_name = get_user_name(update)
                prompt = f"""الرسالة تحتوي على أخطاء إملائية أو نحوية. و هي من الطالب {user_name} أذكر أسمه في البداية في كلمات قصيرة و برفق لتبدو الرسالة من استاذ الى تلميذ لينتبه إلى الرسالة  
✅ قم بتصحيح الأخطاء أولاً، ثم قدّم شرحًا تعليمياً بسيطًا في رسالة قصيرة.  
📌 استخدم إيموجي مناسب لتحسين وضوح التصحيح.  
📄 نسّق الرسالة بفواصل وسطور لجعلها سهلة القراءة.  
🎯 الهدف هو مساعدة المستخدم دون إزعاجه، لذا استخدم أسلوبًا لبقًا ومشجعًا في البداية.  
✍️ مثال على التنسيق المطلوب:  

🔹 *خطأ*: [الجملة الأصلية]  
✅ *التصحيح*: [الجملة المصححة]
💡 _لماذا_: [شرح قصير و مباشر]

📌 اجعل الأسلوب وديًا واحترافيًا، وكأنك مدرس لطيف يساعد الطلاب دون إشعارهم بالحرج و لا تستخدم نص عريض باي شكل من الاشكال و باللغة الانجليزية. الرسالة الأصلية = "{message}" 

أجب بأسلوب أستاذ لغة إنجليزية محترف، مع الحرص على الوضوح والإيجاز. 
اجعل الرد جذابًا بصريًا باستخدام الإيموجي عند الحاجة، دون مبالغة. 
لتجنب الإزعاج، لا تُضمّن جملة تحفيزية أو طلب تقييم إلا إذا كان ذلك مناسبًا في سياق الرد."""

                response = generate_gemini_response(prompt)
                await update.message.reply_text(response, parse_mode='Markdown')
            elif intent.strip() == "4":  # يحذف كل الفراغات والأحرف الخفية
            # مخالفة أو سلوك غير لائق
                logging.info("🚨 [LOG] - دخلنا في جزء المخالفات.")
                print(f"قيمة intent: '{intent}'، نوعها: {type(intent)}، طولها: {len(intent)}")
                logging.info(f"قيمة intent: '{intent}'، نوعها: {type(intent)}")

                try:

                    logging.info("🗑️ [LOG] - تم حذف الرسالة المخالفة بنجاح.")
                    print("🗑️ [LOG] - تم حذف الرسالة المخالفة بنجاح.")

                    warning_msg = ("⚠️ تم حذف الرسالة بسبب مخالفتها لقواعد المجموعة. "
                           "نرحب بالأسئلة والمناقشات المتعلقة بتعلم اللغة الإنجليزية، "
                           "ولكن نرفض السلوك غير اللائق أو المضايقة. يرجى مراجعة قواعد المجموعة.")

                    sent_warning = await context.bot.send_message(
                        chat_id=chat_id,
                        text=warning_msg,
                        reply_to_message_id=update.message.message_id
                        )
                    await update.message.delete()
                    logging.info("⚠️ [LOG] - تم إرسال رسالة تحذيرية للمستخدم.")
                    print("⚠️ [LOG] - تم إرسال رسالة تحذيرية للمستخدم.")

            # حذف رسالة التحذير بعد 10 ثوانٍ
                    await asyncio.sleep(10)
                    await sent_warning.delete()
                    logging.info("🗑️ [LOG] - تم حذف رسالة التحذير بعد 10 ثوانٍ.")
                    print("🗑️ [LOG] - تم حذف رسالة التحذير بعد 10 ثوانٍ.")

            # كتم العضو لمدة 10 دقائق إذا تكررت المخالفة
                    if user_id in user_violations:
                        user_violations[user_id] += 1
                    else:
                        user_violations[user_id] = 1

                    logging.info(f"📊 [LOG] - عدد مخالفات المستخدم {user_id}: {user_violations[user_id]}")
                    print(f"📊 [LOG] - عدد مخالفات المستخدم {user_id}: {user_violations[user_id]}")

                    if user_violations[user_id] >= 3:  # بعد 3 مخالفات
                        mute_duration = timedelta(minutes=10)
                        mute_until = datetime.now() + mute_duration

                        await context.bot.restrict_chat_member(
                            chat_id=chat_id,
                            user_id=user_id,
                            until_date=mute_until,
                            permissions=ChatPermissions(
                                can_send_messages=False,
                                can_send_media_messages=False,
                                can_send_polls=False,
                                can_send_other_messages=False,
                                can_add_web_page_previews=False
                                )
                            )


                    mute_notification = await context.bot.send_message(
                        chat_id=chat_id,
                        text=f"🔇 تم كتم العضو لمدة 10 دقائق بسبب تكرار المخالفات."
                        )

                    await asyncio.sleep(10)
                    await mute_notification.delete()
                    logging.info("🗑️ [LOG] - تم حذف رسالة إشعار الكتم.")
                    print("🗑️ [LOG] - تم حذف رسالة إشعار الكتم.")


                except Exception as e:
                    logging.error(f"❌ [LOG] - خطأ عام أثناء معالجة النية: {e}")
                    print(f"❌ [LOG] - خطأ عام أثناء معالجة النية: {e}")
            else:
                logging.info(f"❓ [LOG] - النية غير معروفة: {intent}")
                print(f"❓ [LOG] - النية غير معروفة: {intent}")



        # النية "5" أو أي قيمة أخرى يتم تجاهلها

    except Exception as e:
        logging.error(f"❌ خطأ في معالجة الرسالة: {e}")
        await update.message.reply_text("عذرًا، حدث خطأ أثناء معالجة سؤالك. يرجى المحاولة لاحقًا.")



async def send_message(update: Update, text: str):
    """ دالة موحدة لإرسال الرسائل مع التحقق من وجود `message`. """
    if update.message:
        await update.message.reply_text(text)

async def is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """ التحقق مما إذا كان المستخدم مشرفًا. """
    return str(update.effective_user.id) in ADMIN_USER_ID

async def reset_database(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ دالة لإعادة تهيئة قاعدة البيانات بعد التأكيد. """
    if not await is_admin(update, context):
        await send_message(update, "⛔ هذا الأمر متاح للمشرفين فقط!")
        return

    confirmation_key = str(uuid.uuid4())[:8]
    context.user_data['db_confirmation'] = confirmation_key

    await send_message(
        update,
        f"⚠️ تحذير: هذا الأمر سيحذف جميع البيانات بشكل دائم!\n"
        f"للتأكيد، أرسل:\n"
        f"/confirm_reset {confirmation_key}"
    )

async def confirm_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ دالة تأكيد إعادة التعيين بعد التأكد من كود الحماية. """
    if not await is_admin(update, context):
        return

    try:
        if not context.args:
            await send_message(update, "❌ يجب إدخال كود التأكيد!")
            return

        confirmation_code = context.args[0]
        stored_code = context.user_data.get('db_confirmation')

        if not stored_code or confirmation_code != stored_code:
            await send_message(update, "❌ كود التأكيد غير صحيح!")
            return

        await send_message(update, "⌛ جاري حذف قاعدة البيانات...")
        db_path = 'faq.db'

        # حذف قاعدة البيانات إذا كانت موجودة
        if os.path.exists(db_path):
            os.remove(db_path)
            await asyncio.sleep(2)  # منح وقت للحذف

            # إعادة التهيئة
            initialize_database()  

            await send_message(update, "✅ تم إعادة تعيين قاعدة البيانات بنجاح!")
        else:
            await send_message(update, "ℹ️ لم يتم العثور على ملف قاعدة البيانات!")

    except Exception as e:
        logging.error(f"Database reset error: {e}")
        await send_message(update, "❌ فشل في إعادة تعيين قاعدة البيانات!")

# إنشاء البوت
app = ApplicationBuilder().token(TOKEN).build()
# إضافة المعالجات
app.add_handler(MessageHandler(filters.TEXT, handle_message))  # تمت إزالة ~filters.COMMAND
app.add_handler(CommandHandler("reset_db", reset_database))
app.add_handler(CommandHandler("confirm_reset", confirm_reset))

# دالة رئيسية لتشغيل البوت
def main():
    logging.info("✅ البوت يعمل الآن...")
    PORT = int(os.environ.get("PORT", 10000))  # الحصول على المنفذ من البيئة أو استخدام 8080 افتراضيًا
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TOKEN,
        webhook_url=f"{WEBHOOK_URL}/{TOKEN}"  # تعيين عنوان الويب هوك
        )
    
if __name__ == '__main__':
    main()
