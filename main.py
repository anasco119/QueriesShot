import os
import sqlite3
import logging
import time
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
import google.generativeai as genai
import pytz  # إضافة مكتبة pytz لضبط التوقيت

# تهيئة التسجيل (logging)
logging.basicConfig(level=logging.INFO)

# قراءة المتغيرات من البيئة
TOKEN = os.getenv("FAQBOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ALLOWED_GROUP_ID = int(os.getenv("ALLOWED_GROUP_ID"))  # معرف المجموعة المسموح بها
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID"))  # معرف المستخدم المشرف (أنت)
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # عنوان URL الخاص بالويبهوك

# تهيئة Gemini API
try:
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel('gemini-2.0-flash')
    logging.info("✅ تم تهيئة Gemini API بنجاح!")
except Exception as e:
    logging.error(f"❌ خطأ في تهيئة Gemini API: {e}")

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
    conn.commit()
    logging.info("✅ تم إنشاء الجداول بنجاح!")
except Exception as e:
    logging.error(f"❌ خطأ في إنشاء الجداول: {e}")

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
        data = cur.fetchall()
        logging.info(f"✅ تم استخراج {len(data)} سؤالًا من قاعدة البيانات.")
        return data
    except Exception as e:
        logging.error(f"❌ خطأ في استخراج البيانات من قاعدة البيانات: {e}")
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
WORKING_HOURS_START = 8  # 8 صباحًا
WORKING_HOURS_END = 19   # 7 مساءً

# دالة للتحقق من ساعات العمل
def is_within_working_hours():
    now = datetime.now(pytz.timezone('Africa/Khartoum'))
    logging.info(f"الوقت الحالي (بتوقيت السودان): {now.hour}:{now.minute}")
    return WORKING_HOURS_START <= now.hour < WORKING_HOURS_END

# دالة لمعالجة الرسائل
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.message.from_user.id
        chat_id = update.message.chat_id
        message = update.message.text

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
# دالة لمعالجة الرسائل
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.message.from_user.id
        chat_id = update.message.chat_id
        message = update.message.text

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
                    prompt += "أجب على استفسار المستخدم بناءً على قاعدة البيانات إذا كان السؤال متعلقًا بها. إذا لم يكن السؤال موجودًا في قاعدة البيانات، قم بالإجابة بشكل عام كمعلم لغة إنجليزية محترف. أضف في نهاية الرد جملة تحفيزية لتشجيع الطلاب على متابعة القناة، ثم أضف طلبًا لتقييم الخدمة إذا رأيت أن ذلك مناسبًا. حافظ على الرسالة قصيرة إن أمكن وجميلة بصريًا باستخدام الإيموجي وغيرها. إذا سُئلت عن اسمك، أجب برد مختصر بأن اسمك هو بوت QueriesShot للإجابة عن الأسئلة الشائعة."

                    response = generate_gemini_response(prompt)
                    await update.message.reply_text(response)

            # إذا كان المستخدم عاديًا في الخاص
            else:
                # تطبيق نفس القيود المطبقة في المجموعة
                if not is_within_working_hours():
                    await update.message.reply_text("عذرًا، البوت يعمل فقط من الساعة 8 صباحًا حتى 7 مساءً بتوقيت السودان.")
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
                prompt += "أجب على استفسار المستخدم بناءً على قاعدة البيانات إذا كان السؤال متعلقًا بها. إذا لم يكن السؤال موجودًا في قاعدة البيانات، قم بالإجابة بشكل عام كمعلم لغة إنجليزية محترف. أضف في نهاية الرد جملة تحفيزية لتشجيع الطلاب على متابعة القناة، ثم أضف طلبًا لتقييم الخدمة إذا رأيت أن ذلك مناسبًا. حافظ على الرسالة قصيرة إن أمكن وجميلة بصريًا باستخدام الإيموجي وغيرها. إذا سُئلت عن اسمك، أجب برد مختصر بأن اسمك هو بوت QueriesShot للإجابة عن الأسئلة الشائعة."

                response = generate_gemini_response(prompt)
                await update.message.reply_text(response)

        # إذا كانت الرسالة في المجموعة
        elif chat_id == ALLOWED_GROUP_ID:
            # تطبيق القيود المطبقة في المجموعة
            if not is_within_working_hours():
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
            prompt += "أجب على استفسار المستخدم بناءً على قاعدة البيانات إذا كان السؤال متعلقًا بها. إذا لم يكن السؤال موجودًا في قاعدة البيانات، قم بالإجابة بشكل عام كمعلم لغة إنجليزية محترف. أضف في نهاية الرد جملة تحفيزية لتشجيع الطلاب على متابعة القناة، ثم أضف طلبًا لتقييم الخدمة إذا رأيت أن ذلك مناسبًا. حافظ على الرسالة قصيرة إن أمكن وجميلة بصريًا باستخدام الإيموجي وغيرها. إذا سُئلت عن اسمك، أجب برد مختصر بأن اسمك هو بوت QueriesShot للإجابة عن الأسئلة الشائعة."

            response = generate_gemini_response(prompt)
            await update.message.reply_text(response)

    except Exception as e:
        logging.error(f"❌ خطأ في معالجة الرسالة: {e}")
        await update.message.reply_text("عذرًا، حدث خطأ أثناء معالجة سؤالك. يرجى المحاولة لاحقًا.")


# إنشاء البوت
app = ApplicationBuilder().token(TOKEN).build()

# إضافة المعالجات
app.add_handler(MessageHandler(filters.TEXT, handle_message))  # تمت إزالة ~filters.COMMAND

# دالة رئيسية لتشغيل البوت
def main():
    logging.info("✅ البوت يعمل الآن...")
    PORT = int(os.environ.get("PORT", 8080))  # الحصول على المنفذ من البيئة أو استخدام 8080 افتراضيًا
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TOKEN,
        webhook_url=f"{WEBHOOK_URL}/{TOKEN}"  # تعيين عنوان الويب هوك
    )

if __name__ == "__main__":
    main()
