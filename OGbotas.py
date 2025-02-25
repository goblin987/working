import telegram
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.triggers.cron import CronTrigger
import pytz
from collections import defaultdict, deque
from datetime import datetime, timedelta, time
import random
import logging
import asyncio
import pickle
import os
import sys

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
logger.info(f"Running on Python {sys.version}")

# Get sensitive information from environment variables
TOKEN = os.getenv('TELEGRAM_TOKEN')
ADMIN_CHAT_ID = os.getenv('ADMIN_CHAT_ID')
GROUP_CHAT_ID = os.getenv('GROUP_CHAT_ID')
PASSWORD = os.getenv('PASSWORD', 'shoebot123')  # Default password or fetch from env if needed

# Check if required environment variables are set
if not TOKEN:
    logger.error("TELEGRAM_TOKEN environment variable is not set.")
    sys.exit(1)
if not ADMIN_CHAT_ID:
    logger.error("ADMIN_CHAT_ID environment variable is not set.")
    sys.exit(1)
if not GROUP_CHAT_ID:
    logger.error("GROUP_CHAT_ID environment variable is not set.")
    sys.exit(1)

# Constants
TIMEZONE = pytz.timezone('Europe/Vilnius')
COINFLIP_STICKER_ID = 'CAACAgIAAxkBAAEN32tnuPb-ovynJR5WNO1TQyv_ea17DwAC-RkAAtswEEqAzfrZRd8B1zYE'

# Data loading and saving functions
def load_data(filename, default):
    try:
        if os.path.exists(filename) and os.path.getsize(filename) > 0:
            with open(filename, 'rb') as f:
                return pickle.load(f)
        return default
    except (FileNotFoundError, EOFError, pickle.UnpicklingError):
        return default

def save_data(data, filename):
    if isinstance(data, defaultdict):
        data = dict(data)
    try:
        with open(filename, 'wb') as f:
            pickle.dump(data, f)
        logger.info(f"Saved data to {filename}")
    except Exception as e:
        logger.error(f"Failed to save {filename}: {str(e)}")

# Load initial data
featured_media_id = load_data('featured_media_id.pkl', None)
featured_media_type = load_data('featured_media_type.pkl', None)
barygos_media_id = load_data('barygos_media_id.pkl', None)
barygos_media_type = load_data('barygos_media_type.pkl', None)

PARDAVEJAI_MESSAGE_FILE = 'pardavejai_message.pkl'
DEFAULT_PARDAVEJAI_MESSAGE = "Pasirink pardavėją, už kurį nori balsuoti iš žemiau esančių mygtukų:"
pardavejai_message = load_data(PARDAVEJAI_MESSAGE_FILE, DEFAULT_PARDAVEJAI_MESSAGE)
last_addftbaryga_message = None
last_addftbaryga2_message = None

def save_pardavejai_message():
    save_data(pardavejai_message, PARDAVEJAI_MESSAGE_FILE)

# Scheduler setup
scheduler = AsyncIOScheduler(timezone=TIMEZONE)
scheduler.add_executor(ThreadPoolExecutor(max_workers=10), alias='default')

async def configure_scheduler(application):
    logger.info("Configuring scheduler...")
    application.job_queue.scheduler = scheduler
    scheduler.start()
    logger.info("Scheduler started successfully.")

# Bot initialization
application = Application.builder().token(TOKEN).post_init(configure_scheduler).build()
logger.info("Bot initialized")

# Data structures
trusted_sellers = ['@Seller1', '@Seller2', '@Seller3']
votes_weekly = load_data('votes_weekly.pkl', defaultdict(int))
votes_monthly = load_data('votes_monthly.pkl', defaultdict(list))
votes_alltime = load_data('votes_alltime.pkl', defaultdict(int))
voters = set()
downvoters = set()
pending_downvotes = {}
approved_downvotes = {}
vote_history = load_data('vote_history.pkl', defaultdict(list))
last_vote_attempt = defaultdict(lambda: datetime.min.replace(tzinfo=TIMEZONE))
last_downvote_attempt = defaultdict(lambda: datetime.min.replace(tzinfo=TIMEZONE))
complaint_id = 0
user_points = load_data('user_points.pkl', defaultdict(int))
coinflip_challenges = {}
daily_messages = defaultdict(lambda: defaultdict(int))
weekly_messages = defaultdict(int)
alltime_messages = load_data('alltime_messages.pkl', defaultdict(int))
chat_streaks = load_data('chat_streaks.pkl', defaultdict(int))
last_chat_day_raw = load_data('last_chat_day.pkl', {})
last_chat_day = defaultdict(lambda: datetime.min.replace(tzinfo=TIMEZONE), last_chat_day_raw)
allowed_groups = {GROUP_CHAT_ID}
valid_licenses = {'LICENSE-XYZ123', 'LICENSE-ABC456'}
pending_activation = {}
username_to_id = {}
polls = {}

def is_allowed_group(chat_id: str) -> bool:
    return str(chat_id) in allowed_groups

# Command handlers
async def debug(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.message.from_user.id)
    if user_id != ADMIN_CHAT_ID:
        await update.message.reply_text("Tik adminas gali naudoti šią komandą!")
        return
    chat_id = update.message.chat_id
    try:
        admins = await context.bot.get_chat_administrators(chat_id)
        admin_list = "\n".join([f"@{m.user.username or m.user.id} (ID: {m.user.id})" for m in admins])
        await update.message.reply_text(f"Matomi adminai:\n{admin_list}")
    except telegram.error.TelegramError as e:
        await update.message.reply_text(f"Debug failed: {str(e)}")

async def whoami(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.message.from_user.id
    chat_id = update.message.chat_id
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        username = f"@{member.user.username}" if member.user.username else "No username"
        await update.message.reply_text(f"Jūs esate: {username} (ID: {user_id})")
    except telegram.error.TelegramError as e:
        await update.message.reply_text(f"Error: {str(e)}")

async def startas(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    if chat_id != user_id:
        if is_allowed_group(chat_id):
            await update.message.reply_text(
                "Sveiki! Use /balsuoju to vote for sellers with buttons. /nepatiko for downvotes (5 pts). "
                "Chat daily for 1-3 pts + streaks. Check /barygos, /chatking, /coinflip, or /apklausa!"
            )
        else:
            await update.message.reply_text("Šis botas skirtas tik mano grupėms! Siųsk /startas Password privačiai!")
    else:
        try:
            password = context.args[0]
            if password == PASSWORD:
                pending_activation[user_id] = "password"
                await update.message.reply_text("Slaptažodis teisingas! Siųsk /activate_group GroupChatID.")
            else:
                await update.message.reply_text("Neteisingas slaptažodis!")
        except IndexError:
            await update.message.reply_text("Naudok: /startas Password privačiai!")

async def activate_group(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.message.from_user.id
    if str(user_id) != ADMIN_CHAT_ID:
        await update.message.reply_text("Tik adminas gali aktyvuoti grupes!")
        return
    if user_id not in pending_activation:
        await update.message.reply_text("Pirma įvesk slaptažodį privačiai!")
        return
    try:
        group_id = context.args[0]
        if group_id in allowed_groups:
            await update.message.reply_text("Grupė jau aktyvuota!")
        else:
            allowed_groups.add(group_id)
            if pending_activation[user_id] != "password":
                valid_licenses.remove(pending_activation[user_id])
            del pending_activation[user_id]
            await update.message.reply_text(f"Grupė {group_id} aktyvuota! Use /startas in the group.")
    except IndexError:
        await update.message.reply_text("Naudok: /activate_group GroupChatID")

async def privatus(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.message.from_user.id)
    if user_id != ADMIN_CHAT_ID:
        await update.message.reply_text("Tik adminas gali naudoti šią komandą!")
        return
    chat_id = update.message.chat_id
    if not is_allowed_group(chat_id):
        await update.message.reply_text("Botas neveikia šioje grupėje!")
        return
    keyboard = [[InlineKeyboardButton("Valdyti privačiai", url=f"https://t.me/{context.bot.username}")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Spausk mygtuką, kad valdytum botą privačiai:", reply_markup=reply_markup)

async def start_private(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.message.from_user.id)
    chat_id = update.message.chat_id
    if chat_id == int(user_id) and user_id == ADMIN_CHAT_ID:
        keyboard = [
            [InlineKeyboardButton("Pridėti pardavėją", callback_data="admin_addseller")],
            [InlineKeyboardButton("Pašalinti pardavėją", callback_data="admin_removeseller")],
            [InlineKeyboardButton("Redaguoti /balsuoju tekstą", callback_data="admin_editpardavejai")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("Sveikas, admin! Ką nori valdyti?", reply_markup=reply_markup)

async def handle_admin_button(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = str(query.from_user.id)
    if user_id != ADMIN_CHAT_ID:
        await query.answer("Tik adminas gali tai daryti!")
        return
    chat_id = query.message.chat_id
    if chat_id != int(user_id):
        await query.answer("Šią komandą naudok privačiai!")
        return

    data = query.data
    if data == "admin_addseller":
        await query.edit_message_text("Įvesk: /addseller @VendorTag")
    elif data == "admin_removeseller":
        await query.edit_message_text("Įvesk: /removeseller @VendorTag")
    elif data == "admin_editpardavejai":
        await query.edit_message_text("Įvesk: /editpardavejai 'Naujas tekstas'")
    await query.answer()

async def balsuoju(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id

    if not is_allowed_group(chat_id):
        await update.message.reply_text("Botas neveikia šioje grupėje!")
        return

    keyboard = [[InlineKeyboardButton(seller, callback_data=f"vote_{seller}")] for seller in trusted_sellers]
    reply_markup = InlineKeyboardMarkup(keyboard)
    if 'featured_media_id' in globals() and featured_media_id and featured_media_type:
        if featured_media_type == 'photo':
            await context.bot.send_photo(chat_id=chat_id, photo=featured_media_id, caption=pardavejai_message, reply_markup=reply_markup)
        elif featured_media_type == 'animation':
            await context.bot.send_animation(chat_id=chat_id, animation=featured_media_id, caption=pardavejai_message, reply_markup=reply_markup)
        elif featured_media_type == 'video':
            await context.bot.send_video(chat_id=chat_id, video=featured_media_id, caption=pardavejai_message, reply_markup=reply_markup)
    else:
        await context.bot.send_message(chat_id=chat_id, text=pardavejai_message, reply_markup=reply_markup)
    logger.info(f"/balsuoju called by user_id={user_id} in chat_id={chat_id}, buttons sent to group.")

async def addftbaryga(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.message.from_user.id)
    chat_id = update.message.chat_id
    if user_id != ADMIN_CHAT_ID:
        await update.message.reply_text("Tik adminas gali pridėti media!")
        return
    if not update.message.reply_to_message:
        await update.message.reply_text("Atsakyk į žinutę su paveikslėliu, GIF ar video!")
        return
    
    global featured_media_id, featured_media_type, last_addftbaryga_message
    reply = update.message.reply_to_message
    if reply.photo:
        media = reply.photo[-1]
        featured_media_id = media.file_id
        featured_media_type = 'photo'
        last_addftbaryga_message = "Paveikslėlis pridėtas prie /balsuoju!"
    elif reply.animation:
        media = reply.animation
        featured_media_id = media.file_id
        featured_media_type = 'animation'
        last_addftbaryga_message = "GIF pridėtas prie /balsuoju!"
    elif reply.video:
        media = reply.video
        featured_media_id = media.file_id
        featured_media_type = 'video'
        last_addftbaryga_message = "Video pridėtas prie /balsuoju!"
    else:
        await update.message.reply_text("Atsakyk į žinutę su paveikslėliu, GIF ar video!")
        return
    
    save_data(featured_media_id, 'featured_media_id.pkl')
    save_data(featured_media_type, 'featured_media_type.pkl')
    await update.message.reply_text(last_addftbaryga_message)

async def addftbaryga2(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.message.from_user.id)
    chat_id = update.message.chat_id
    if user_id != ADMIN_CHAT_ID:
        await update.message.reply_text("Tik adminas gali pridėti media!")
        return
    if not update.message.reply_to_message:
        await update.message.reply_text("Atsakyk į žinutę su paveikslėliu, GIF ar video!")
        return
    
    global barygos_media_id, barygos_media_type, last_addftbaryga2_message
    reply = update.message.reply_to_message
    if reply.photo:
        media = reply.photo[-1]
        barygos_media_id = media.file_id
        barygos_media_type = 'photo'
        last_addftbaryga2_message = "Paveikslėlis pridėtas prie /barygos!"
    elif reply.animation:
        media = reply.animation
        barygos_media_id = media.file_id
        barygos_media_type = 'animation'
        last_addftbaryga2_message = "GIF pridėtas prie /barygos!"
    elif reply.video:
        media = reply.video
        barygos_media_id = media.file_id
        barygos_media_type = 'video'
        last_addftbaryga2_message = "Video pridėtas prie /barygos!"
    else:
        await update.message.reply_text("Atsakyk į žinutę su paveikslėliu, GIF ar video!")
        return
    
    save_data(barygos_media_id, 'barygos_media_id.pkl')
    save_data(barygos_media_type, 'barygos_media_type.pkl')
    await update.message.reply_text(last_addftbaryga2_message)

async def editpardavejai(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.message.from_user.id)
    if user_id != ADMIN_CHAT_ID:
        await update.message.reply_text("Tik adminas gali redaguoti šį tekstą!")
        return

    try:
        new_message = " ".join(context.args)
        if not new_message:
            await update.message.reply_text("Naudok: /editpardavejai 'Naujas tekstas'")
            return
        global pardavejai_message
        pardavejai_message = new_message
        save_pardavejai_message()
        await update.message.reply_text(f"Pardavėjų žinutė atnaujinta: '{pardavejai_message}'")
    except IndexError:
        await update.message.reply_text("Naudok: /editpardavejai 'Naujas tekstas'")

async def handle_vote_button(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        logger.error("No callback query received")
        return
    
    user_id = query.from_user.id
    if query.message is None:
        await query.answer("Klaida: Balsavimo žinutė nerasta. Prašau bandyti dar kartą.")
        logger.error(f"Message is None for user_id={user_id}, callback_data={query.data}")
        return
    
    chat_id = query.message.chat_id
    data = query.data

    logger.info(f"Vote attempt by user_id={user_id} in chat_id={chat_id}, callback_data={data}")

    if not data.startswith("vote_"):
        logger.warning(f"Invalid callback data: {data} from user_id={user_id}")
        return

    seller = data.replace("vote_", "")
    if seller not in trusted_sellers:
        await query.answer("Šis pardavėjas nebegalioja!")
        logger.warning(f"Attempt to vote for invalid seller '{seller}' by user_id={user_id}. Trusted sellers: {trusted_sellers}")
        return

    now = datetime.now(TIMEZONE)
    last_vote = last_vote_attempt.get(user_id, datetime.min.replace(tzinfo=TIMEZONE))
    cooldown_remaining = timedelta(days=7) - (now - last_vote)
    if cooldown_remaining > timedelta(0):
        days_left = max(1, int(cooldown_remaining.total_seconds() // 86400))
        await query.answer(f"Tu jau balsavai! Liko {days_left} dienų iki kito balsavimo.")
        await context.bot.send_message(chat_id=chat_id, text=f"@{query.from_user.username or 'User' + str(user_id)}, tu jau balsavai! Liko {days_left} dienų iki kito balsavimo.")
        logger.info(f"User_id={user_id} blocked by cooldown, {days_left} days left.")
        return

    user_points.setdefault(user_id, 0)
    votes_weekly.setdefault(seller, 0)
    votes_alltime.setdefault(seller, 0)
    votes_monthly.setdefault(seller, [])
    vote_history.setdefault(seller, [])

    logger.info(f"Before vote: user_id={user_id}, points={user_points[user_id]}, votes_weekly[{seller}]={votes_weekly[seller]}, votes_alltime[{seller}]={votes_alltime[seller]}")

    votes_weekly[seller] += 1
    votes_monthly[seller].append((now, 1))
    votes_alltime[seller] += 1
    voters.add(user_id)
    vote_history[seller].append((user_id, "up", "Button vote", now))
    user_points[user_id] += 5
    last_vote_attempt[user_id] = now

    logger.info(f"After vote: user_id={user_id}, points={user_points[user_id]}, votes_weekly[{seller}]={votes_weekly[seller]}, votes_alltime[{seller}]={votes_alltime[seller]}")

    await query.answer("Ačiū už jūsų balsą, 5 taškai buvo pridėti prie jūsų sąskaitos.")
    await query.edit_message_text(f"Ačiū už jūsų balsą už {seller}, 5 taškai pridėti!")
    
    save_data(votes_weekly, 'votes_weekly.pkl')
    save_data(votes_monthly, 'votes_monthly.pkl')
    save_data(votes_alltime, 'votes_alltime.pkl')
    save_data(vote_history, 'vote_history.pkl')
    save_data(user_points, 'user_points.pkl')

async def apklausa(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id

    if not is_allowed_group(chat_id):
        await update.message.reply_text("Botas neveikia šioje grupėje!")
        return

    try:
        question = " ".join(context.args)
        if not question:
            await update.message.reply_text("Naudok: /apklausa 'Klausimas'")
            return

        poll_id = f"{chat_id}_{user_id}_{int(datetime.now(TIMEZONE).timestamp())}"
        polls[poll_id] = {"question": question, "yes": 0, "no": 0, "voters": set()}
        logger.info(f"Created poll with ID: {poll_id}")

        keyboard = [
            [InlineKeyboardButton("Taip (0)", callback_data=f"poll_{poll_id}_yes"),
             InlineKeyboardButton("Ne (0)", callback_data=f"poll_{poll_id}_no")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(f"📊 Apklausa: {question}", reply_markup=reply_markup)
    except IndexError:
        await update.message.reply_text("Naudok: /apklausa 'Klausimas'")

async def handle_poll_button(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data

    if not data.startswith("poll_"):
        return

    parts = data.rsplit("_", 1)
    if len(parts) != 2:
        logger.error(f"Invalid callback data format: {data}")
        await query.answer("Klaida balsuojant!")
        return

    poll_id, vote = parts[0][5:], parts[1]
    logger.info(f"Poll button pressed: data={data}, poll_id={poll_id}, vote={vote}, polls.keys={list(polls.keys())}")

    if poll_id not in polls:
        logger.error(f"Poll ID {poll_id} not found in polls: {polls}")
        await query.answer("Ši apklausa nebegalioja!")
        return

    poll = polls[poll_id]
    if user_id in poll["voters"]:
        await query.answer("Jau balsavai šioje apklausoje!")
        return

    poll["voters"].add(user_id)
    if vote == "yes":
        poll["yes"] += 1
    elif vote == "no":
        poll["no"] += 1
    else:
        logger.error(f"Invalid vote type: {vote}")
        await query.answer("Klaida balsuojant!")
        return

    keyboard = [
        [InlineKeyboardButton(f"Taip ({poll['yes']})", callback_data=f"poll_{poll_id}_yes"),
         InlineKeyboardButton(f"Ne ({poll['no']})", callback_data=f"poll_{poll_id}_no")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(f"📊 Apklausa: {poll['question']}\nBalsai: Taip - {poll['yes']}, Ne - {poll['no']}", reply_markup=reply_markup)
    await query.answer("Tavo balsas užskaitytas!")

async def nepatiko(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    
    if not is_allowed_group(chat_id):
        await update.message.reply_text("Botas neveikia šioje grupėje!")
        return
    
    now = datetime.now(TIMEZONE)
    if now - last_downvote_attempt[user_id] < timedelta(days=7):
        await update.message.reply_text("Palauk 7 dienas po paskutinio nepritarimo!")
        return
    
    try:
        vendor, reason = context.args[0], " ".join(context.args[1:])
        if not reason:
            await update.message.reply_text("Prašau nurodyti priežastį!")
            return
        
        global complaint_id
        complaint_id += 1
        pending_downvotes[complaint_id] = (vendor, user_id, reason, now)
        downvoters.add(user_id)
        vote_history.setdefault(vendor, []).append((user_id, "down", reason, now))
        user_points[user_id] += 5
        last_downvote_attempt[user_id] = now
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=f"Skundas #{complaint_id}: {vendor} - '{reason}' by User {user_id}. Patvirtinti su /approve {complaint_id}"
        )
        await update.message.reply_text(f"Skundas pateiktas! Atsiųsk įrodymus @kunigasnew dėl Skundo #{complaint_id}. +5 taškų!")
        save_data(vote_history, 'vote_history.pkl')
        save_data(user_points, 'user_points.pkl')
    except IndexError:
        await update.message.reply_text("Naudok: /nepatiko @VendorTag 'Reason'")

async def approve(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.message.from_user.id)
    chat_id = update.message.chat_id
    if user_id != ADMIN_CHAT_ID:
        return
    if not (is_allowed_group(chat_id) or chat_id == int(user_id)):
        await update.message.reply_text("Ši komanda veikia tik grupėje arba privačiai!")
        return
    try:
        cid = int(context.args[0])
        if cid not in pending_downvotes:
            await update.message.reply_text("Neteisingas skundo ID!")
            return
        vendor, user_id, reason, timestamp = pending_downvotes[cid]
        votes_weekly[vendor] -= 1
        votes_monthly[vendor].append((timestamp, -1))
        votes_alltime[vendor] -= 1
        approved_downvotes[cid] = pending_downvotes[cid]
        del pending_downvotes[cid]
        await update.message.reply_text(f"Skundas patvirtintas dėl {vendor}!")
        save_data(votes_weekly, 'votes_weekly.pkl')
        save_data(votes_monthly, 'votes_monthly.pkl')
        save_data(votes_alltime, 'votes_alltime.pkl')
    except (IndexError, ValueError):
        await update.message.reply_text("Naudok: /approve ComplaintID")

async def addseller(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.message.from_user.id)
    if user_id != ADMIN_CHAT_ID:
        await update.message.reply_text("Tik adminas gali pridėti pardavėją!")
        return
    chat_id = update.message.chat_id
    if not is_allowed_group(chat_id) and chat_id != int(user_id):
        await update.message.reply_text("Botas neveikia šioje grupėje arba naudok privačiai!")
        return
    try:
        vendor = context.args[0]
        if vendor in trusted_sellers:
            await update.message.reply_text(f"{vendor} jau yra patikimų pardavėjų sąraše!")
            return
        trusted_sellers.append(vendor)
        await update.message.reply_text(f"Pardavėjas {vendor} pridėtas! Jis dabar matomas /balsuoju sąraše.")
    except IndexError:
        await update.message.reply_text("Naudok: /addseller @VendorTag")

async def removeseller(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.message.from_user.id)
    if user_id != ADMIN_CHAT_ID:
        await update.message.reply_text("Tik adminas gali pašalinti pardavėją!")
        return
    chat_id = update.message.chat_id
    if not is_allowed_group(chat_id) and chat_id != int(user_id):
        await update.message.reply_text("Botas neveikia šioje grupėje arba naudok privačiai!")
        return
    try:
        vendor = context.args[0].strip()
        vendor_lower = vendor.lower()
        matching_seller = next((s for s in trusted_sellers if s.lower() == vendor_lower), None)
        logger.info(f"Attempting to remove vendor: '{vendor}', current trusted_sellers: {trusted_sellers}")
        if matching_seller is None:
            await update.message.reply_text(f"'{vendor}' nėra patikimų pardavėjų sąraše! Sąrašas: {trusted_sellers}")
            return
        trusted_sellers.remove(matching_seller)
        votes_weekly.pop(matching_seller, None)
        votes_monthly.pop(matching_seller, None)
        votes_alltime.pop(matching_seller, None)
        await update.message.reply_text(f"Pardavėjas {matching_seller} pašalintas iš sąrašo ir balsų!")
        logger.info(f"Vendor {matching_seller} removed successfully. New trusted_sellers: {trusted_sellers}")
        save_data(votes_weekly, 'votes_weekly.pkl')
        save_data(votes_monthly, 'votes_monthly.pkl')
        save_data(votes_alltime, 'votes_alltime.pkl')
    except IndexError:
        await update.message.reply_text("Naudok: /removeseller @VendorTag")

async def sellerinfo(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.message.chat_id
    if not is_allowed_group(chat_id):
        await update.message.reply_text("Botas neveikia šioje grupėje!")
        return
    try:
        vendor = context.args[0]
        if vendor not in trusted_sellers:
            await update.message.reply_text(f"{vendor} nėra patikimas pardavėjas!")
            return
        now = datetime.now(TIMEZONE)
        monthly_score = sum(s for ts, s in votes_monthly[vendor] if now - ts < timedelta(days=30))
        downvotes_30d = sum(1 for cid, (v, _, _, ts) in approved_downvotes.items() if v == vendor and now - ts < timedelta(days=30))
        info = f"{vendor} Info:\nSavaitė: {votes_weekly[vendor]}\nMėnuo: {monthly_score}\nViso: {votes_alltime[vendor]}\nNeigiami (30d): {downvotes_30d}"
        await update.message.reply_text(info)
    except IndexError:
        await update.message.reply_text("Naudok: /pardavejoinfo @VendorTag")

async def barygos(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.message.chat_id
    if not is_allowed_group(chat_id):
        await update.message.reply_text("Botas neveikia šioje grupėje!")
        return
    now = datetime.now(TIMEZONE)
    
    message = ""
    if last_addftbaryga2_message:
        message += f"{last_addftbaryga2_message}\n\n"
    
    weekly_board = "🏆 Savaitės Top Pardavėjai 🏆\n"
    if not votes_weekly:
        weekly_board += "Dar nėra balsų šią savaitę!\n"
    else:
        sorted_weekly = sorted(votes_weekly.items(), key=lambda x: x[1], reverse=True)
        for vendor, score in sorted_weekly[:3]:
            weekly_board += f"{vendor}: {score}\n"
    
    monthly_board = "📅 Mėnesio Top Pardavėjai 📅\n"
    monthly_totals = defaultdict(int)
    for vendor, votes_list in votes_monthly.items():
        votes_list[:] = [(ts, s) for ts, s in votes_list if now - ts < timedelta(days=30)]
        monthly_totals[vendor] = sum(s for _, s in votes_list)
    if not monthly_totals:
        monthly_board += "Nėra balsų per 30 dienų!\n"
    else:
        sorted_monthly = sorted(monthly_totals.items(), key=lambda x: x[1], reverse=True)
        for vendor, score in sorted_monthly[:3]:
            monthly_board += f"{vendor}: {score}\n"
    
    alltime_board = "🌟 Visų Laikų Top 5 Pardavėjai 🌟\n"
    if not votes_alltime:
        alltime_board += "Dar nėra balsų!\n"
    else:
        sorted_alltime = sorted(votes_alltime.items(), key=lambda x: x[1], reverse=True)
        for i, (vendor, score) in enumerate(sorted_alltime[:5], 1):
            alltime_board += f"{i}. {vendor}: {score}\n"
    
    full_message = f"{message}{weekly_board}\n{monthly_board}\n{alltime_board}"
    if 'barygos_media_id' in globals() and barygos_media_id and barygos_media_type:
        if barygos_media_type == 'photo':
            await context.bot.send_photo(chat_id=chat_id, photo=barygos_media_id, caption=full_message)
        elif barygos_media_type == 'animation':
            await context.bot.send_animation(chat_id=chat_id, animation=barygos_media_id, caption=full_message)
        elif barygos_media_type == 'video':
            await context.bot.send_video(chat_id=chat_id, video=barygos_media_id, caption=full_message)
    else:
        await context.bot.send_message(chat_id=chat_id, text=full_message)

async def chatking(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.message.chat_id
    if not is_allowed_group(chat_id):
        await update.message.reply_text("Botas neveikia šioje grupėje!")
        return
    
    if not alltime_messages:
        await update.message.reply_text("Dar nėra žinučių!")
        return
    
    sorted_chatters = sorted(alltime_messages.items(), key=lambda x: x[1], reverse=True)[:10]
    leaderboard = "👑 Visų Laikų Pokalbių Karaliai 👑\n"
    for user_id, msg_count in sorted_chatters:
        try:
            member = await context.bot.get_chat_member(chat_id, user_id)
            username = f"@{member.user.username}" if member.user.username else f"User {user_id}"
            leaderboard += f"{username}: {msg_count} žinučių\n"
        except telegram.error.TelegramError:
            leaderboard += f"User {user_id}: {msg_count} žinučių\n"
    
    await update.message.reply_text(leaderboard)

async def handle_message(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.message.chat_id
    if not is_allowed_group(chat_id) or update.message.text.startswith('/'):
        return
    user_id = update.message.from_user.id
    username = update.message.from_user.username
    if username:
        username_to_id[f"@{username.lower()}"] = user_id
    
    today = datetime.now(TIMEZONE)
    daily_messages[user_id][today.date()] += 1
    weekly_messages[user_id] += 1
    alltime_messages.setdefault(user_id, 0)  # Ensure key exists with default 0
    alltime_messages[user_id] += 1
    
    yesterday = today - timedelta(days=1)
    last_day = last_chat_day[user_id].date()
    if last_day == yesterday.date():
        chat_streaks[user_id] += 1
    elif last_day != today.date():
        chat_streaks[user_id] = 1
    last_chat_day[user_id] = today
    save_data(alltime_messages, 'alltime_messages.pkl')
    save_data(chat_streaks, 'chat_streaks.pkl')
    save_data(last_chat_day, 'last_chat_day.pkl')

async def award_daily_points(context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    today = datetime.now(TIMEZONE).date()
    yesterday = today - timedelta(days=1)
    for user_id in daily_messages:
        msg_count = daily_messages[user_id].get(yesterday, 0)
        if msg_count < 50:
            continue
        
        chat_points = min(3, (msg_count // 50))
        streak_bonus = chat_streaks[user_id] // 3
        total_points = chat_points + streak_bonus
        user_points[user_id] += total_points
        
        msg = f"Gavai {chat_points} taškus už {msg_count} žinučių vakar!"
        if streak_bonus > 0:
            msg += f" +{streak_bonus} už {chat_streaks[user_id]}-dienų seriją!"
        
        try:
            username = next(k for k, v in username_to_id.items() if v == user_id)
            await context.bot.send_message(
                chat_id=GROUP_CHAT_ID,
                text=f"{username}, {msg} Dabar turi {user_points[user_id]} taškų!"
            )
        except StopIteration:
            pass
    
    daily_messages.clear()
    save_data(user_points, 'user_points.pkl')

async def weekly_recap(context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    if not weekly_messages:
        return
    
    sorted_chatters = sorted(weekly_messages.items(), key=lambda x: x[1], reverse=True)[:3]
    recap = "📢 Savaitės Pokalbių Karaliai 📢\n"
    for user_id, msg_count in sorted_chatters:
        try:
            username = next(k for k, v in username_to_id.items() if v == user_id)
            recap += f"{username}: {msg_count} žinučių\n"
        except StopIteration:
            recap += f"User {user_id}: {msg_count} žinučių\n"
    
    await context.bot.send_message(GROUP_CHAT_ID, recap)
    weekly_messages.clear()

async def coinflip(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.message.chat_id
    if not is_allowed_group(chat_id):
        await update.message.reply_text("Botas neveikia šioje grupėje!")
        return
    initiator_id = update.message.from_user.id
    try:
        amount = int(context.args[0])
        opponent = context.args[1]
        
        if amount <= 0 or user_points[initiator_id] < amount:
            await update.message.reply_text("Netinkama suma arba trūksta taškų!")
            return
        
        initiator_member = await context.bot.get_chat_member(chat_id, initiator_id)
        initiator_username = f"@{initiator_member.user.username}" if initiator_member.user.username else f"@User{initiator_id}"

        target_id = username_to_id.get(opponent.lower(), None)
        if not target_id or opponent == initiator_username:
            await update.message.reply_text("Negalima mesti iššūkio sau ar neegzistuojančiam vartotojui!")
            return
        
        opponent_tag = opponent
        if user_points[target_id] < amount:
            await update.message.reply_text(f"{opponent_tag} neturi pakankamai taškų!")
            return
        
        coinflip_challenges[target_id] = (initiator_id, amount, datetime.now(TIMEZONE), initiator_username, opponent_tag, chat_id)
        await update.message.reply_text(f"{initiator_username} iššaukė {opponent_tag} monetos metimui už {amount} taškų! Priimk su /accept_coinflip!")
        context.job_queue.run_once(expire_challenge, 300, context=(target_id, context))
    except (IndexError, ValueError):
        await update.message.reply_text("Naudok: /coinflip Amount @Username")

async def accept_coinflip(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    if user_id not in coinflip_challenges:
        await update.message.reply_text("Nėra aktyvaus iššūkio!")
        return
    initiator_id, amount, timestamp, initiator_username, opponent_username, original_chat_id = coinflip_challenges[user_id]
    now = datetime.now(TIMEZONE)
    if now - timestamp > timedelta(minutes=5) or chat_id != original_chat_id:
        del coinflip_challenges[user_id]
        await update.message.reply_text("Iššūkis pasibaigė arba neteisinga grupė!")
        return
    result = random.choice([initiator_id, user_id])
    await context.bot.send_sticker(chat_id=chat_id, sticker=COINFLIP_STICKER_ID)
    if result == initiator_id:
        user_points[initiator_id] += amount
        user_points[user_id] -= amount
        await update.message.reply_text(f"🪙 {initiator_username} laimėjo {amount} taškų prieš {opponent_username}!")
    else:
        user_points[user_id] += amount
        user_points[initiator_id] -= amount
        await update.message.reply_text(f"🪙 {opponent_username} laimėjo {amount} taškų prieš {initiator_username}!")
    del coinflip_challenges[user_id]
    save_data(user_points, 'user_points.pkl')

async def expire_challenge(context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    opponent_id, ctx = context.job.context
    if opponent_id in coinflip_challenges:
        _, amount, _, initiator_username, opponent_username, chat_id = coinflip_challenges[opponent_id]
        del coinflip_challenges[opponent_id]
        await ctx.bot.send_message(chat_id, f"Iššūkis tarp {initiator_username} ir {opponent_username} už {amount} taškų pasibaigė!")

async def addpoints(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.message.from_user.id)
    if user_id != ADMIN_CHAT_ID:
        await update.message.reply_text("Tik adminas gali pridėti taškus!")
        return
    try:
        amount = int(context.args[0])
        target = context.args[1]
        target_id = int(target.strip('@User'))
        user_points[target_id] += amount
        await update.message.reply_text(f"Pridėta {amount} taškų @User{target_id}! Dabar: {user_points[target_id]}")
        save_data(user_points, 'user_points.pkl')
    except (IndexError, ValueError):
        await update.message.reply_text("Naudok: /addpoints Amount @UserID")

async def points(update: telegram.Update, context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    logger.info(f"/points called by user_id={user_id} in chat_id={chat_id}")

    if not is_allowed_group(chat_id):
        await update.message.reply_text("Botas neveikia šioje grupėje!")
        logger.warning(f"Chat_id={chat_id} not in allowed_groups={allowed_groups}")
        return

    points = user_points.get(user_id, 0)
    streak = chat_streaks.get(user_id, 0)
    await update.message.reply_text(f"Jūsų taškai: {points}\nSerija: {streak} dienų")
    logger.info(f"Points for user_id={user_id}: {points}, Streak: {streak}")

async def reset_votes(context: telegram.ext.ContextTypes.DEFAULT_TYPE) -> None:
    global votes_weekly, voters, downvoters, pending_downvotes, complaint_id, last_vote_attempt
    votes_weekly.clear()
    voters.clear()
    downvoters.clear()
    pending_downvotes.clear()
    last_vote_attempt.clear()
    complaint_id = 0
    await context.bot.send_message(GROUP_CHAT_ID, "Nauja balsavimo savaitė prasidėjo!")
    save_data(votes_weekly, 'votes_weekly.pkl')

# Add handlers
application.add_handler(CommandHandler(['startas'], startas))
application.add_handler(CommandHandler(['activate_group'], activate_group))
application.add_handler(CommandHandler(['nepatiko'], nepatiko))
application.add_handler(CommandHandler(['approve'], approve))
application.add_handler(CommandHandler(['addseller'], addseller))
application.add_handler(CommandHandler(['removeseller'], removeseller))
application.add_handler(CommandHandler(['pardavejoinfo'], sellerinfo))
application.add_handler(CommandHandler(['barygos'], barygos))
application.add_handler(CommandHandler(['balsuoju'], balsuoju))
application.add_handler(CommandHandler(['chatking'], chatking))
application.add_handler(CommandHandler(['coinflip'], coinflip))
application.add_handler(CommandHandler(['accept_coinflip'], accept_coinflip))
application.add_handler(CommandHandler(['addpoints'], addpoints))
application.add_handler(CommandHandler(['points'], points))
application.add_handler(CommandHandler(['debug'], debug))
application.add_handler(CommandHandler(['whoami'], whoami))
application.add_handler(CommandHandler(['addftbaryga'], addftbaryga))
application.add_handler(CommandHandler(['addftbaryga2'], addftbaryga2))
application.add_handler(CommandHandler(['editpardavejai'], editpardavejai))
application.add_handler(CommandHandler(['apklausa'], apklausa))
application.add_handler(CommandHandler(['privatus'], privatus))
application.add_handler(MessageHandler(filters.Regex('^/start$') & filters.ChatType.PRIVATE, start_private))
application.add_handler(CallbackQueryHandler(handle_vote_button, pattern="vote_"))
application.add_handler(CallbackQueryHandler(handle_poll_button, pattern="poll_"))
application.add_handler(CallbackQueryHandler(handle_admin_button, pattern="admin_"))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

# Schedule jobs
application.job_queue.run_daily(award_daily_points, time=time(hour=0, minute=0))
application.job_queue.scheduler.add_job(
    weekly_recap, CronTrigger(day_of_week='sun', hour=23, minute=0, timezone=TIMEZONE), args=[application], id='weekly_recap'
)
application.job_queue.scheduler.add_job(
    reset_votes, CronTrigger(day_of_week='mon', hour=0, minute=0, timezone=TIMEZONE), args=[application], id='reset_votes_weekly'
)

if __name__ == '__main__':
    try:
        logger.info("Starting bot polling...")
        application.run_polling()
    except Exception as e:
        logger.error(f"Polling failed: {str(e)}")
    logger.info("Bot polling stopped.")