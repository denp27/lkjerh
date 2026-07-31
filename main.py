import os
import asyncio
import sqlite3
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from telethon import TelegramClient
from telethon.errors import (
    SessionPasswordNeededError,
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
    PasswordHashInvalidError
)

# ================= КОНФИГУРАЦИЯ =================
BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"  # Токен основного бота от @BotFather
API_ID = 12345678                  # api_id с my.telegram.org
API_HASH = "YOUR_API_HASH_HERE"    # api_hash с my.telegram.org

# Интервал автоматической проверки всех аккаунтов (в секундах)
# 3600 = 1 час, 21600 = 6 часов
CHECK_INTERVAL_SECONDS = 3600

SESSIONS_DIR = "sessions"
DB_PATH = "database.db"
os.makedirs(SESSIONS_DIR, exist_ok=True)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# Временное хранилище сессий во время авторизации
AUTH_SESSIONS = {}

# ================= БАЗА ДАННЫХ =================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            session_name TEXT PRIMARY KEY,
            owner_id INTEGER,
            phone TEXT,
            has_spamblock INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()

def add_account_to_db(session_name: str, owner_id: int, phone: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO accounts (session_name, owner_id, phone, has_spamblock) VALUES (?, ?, ?, 0)",
        (session_name, owner_id, phone)
    )
    conn.commit()
    conn.close()

def get_all_accounts():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT session_name, owner_id, phone, has_spamblock FROM accounts")
    rows = cursor.fetchall()
    conn.close()
    return rows

def update_spamblock_status(session_name: str, status: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE accounts SET has_spamblock = ? WHERE session_name = ?", (status, session_name))
    conn.commit()
    conn.close()

# ================= FSM (СОСТОЯНИЯ) =================
class AddAccountSG(StatesGroup):
    waiting_for_phone = State()
    waiting_for_code = State()
    waiting_for_2fa = State()

# ================= КЛАВИАТУРЫ =================
def get_main_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить аккаунт", callback_data="add_account")],
        [InlineKeyboardButton(text="🔄 Проверить аккаунты сейчас", callback_data="check_now")]
    ])

# ================= ХЕНДЛЕРЫ МЕНЮ =================
@dp.message(CommandStart())
@dp.message(F.text == "/start")
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "👋 **Система автоматического мониторинга SpamBot**\n\n"
        "Добавьте аккаунт, и бот будет автоматически отправлять `/start` в `@SpamBot` "
        "для проверки спамблока и разблокировки.",
        reply_markup=get_main_keyboard(),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data == "add_account")
async def start_add_account(call: types.CallbackQuery, state: FSMContext):
    await state.set_state(AddAccountSG.waiting_for_phone)
    await call.message.edit_text(
        "📱 **Шаг 1 из 3**\n\n"
        "Введите номер телефона аккаунта в международном формате (например, `+79001234567`):",
        parse_mode="Markdown"
    )

# ================= ШАГ 1: ВВОД НОМЕРА =================
@dp.message(AddAccountSG.waiting_for_phone)
async def process_phone(message: types.Message, state: FSMContext):
    phone = message.text.strip().replace(" ", "")
    user_id = message.from_user.id
    session_name = f"user_{user_id}_{phone.replace('+', '')}"
    session_file = os.path.join(SESSIONS_DIR, f"{session_name}.session")

    client = TelegramClient(session_file, API_ID, API_HASH)
    await client.connect()

    try:
        sent_code = await client.send_code_request(phone)
        AUTH_SESSIONS[user_id] = {
            "client": client,
            "phone_code_hash": sent_code.phone_code_hash,
            "phone": phone,
            "session_name": session_name
        }
        await state.set_state(AddAccountSG.waiting_for_code)
        await message.answer(
            "📩 **Шаг 2 из 3**\n\n"
            "Код подтверждения отправлен в Telegram.\n"
            "Введите полученный код:"
        )
    except Exception as e:
        await client.disconnect()
        await message.answer(f"❌ Ошибка отправки кода: `{e}`\n\nПопробуйте снова через /start.")
        await state.clear()

# ================= ШАГ 2: ВВОД КОДА =================
@dp.message(AddAccountSG.waiting_for_code)
async def process_code(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    session_data = AUTH_SESSIONS.get(user_id)

    if not session_data:
        await message.answer("⚠️ Сессия истекла. Начните заново с /start.")
        await state.clear()
        return

    client: TelegramClient = session_data["client"]
    code = message.text.strip()

    try:
        await client.sign_in(
            phone=session_data["phone"],
            code=code,
            phone_code_hash=session_data["phone_code_hash"]
        )
        
        me = await client.get_me()
        await client.disconnect()

        # Сохраняем в БД
        add_account_to_db(session_data["session_name"], user_id, session_data["phone"])
        del AUTH_SESSIONS[user_id]
        await state.clear()

        await message.answer(
            f"✅ **Аккаунт успешно добавлен в авто-мониторинг!**\n\n"
            f"👤 Имя: `{me.first_name}`\n"
            f"📞 Телефон: `{session_data['phone']}`",
            reply_markup=get_main_keyboard(),
            parse_mode="Markdown"
        )

    except SessionPasswordNeededError:
        await state.set_state(AddAccountSG.waiting_for_2fa)
        await message.answer(
            "🔐 **Шаг 3 из 3**\n\n"
            "На аккаунте включен 2FA (облачный пароль).\n"
            "Введите ваш пароль:"
        )
    except (PhoneCodeInvalidError, PhoneCodeExpiredError):
        await message.answer("❌ Неверный или истекший код. Попробуйте ввести код снова:")
    except Exception as e:
        await client.disconnect()
        if user_id in AUTH_SESSIONS:
            del AUTH_SESSIONS[user_id]
        await state.clear()
        await message.answer(f"❌ Ошибка авторизации: `{e}`", reply_markup=get_main_keyboard())

# ================= ШАГ 3: ВВОД 2FA =================
@dp.message(AddAccountSG.waiting_for_2fa)
async def process_2fa(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    session_data = AUTH_SESSIONS.get(user_id)

    if not session_data:
        await message.answer("⚠️ Сессия истекла. Начните заново с /start.")
        await state.clear()
        return

    client: TelegramClient = session_data["client"]
    password = message.text.strip()

    try:
        await client.sign_in(password=password)
        me = await client.get_me()
        await client.disconnect()

        add_account_to_db(session_data["session_name"], user_id, session_data["phone"])
        del AUTH_SESSIONS[user_id]
        await state.clear()

        await message.answer(
            f"✅ **Аккаунт с 2FA успешно добавлен в авто-мониторинг!**\n\n"
            f"👤 Имя: `{me.first_name}`\n"
            f"📞 Телефон: `{session_data['phone']}`",
            reply_markup=get_main_keyboard(),
            parse_mode="Markdown"
        )
    except PasswordHashInvalidError:
        await message.answer("❌ Неверный пароль 2FA. Попробуйте еще раз:")
    except Exception as e:
        await client.disconnect()
        if user_id in AUTH_SESSIONS:
            del AUTH_SESSIONS[user_id]
        await state.clear()
        await message.answer(f"❌ Ошибка: `{e}`", reply_markup=get_main_keyboard())

# ================= РУЧНОЙ ЗАПУСК ПРОВЕРКИ =================
@dp.callback_query(F.data == "check_now")
async def trigger_manual_check(call: types.CallbackQuery):
    await call.answer("🔍 Запускаем проверку...", show_alert=False)
    await run_spambot_check_for_all()

# ================= ЛОГИКА ПРОВЕРКИ SPAMBOT =================
async def check_single_account(session_name: str, owner_id: int, phone: str, prev_status: int):
    session_file = os.path.join(SESSIONS_DIR, f"{session_name}.session")
    if not os.path.exists(session_file):
        return

    client = TelegramClient(session_file, API_ID, API_HASH)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            await bot.send_message(owner_id, f"⚠️ Сессия для аккаунта `{phone}` слетела. Добавьте его заново.")
            await client.disconnect()
            return

        spam_bot = '@SpamBot'
        
        # 1. Отправляем /start для проверки и попытки снять спамблок
        await client.send_message(spam_bot, '/start')
        await asyncio.sleep(3)

        messages = await client.get_messages(spam_bot, limit=1)
        if not messages:
            await client.disconnect()
            return

        response_text = messages[0].text

        # Проверяем на отсутствие спамблока
        is_clean = ("Good news" in response_text or 
                    "Ваш аккаунт свободен" in response_text or 
                    "нет никаких ограничений" in response_text.lower())

        if is_clean:
            current_status = 0
            if prev_status == 1:
                # Спамблок БЫЛ, но СНЯЛСЯ
                update_spamblock_status(session_name, 0)
                await bot.send_message(
                    owner_id,
                    f"🎉 **СПАМБЛОК СНЯТ!**\n\n"
                    f"📱 Аккаунт: `{phone}`\n"
                    f"💬 Ответ SpamBot:\n`{response_text}`",
                    parse_mode="Markdown"
                )
        else:
            current_status = 1
            if prev_status == 0:
                # Появился НОВЫЙ спамблок
                update_spamblock_status(session_name, 1)
                await bot.send_message(
                    owner_id,
                    f"🚨 **ОБНАРУЖЕН СПАМБЛОК!**\n\n"
                    f"📱 Аккаунт: `{phone}`\n"
                    f"✉️ В @SpamBot отправлен `/start` для снятия блокировки.\n\n"
                    f"💬 Ответ бота:\n`{response_text}`",
                    parse_mode="Markdown"
                )

        await client.disconnect()

    except Exception as e:
        print(f"Ошибка при проверке {phone}: {e}")
        if client.is_connected():
            await client.disconnect()

async def run_spambot_check_for_all():
    accounts = get_all_accounts()
    for session_name, owner_id, phone, has_spamblock in accounts:
        await check_single_account(session_name, owner_id, phone, has_spamblock)
        await asyncio.sleep(2)  # Небольшая пауза между аккаунтами

async def periodic_check_task():
    while True:
        await run_spambot_check_for_all()
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)

# ================= ЗАПУСК =================
async def main():
    init_db()
    # Запускаем фоновую периодическую задачу
    asyncio.create_task(periodic_check_task())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
