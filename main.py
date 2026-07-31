import asyncio
import os
import random
import re
import io
import qrcode
import socks

from telethon import TelegramClient, events, Button
from telethon.errors import (
    PeerFloodError,
    UserPrivacyRestrictedError,
    FloodWaitError,
    InviteHashExpiredError,
    UserAlreadyParticipantError,
    SessionPasswordNeededError,
    PhoneCodeInvalidError,
    PasswordHashInvalidError
)
from telethon.tl.functions.messages import SendReactionRequest, ReadHistoryRequest, ImportChatInviteRequest
from telethon.tl.types import ReactionEmoji

# ==================== КОНФИГУРАЦИЯ ====================
API_ID = 12345678                  # Ваш API ID (с my.telegram.org)
API_HASH = 'YOUR_API_HASH'          # Ваш API Hash
BOT_TOKEN = 'YOUR_BOT_TOKEN'        # Токен бота от @BotFather
ADMIN_ID = 123456789                # Ваш личный Telegram ID (число)

USERBOT_SESSION = 'userbot_session'
BOT_SESSION = 'admin_bot_session'

PARSED_USERS_FILE = 'parsed_users.txt'
POPULAR_CHANNELS = ['durov', 'telegram', 'rbc_news', 'ria_realtime', 'tass_agency', 'mash']
REACTIONS = ['👍', '🔥', '👏', '❤️', '🤔', '🎉']

# ==================== БАЗА ДАННЫХ ПРОКСИ В ПАМЯТИ ====================
# Структура: [{'id': int, 'type': socks.HTTP/SOCKS5/SOCKS4, 'host': str, 'port': int, 'user': str, 'password': str}]
proxies_db = []
active_proxy_id = None

user_states = {}
auth_context = {}


def get_telethon_proxy(proxy_dict):
    """Преобразование словаря прокси в формат Telethon (универсально для HTTP, SOCKS4, SOCKS5)"""
    if not proxy_dict:
        return None

    p_type = proxy_dict.get('type', socks.HTTP)
    host = proxy_dict['host']
    port = proxy_dict['port']
    user = proxy_dict.get('user')
    password = proxy_dict.get('password')

    if user and password:
        return (p_type, host, port, True, user, password)
    return (p_type, host, port)


def parse_proxy_string(raw_text: str):
    """Автоматический парсер входящей строки прокси любого формата"""
    text = raw_text.strip()
    p_type = None

    # Определение по префиксу
    if text.startswith("socks5://"):
        p_type = socks.SOCKS5
        text = text.replace("socks5://", "")
    elif text.startswith("socks4://"):
        p_type = socks.SOCKS4
        text = text.replace("socks4://", "")
    elif text.startswith("http://") or text.startswith("https://"):
        p_type = socks.HTTP
        text = text.replace("http://", "").replace("https://", "")

    parts = text.split(':')
    if len(parts) not in (2, 4):
        return None

    host = parts[0]
    try:
        port = int(parts[1])
    except ValueError:
        return None

    user = parts[2] if len(parts) == 4 else None
    password = parts[3] if len(parts) == 4 else None

    # Если тип не был задан через префикс, определяем по частым портам
    if p_type is None:
        if port in (1080, 1081, 9050, 9150):
            p_type = socks.SOCKS5
        else:
            p_type = socks.HTTP  # Порты 8000, 8080, 3128, 80 и т.д.

    return {
        'type': p_type,
        'host': host,
        'port': port,
        'user': user,
        'password': password
    }


# ==================== МОДУЛЬ ЮЗЕРБОТА ====================
class UserbotWorker:
    def __init__(self):
        self.client = None

    def get_current_proxy(self):
        global active_proxy_id, proxies_db
        if active_proxy_id is None:
            return None
        for p in proxies_db:
            if p['id'] == active_proxy_id:
                return p
        return None

    async def init_client(self):
        """Инициализация клиента с выбранным прокси"""
        proxy_info = self.get_current_proxy()
        if not proxy_info:
            raise ValueError("⛔ Прокси не выбран! Добавьте и выберите прокси в меню.")

        telethon_proxy = get_telethon_proxy(proxy_info)

        if self.client and self.client.is_connected():
            await self.client.disconnect()

        self.client = TelegramClient(USERBOT_SESSION, API_ID, API_HASH, proxy=telethon_proxy)
        await self.client.connect()

        if not await self.client.is_user_authorized():
            raise PermissionError("🔒 Аккаунт не авторизован! Выполните вход через меню.")

    async def check_ban(self) -> str:
        try:
            await self.init_client()
            spam_bot = await self.client.get_entity('SpamBot')
            await self.client(ReadHistoryRequest(peer=spam_bot, max_id=0))
            await self.client.send_message(spam_bot, '/start')
            await asyncio.sleep(3)

            messages = await self.client.get_messages(spam_bot, limit=1)
            if not messages:
                return "❓ Нет ответа от SpamBot."

            text = messages[0].text
            if any(p in text.lower() for p in ["good news", "свободен", "никаких ограничений"]):
                return "✅ **Аккаунт чист** (спамблока нет)."
            else:
                return f"⚠️ **Статус ограничения:**\n\n{text}"
        except (ValueError, PermissionError) as err:
            return str(err)
        except Exception as e:
            return f"❌ Ошибка проверки прокси/аккаунта: {e}"

    async def warm_up(self, cycles=1, progress_callback=None):
        try:
            await self.init_client()
            channels = POPULAR_CHANNELS.copy()

            for cycle in range(cycles):
                random.shuffle(channels)
                for channel in channels:
                    if progress_callback:
                        await progress_callback(f"🔥 **Прогрев:** Просмотр @{channel} (Цикл {cycle+1}/{cycles})")

                    try:
                        entity = await self.client.get_entity(channel)
                        messages = await self.client.get_messages(entity, limit=5)
                        if not messages:
                            continue

                        await self.client(ReadHistoryRequest(peer=entity, max_id=messages[0].id))

                        for msg in messages:
                            await asyncio.sleep(random.randint(4, 7))
                            if random.random() < 0.35 and msg.id:
                                emoji = random.choice(REACTIONS)
                                try:
                                    await self.client(SendReactionRequest(
                                        peer=entity,
                                        msg_id=msg.id,
                                        reaction=[ReactionEmoji(emoticon=emoji)]
                                    ))
                                except Exception:
                                    pass

                        await asyncio.sleep(random.randint(8, 15))
                    except Exception as e:
                        print(f"Ошибка прогрева {channel}: {e}")

            return "✅ **Прогрев успешно завершен!**"
        except (ValueError, PermissionError) as err:
            return str(err)
        except Exception as e:
            return f"❌ Ошибка прогрева: {e}"

    async def parse_target(self, target: str, limit: int = 300) -> str:
        try:
            await self.init_client()
            entity = None

            if "t.me/+" in target or "joinchat" in target:
                hash_match = re.search(r'(?:joinchat/|\+|\/join\/)([a-zA-Z0-9_-]+)', target)
                if not hash_match:
                    return "❌ Некорректный формат приватной ссылки."
                try:
                    updates = await self.client(ImportChatInviteRequest(hash_match.group(1)))
                    entity = updates.chats[0]
                except UserAlreadyParticipantError:
                    entity = await self.client.get_entity(target)
            else:
                entity = await self.client.get_entity(target)

            parsed_users = set()
            try:
                async for user in self.client.iter_participants(entity, limit=limit):
                    if user.username and not user.bot:
                        parsed_users.add(user.username)
            except Exception:
                pass

            if len(parsed_users) < 5:
                try:
                    async for message in self.client.iter_messages(entity, limit=200):
                        if message.sender_id and getattr(message.sender, 'username', None):
                            if not message.sender.bot:
                                parsed_users.add(message.sender.username)
                except Exception:
                    pass

            if not parsed_users:
                return "❌ Не удалось собрать юзернеймы из этого источника."

            existing_users = set()
            if os.path.exists(PARSED_USERS_FILE):
                with open(PARSED_USERS_FILE, 'r', encoding='utf-8') as f:
                    existing_users = set(line.strip() for line in f if line.strip())

            all_users = existing_users.union(parsed_users)
            with open(PARSED_USERS_FILE, 'w', encoding='utf-8') as f:
                for u in all_users:
                    f.write(f"{u}\n")

            return f"✅ **Парсинг завершен!**\nНовых найдено: `{len(parsed_users)}`\nВсего в базе: `{len(all_users)}`"
        except (ValueError, PermissionError) as err:
            return str(err)
        except Exception as e:
            return f"❌ Ошибка при парсинге: {e}"

    async def broadcast(self, message_text: str, progress_callback=None) -> str:
        if not os.path.exists(PARSED_USERS_FILE):
            return "❌ Файл базы не найден. Сначала выполните парсинг."

        with open(PARSED_USERS_FILE, 'r', encoding='utf-8') as f:
            users = [line.strip() for line in f if line.strip()]

        if not users:
            return "❌ База пользователей пуста."

        try:
            await self.init_client()
            successful = 0

            for index, username in enumerate(users):
                if progress_callback and index % 2 == 0:
                    await progress_callback(f"✉️ **Рассылка:** [{index+1}/{len(users)}] для @{username}...")

                try:
                    await self.client.send_message(username, message_text)
                    successful += 1
                    await asyncio.sleep(random.randint(40, 75))
                except PeerFloodError:
                    return f"⛔ **Спамблок!** Рассылка остановлена. Успешно: `{successful}`."
                except Exception:
                    pass

            return f"✅ **Рассылка завершена!** Доставлено: `{successful}/{len(users)}`"
        except (ValueError, PermissionError) as err:
            return str(err)
        except Exception as e:
            return f"❌ Ошибка рассылки: {e}"


# ==================== ИНИЦИАЛИЗАЦИЯ И МЕНЮ ====================
worker = UserbotWorker()
bot = TelegramClient(BOT_SESSION, API_ID, API_HASH)

def get_main_keyboard():
    return [
        [Button.inline("🔑 Авторизовать Аккаунт", b"auth_menu")],
        [Button.inline("🔍 Проверить бан (@SpamBot)", b"check_ban")],
        [Button.inline("🔥 Запустить прогрев", b"warmup"), Button.inline("👥 Парсинг", b"parse")],
        [Button.inline("✉️ Рассылка", b"broadcast")],
        [Button.inline("🌐 Управление Прокси", b"proxy_menu")]
    ]

def get_proxy_keyboard():
    buttons = []
    type_names = {socks.HTTP: "HTTP", socks.SOCKS5: "SOCKS5", socks.SOCKS4: "SOCKS4"}
    
    for p in proxies_db:
        status_icon = "✅ " if p['id'] == active_proxy_id else ""
        proto = type_names.get(p['type'], 'PROXY')
        label = f"{status_icon}[{proto}] {p['host']}:{p['port']}"
        buttons.append([Button.inline(label, f"select_proxy_{p['id']}".encode())])
    
    buttons.append([Button.inline("➕ Добавить прокси", b"add_proxy")])
    buttons.append([Button.inline("⬅️ Назад в меню", b"main_menu")])
    return buttons

def get_auth_keyboard():
    return [
        [Button.inline("📱 По номеру телефона", b"auth_phone")],
        [Button.inline("🔲 По QR-коду", b"auth_qr")],
        [Button.inline("⬅️ Назад", b"main_menu")]
    ]


# ==================== ОБРАБОТКА КОМАНД И КНОПОК ====================

@bot.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    if event.sender_id != ADMIN_ID:
        return
    await event.respond("🤖 **Панель управления Telegram Юзерботом**\n\n⚠️ *Все операции выполняются исключительно через активный прокси.*", buttons=get_main_keyboard())


@bot.on(events.CallbackQuery)
async def callback_handler(event):
    if event.sender_id != ADMIN_ID:
        return

    data = event.data

    if data == b"main_menu":
        user_states.pop(event.sender_id, None)
        await event.edit("🤖 **Главное меню**", buttons=get_main_keyboard())

    # --- НАСТРОЙКА ПРОКСИ ---
    elif data == b"proxy_menu":
        active_p = worker.get_current_proxy()
        if active_p:
            proto = "HTTP" if active_p['type'] == socks.HTTP else "SOCKS5"
            active_str = f"`{proto} -> {active_p['host']}:{active_p['port']}`"
        else:
            active_str = "❌ Не выбран"

        text = f"🌐 **Настройки Прокси:**\n\nАктивный прокси: {active_str}\n\nВыберите прокси из списка ниже или добавьте новый:"
        await event.edit(text, buttons=get_proxy_keyboard())

    elif data == b"add_proxy":
        user_states[event.sender_id] = 'awaiting_proxy_input'
        await event.edit(
            "🌐 **Отправьте прокси в любом из форматов:**\n\n"
            "• `168.181.53.243:8000` *(авто-HTTP)*\n"
            "• `168.181.53.243:8000:user:pass`\n"
            "• `socks5://192.168.1.1:1080:user:pass`\n"
            "• `http://192.168.1.1:8000:user:pass`",
            buttons=[[Button.inline("❌ Отмена", b"proxy_menu")]]
        )

    elif data.startswith(b"select_proxy_"):
        pid = int(data.decode().split("_")[2])
        global active_proxy_id
        active_proxy_id = pid
        await event.answer("✅ Активный прокси изменен!")
        await callback_handler(type('Event', (), {'sender_id': ADMIN_ID, 'data': b"proxy_menu", 'edit': event.edit, 'answer': event.answer})())

    # --- АВТОРИЗАЦИЯ ---
    elif data == b"auth_menu":
        if not worker.get_current_proxy():
            await event.answer("⛔ Сначала добавьте и выберите Прокси!", alert=True)
            return
        await event.edit("🔑 **Выберите способ входа в аккаунт:**\n\n*(Соединение устанавливается через активный прокси)*", buttons=get_auth_keyboard())

    # Авторизация по номеру
    elif data == b"auth_phone":
        user_states[event.sender_id] = 'awaiting_phone'
        await event.edit("📱 Отправьте **номер телефона** в международном формате (например `+79991234567`):", buttons=[[Button.inline("❌ Отмена", b"auth_menu")]])

    # Авторизация по QR-коду
    elif data == b"auth_qr":
        proxy_info = worker.get_current_proxy()
        telethon_proxy = get_telethon_proxy(proxy_info)
        
        await event.edit("⏳ Подключение к Telegram через прокси...")

        auth_client = TelegramClient(USERBOT_SESSION, API_ID, API_HASH, proxy=telethon_proxy)
        try:
            await auth_client.connect()
        except Exception as e:
            await event.edit(
                f"❌ **Не удалось подключиться через данный прокси!**\n\nОшибка: `{e}`\n\nПроверьте работоспособность прокси.",
                buttons=[[Button.inline("⬅️ Прокси меню", b"proxy_menu")]]
            )
            return

        try:
            qr_login = await auth_client.qr_login()
            
            img = qrcode.make(qr_login.url)
            bio = io.BytesIO()
            bio.name = 'qr.png'
            img.save(bio, 'PNG')
            bio.seek(0)

            await bot.send_file(
                event.chat_id,
                bio,
                caption="🔲 **Сканируйте QR-код в приложении Telegram:**\n\n*Настройки -> Устройства -> Подключить устройство*\n\nОжидание сканирования..."
            )

            try:
                user = await qr_login.wait(timeout=60)
                await bot.send_message(event.chat_id, f"✅ **Успешно авторизован:** {user.first_name} (@{user.username})", buttons=get_main_keyboard())
            except SessionPasswordNeededError:
                auth_context['client'] = auth_client
                user_states[event.sender_id] = 'awaiting_2fa_qr'
                await bot.send_message(event.chat_id, "🔐 Отправьте ваш **2FA пароль** в чат:")
            finally:
                if auth_client.is_connected():
                    await auth_client.disconnect()

        except Exception as e:
            await bot.send_message(event.chat_id, f"❌ Ошибка QR-авторизации: {e}", buttons=get_main_keyboard())

    # --- ВЫПОЛНЕНИЕ ДЕЙСТВИЙ ---
    elif data == b"check_ban":
        await event.edit("🔄 Проверка статуса в @SpamBot...")
        res = await worker.check_ban()
        await event.edit(res, buttons=get_main_keyboard())

    elif data == b"warmup":
        await event.edit("🔥 Запуск процесса прогрева...")
        async def progress(t):
            try: await event.edit(t)
            except Exception: pass
        res = await worker.warm_up(cycles=1, progress_callback=progress)
        await event.edit(res, buttons=get_main_keyboard())

    elif data == b"parse":
        user_states[event.sender_id] = 'awaiting_parse_target'
        await event.edit("👥 Отправьте ссылку на чат/канал для парсинга:", buttons=[[Button.inline("❌ Отмена", b"main_menu")]])

    elif data == b"broadcast":
        user_states[event.sender_id] = 'awaiting_broadcast_text'
        await event.edit("✉️ Отправьте текст для рассылки:", buttons=[[Button.inline("❌ Отмена", b"main_menu")]])


# ==================== ОБРАБОТКА ТЕКСТОВОГО ВВОДА ====================

@bot.on(events.NewMessage)
async def text_input_handler(event):
    if event.sender_id != ADMIN_ID or event.text.startswith('/'):
        return

    state = user_states.get(event.sender_id)

    # 1. Ввод данных Прокси
    if state == 'awaiting_proxy_input':
        proxy_data = parse_proxy_string(event.text)
        if proxy_data:
            new_id = len(proxies_db)
            proxy_data['id'] = new_id
            proxies_db.append(proxy_data)
            
            global active_proxy_id
            active_proxy_id = new_id

            user_states.pop(event.sender_id, None)
            proto_str = "HTTP" if proxy_data['type'] == socks.HTTP else ("SOCKS5" if proxy_data['type'] == socks.SOCKS5 else "SOCKS4")
            await event.respond(
                f"✅ **Прокси [{proto_str}] успешно добавлен и включен!**\n\n"
                f"`{proxy_data['host']}:{proxy_data['port']}`", 
                buttons=get_main_keyboard()
            )
        else:
            await event.respond(
                "❌ **Неверный формат прокси!**\n\n"
                "Примеры:\n"
                "• `168.181.53.243:8000`\n"
                "• `168.181.53.243:8000:user:pass`\n"
                "• `socks5://1.2.3.4:1080`"
            )

    # 2. Номер телефона
    elif state == 'awaiting_phone':
        phone = event.text.strip()
        proxy_info = worker.get_current_proxy()
        telethon_proxy = get_telethon_proxy(proxy_info)

        msg = await event.respond("⏳ Подключение к Telegram и отправка кода...")

        try:
            auth_client = TelegramClient(USERBOT_SESSION, API_ID, API_HASH, proxy=telethon_proxy)
            await auth_client.connect()
            sent_code = await auth_client.send_code_request(phone)

            auth_context['client'] = auth_client
            auth_context['phone'] = phone
            auth_context['phone_code_hash'] = sent_code.phone_code_hash

            user_states[event.sender_id] = 'awaiting_code'
            await msg.edit("📩 **Код отправлен!** Введите код из Telegram/SMS:")
        except Exception as e:
            user_states.pop(event.sender_id, None)
            await msg.edit(f"❌ Не удалось отправить код через прокси: {e}", buttons=get_main_keyboard())

    # 3. Код из СМС/Telegram
    elif state == 'awaiting_code':
        code = event.text.strip()
        auth_client = auth_context.get('client')
        phone = auth_context.get('phone')
        phone_code_hash = auth_context.get('phone_code_hash')

        try:
            await auth_client.sign_in(phone, code, phone_code_hash=phone_code_hash)
            await auth_client.disconnect()

            user_states.pop(event.sender_id, None)
            auth_context.clear()
            await event.respond("✅ **Вход выполнен успешно!**", buttons=get_main_keyboard())

        except SessionPasswordNeededError:
            user_states[event.sender_id] = 'awaiting_2fa_phone'
            await event.respond("🔐 На аккаунте установлен **2FA пароль**. Введите его:")
        except PhoneCodeInvalidError:
            await event.respond("❌ Неверный код. Попробуйте ещё раз:")
        except Exception as e:
            user_states.pop(event.sender_id, None)
            await event.respond(f"❌ Ошибка входа: {e}", buttons=get_main_keyboard())

    # 4. Ввод 2FA пароля
    elif state in ('awaiting_2fa_phone', 'awaiting_2fa_qr'):
        password = event.text.strip()
        auth_client = auth_context.get('client')

        try:
            await auth_client.sign_in(password=password)
            await auth_client.disconnect()

            user_states.pop(event.sender_id, None)
            auth_context.clear()
            await event.respond("✅ **Пароль принят! Вход успешно завершен.**", buttons=get_main_keyboard())
        except PasswordHashInvalidError:
            await event.respond("❌ Неверный 2FA пароль. Повторите ввод:")
        except Exception as e:
            user_states.pop(event.sender_id, None)
            await event.respond(f"❌ Ошибка: {e}", buttons=get_main_keyboard())

    # 5. Парсинг
    elif state == 'awaiting_parse_target':
        target = event.text.strip()
        user_states.pop(event.sender_id, None)
        msg = await event.respond(f"⏳ Запуск парсинга с `{target}`...")
        res = await worker.parse_target(target)
        await msg.edit(res, buttons=get_main_keyboard())

    # 6. Текст для рассылки
    elif state == 'awaiting_broadcast_text':
        text = event.text
        user_states.pop(event.sender_id, None)
        msg = await event.respond("🚀 Запуск рассылки по базе...")
        
        async def progress(s):
            try: await msg.edit(s)
            except Exception: pass

        res = await worker.broadcast(text, progress_callback=progress)
        await msg.edit(res, buttons=get_main_keyboard())


# ==================== ЗАПУСК ====================
if __name__ == '__main__':
    print("[+] Бот управления запущен в Telegram...")
    bot.start(bot_token=BOT_TOKEN)
    bot.run_until_disconnected()
