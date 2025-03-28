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
user_violations = {}  # لتتبع عدد مخالفات كل مستخدم
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
                        "إذا سُئلت عن اسمك، أجب باختصار بأنك QueriesShot، بوت متخصص في الإجابة عن الأسئلة الشائعة في تعلم اللغة الإنجليزية."
)

                    response = generate_gemini_response(prompt)
                    await update.message.reply_text(response)

            # إذا كان المستخدم عاديًا في الخاص
            else:
                # التحقق من ساعات العمل
                if not is_within_working_hours():
                    await update.message.reply_text(
                        "عذرًا، البوت يعمل فقط من الساعة 8 صباحًا حتى 7 مساءً بتوقيت السودان.\n"
                        "Sorry, the bot operates only from 8 AM to 7 PM Sudan time."
                    )
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
                    "لتجنب الإزعاج، لا تُضمّن جملة تحفيزية أو طلب تقييم إلا إذا كان ذلك مناسبًا في سياق الرد. ")
                               
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
                return

            user_message_count[user_id] += 1

                # تحقق من نية الرسالة أولاً
            intent_prompt = f"""حدد نية الرسالة التالية من الخيارات التالية فقط:
                1. إستفسار
                2. ذو علاقة بدراسة باللغة الانجليزية
                3. خطأ إملائي وغرامر
                4. مخالفة، سلوك غير لائق أو ترويج ومضايقة
                5. أخرى (غير ذات صلة)

                الرسالة: "{message}"

                الرد يجب أن يكون رقمًا فقط بين 1 و5."""
        
            intent = generate_gemini_response(intent_prompt).strip()

        # معالجة حسب النية
            if intent in ["1", "2", "3"]:  # استفسار أو دراسة أو تصحيح
                faq_data = get_faq_data()
                prompt = "أنت معلم لغة إنجليزية محترف. لديك قاعدة بيانات تحتوي على الأسئلة والأجوبة التالية:\n\n"
            
                for q, a in faq_data:
                    prompt += f"س: {q}\nج: {a}\n\n"
            
                prompt += f"استفسار المستخدم: {message}\n\n"
            
                if intent == "1":  # استفسار عام
                    prompt += "أجب على استفسار المستخدم استنادًا إلى قاعدة البيانات إذا كان مرتبطًا بها."
                elif intent == "2":  # دراسة باللغة الإنجليزية
                    prompt += " الرسالة متعلقة بدراسة اللغة الإنجليزية. قدم إجابة مفصلة ومنظمة و قصيرة."
                elif intent == "3":  # تصحيح أخطاء
                    prompt += " الرسالة تحتوي على أخطاء إملائية أو نحوية. قم بتصحيح الأخطاء أولاً ثم اشرحها بطريقة تعليمية لكن في رسالة قصيرة مع إيموجي مناسب و محسنة بصريا بالسطور ، راعي ان الرسالة لاعضاء مجموعة لذلك أبدأ الرسالة بإسلوب لائق لتجنب أي ازعاج للمستخدم."
            
                    prompt += (" أجب بأسلوب أستاذ لغة إنجليزية محترف، مع الحرص على الوضوح والإيجاز. "
                    "اجعل الرد جذابًا بصريًا باستخدام الإيموجي عند الحاجة، دون مبالغة. "
                    "لتجنب الإزعاج، لا تُضمّن جملة تحفيزية أو طلب تقييم إلا إذا كان ذلك مناسبًا في سياق الرد.")
            
                    response = generate_gemini_response(prompt)
                    await update.message.reply_text(response)
            
        elif intent == "4":  # مخالفة أو سلوك غير لائق
            # حذف الرسالة وإرسال تحذير
            try:
                    await update.message.delete()
                    warning_msg = ("⚠️ تم حذف الرسالة بسبب مخالفتها لقواعد المجموعة. "
                            "نرحب بالأسئلة والمناقشات المتعلقة بتعلم اللغة الإنجليزية، "
                            "ولكن نرفض السلوك غير اللائق أو المضايقة. يرجى مراجعة قواعد المجموعة.")
                    sent_warning = await context.bot.send_message(
                    chat_id=chat_id,
                    text=warning_msg,
                    reply_to_message_id=update.message.message_id
                )
                
                # حذف رسالة التحذير بعد 10 ثوانٍ
                    await asyncio.sleep(10)
                    await sent_warning.delete()
                
                # كتم العضو لمدة 10 دقائق إذا تكرر منه ذلك
                    if user_id in user_violations:
                        user_violations[user_id] += 1
                    if user_violations[user_id] >= 3:  # بعد 3 مخالفات
                        from datetime import datetime, timedelta
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
                            text=f"تم كتم العضو لمدة 10 دقائق بسبب تكرار المخالفات."
                        )
                        await asyncio.sleep(10)
                        await mute_notification.delete()
                    else:
                      user_violations[user_id] = 1
            except Exception as e:
                logging.error(f"خطأ في معالجة المخالفة: {e}")
                
        # النية "5" أو أي قيمة أخرى يتم تجاهلها

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
