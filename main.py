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
API_ID = 12345678                  # Ваш API ID
API_HASH = 'YOUR_API_HASH'          # Ваш API Hash
BOT_TOKEN = 'YOUR_BOT_TOKEN'        # Токен бота управления от @BotFather
ADMIN_ID = 123456789                # Ваш личный Telegram ID

USERBOT_SESSION = 'userbot_session'
BOT_SESSION = 'admin_bot_session'

PARSED_USERS_FILE = 'parsed_users.txt'
POPULAR_CHANNELS = ['durov', 'telegram', 'rbc_news', 'ria_realtime', 'tass_agency', 'mash']
REACTIONS = ['👍', '🔥', '👏', '❤️', '🤔', '🎉']

# ==================== БАЗА ДАННЫХ ПРОКСИ В ПАМЯТИ ====================
# Список сохраненных прокси: [{'id': 0, 'host': ..., 'port': ..., 'user': ..., 'password': ...}]
proxies_db = []
active_proxy_id = None

user_states = {}
auth_context = {}  # Контекст процесса входа (телефон, phone_hash, client и т.д.)


def get_telethon_proxy(proxy_dict):
    """Преобразование словаря прокси в формат Telethon"""
    if not proxy_dict:
        return None
    if proxy_dict.get('user') and proxy_dict.get('password'):
        return (
            socks.SOCKS5,
            proxy_dict['host'],
            proxy_dict['port'],
            True,
            proxy_dict['user'],
            proxy_dict['password']
        )
    return (socks.SOCKS5, proxy_dict['host'], proxy_dict['port'])


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
        """Инициализация клиента строго с выбранным прокси"""
        proxy_info = self.get_current_proxy()
        if not proxy_info:
            raise ValueError("⛔ Прокси не выбран или не настроен! Запуск заблокирован.")

        telethon_proxy = get_telethon_proxy(proxy_info)

        if self.client:
            if self.client.is_connected():
                await self.client.disconnect()

        self.client = TelegramClient(USERBOT_SESSION, API_ID, API_HASH, proxy=telethon_proxy)
        await self.client.connect()

        if not await self.client.is_user_authorized():
            raise PermissionError("🔒 Аккаунт не авторизован! Пройдите авторизацию через меню.")

    async def check_ban(() -> str:
        try:
            worker = UserbotWorker()
            await worker.init_client()
            spam_bot = await worker.client.get_entity('SpamBot')
            await worker.client(ReadHistoryRequest(peer=spam_bot, max_id=0))
            await worker.client.send_message(spam_bot, '/start')
            await asyncio.sleep(3)

            messages = await worker.client.get_messages(spam_bot, limit=1)
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
            return f"❌ Ошибка проверки: {e}"

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
                return "❌ Не удалось собрать юзернеймы."

            existing_users = set()
            if os.path.exists(PARSED_USERS_FILE):
                with open(PARSED_USERS_FILE, 'r', encoding='utf-8') as f:
                    existing_users = set(line.strip() for line in f if line.strip())

            all_users = existing_users.union(parsed_users)
            with open(PARSED_USERS_FILE, 'w', encoding='utf-8') as f:
                for u in all_users:
                    f.write(f"{u}\n")

            return f"✅ **Парсинг завершен!**\nСобрано: `{len(parsed_users)}`\nВсего в базе: `{len(all_users)}`"
        except (ValueError, PermissionError) as err:
            return str(err)
        except Exception as e:
            return f"❌ Ошибка при парсинге: {e}"

    async def broadcast(self, message_text: str, progress_callback=None) -> str:
        if not os.path.exists(PARSED_USERS_FILE):
            return "❌ База пользователей не найдена."

        with open(PARSED_USERS_FILE, 'r', encoding='utf-8') as f:
            users = [line.strip() for line in f if line.strip()]

        if not users:
            return "❌ База пуста."

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
                    return f"⛔ **Спамблок!** Отправка остановлена. Успешно: `{successful}`."
                except Exception:
                    pass

            return f"✅ **Рассылка завершена!** Доставлено: `{successful}/{len(users)}`"
        except (ValueError, PermissionError) as err:
            return str(err)
        except Exception as e:
            return f"❌ Ошибка рассылки: {e}"


worker = UserbotWorker()
bot = TelegramClient(BOT_SESSION, API_ID, API_HASH)


# ==================== КНОПКИ И МЕНЮ ====================

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
    for p in proxies_db:
        status_icon = "✅ " if p['id'] == active_proxy_id else ""
        label = f"{status_icon}{p['host']}:{p['port']}"
        buttons.append([Button.inline(label, f"select_proxy_{p['id']}".encode())])
    
    buttons.append([Button.inline("➕ Добавить прокси SOCKS5", b"add_proxy")])
    buttons.append([Button.inline("⬅️ Назад", b"main_menu")])
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
    await event.respond("🤖 **Панель управления Telegram Юзерботом**\n\n⚠️ *Все действия выполняются строго через выбранный прокси.*", buttons=get_main_keyboard())


@bot.on(events.CallbackQuery)
async def callback_handler(event):
    if event.sender_id != ADMIN_ID:
        return

    data = event.data

    if data == b"main_menu":
        user_states.pop(event.sender_id, None)
        await event.edit("🤖 **Главное меню**", buttons=get_main_keyboard())

    # --- МЕНЮ ПРОКСИ ---
    elif data == b"proxy_menu":
        active_p = worker.get_current_proxy()
        active_str = f"`{active_p['host']}:{active_p['port']}`" if active_p else "❌ Не выбран"
        text = f"🌐 **Настройки Прокси:**\n\nАктивный прокси: {active_str}\n\nВыберите прокси из списка ниже или добавьте новый:"
        await event.edit(text, buttons=get_proxy_keyboard())

    elif data == b"add_proxy":
        user_states[event.sender_id] = 'awaiting_proxy_input'
        await event.edit(
            "🌐 **Отправьте прокси SOCKS5 в формате:**\n\n"
            "`ip:port:login:password`\nили\n`ip:port`",
            buttons=[[Button.inline("❌ Отмена", b"proxy_menu")]]
        )

    elif data.startswith(b"select_proxy_"):
        pid = int(data.decode().split("_")[2])
        global active_proxy_id
        active_proxy_id = pid
        await event.answer("✅ Прокси успешно выбран!")
        await callback_handler(type('Event', (), {'sender_id': ADMIN_ID, 'data': b"proxy_menu", 'edit': event.edit, 'answer': event.answer})())

    # --- АВТОРИЗАЦИЯ АКТУАЛЬНОГО АККАУНТА ---
    elif data == b"auth_menu":
        if not worker.get_current_proxy():
            await event.answer("⛔ Сначала добавьте и выберите Прокси!", alert=True)
            return
        await event.edit("🔑 **Выберите способ входа в аккаунт:**\n\n*(Подключение пойдет через активный прокси)*", buttons=get_auth_keyboard())

    # Вход по номеру
    elif data == b"auth_phone":
        user_states[event.sender_id] = 'awaiting_phone'
        await event.edit("📱 Отправьте **номер телефона** аккаунта (в международном формате, например `+79991234567`):", buttons=[[Button.inline("❌ Отмена", b"auth_menu")]])

    # Вход по QR-коду
    elif data == b"auth_qr":
        proxy_info = worker.get_current_proxy()
        telethon_proxy = get_telethon_proxy(proxy_info)
        
        await event.edit("⏳ Генерация QR-кода... Подключение к Telegram...")

        auth_client = TelegramClient(USERBOT_SESSION, API_ID, API_HASH, proxy=telethon_proxy)
        await auth_client.connect()

        try:
            qr_login = await auth_client.qr_login()
            
            # Генерация QR изображения
            img = qrcode.make(qr_login.url)
            bio = io.BytesIO()
            bio.name = 'qr.png'
            img.save(bio, 'PNG')
            bio.seek(0)

            await bot.send_file(
                event.chat_id,
                bio,
                caption="🔲 **Сканируйте QR-код в приложении Telegram:**\n\n*Настройки -> Устройства -> Подключить устройство*\n\nОжидаем подтверждения..."
            )

            # Ожидание сканирования
            try:
                user = await qr_login.wait(timeout=60)
                await bot.send_message(event.chat_id, f"✅ **Успешно авторизован пользователь:** {user.first_name} (@{user.username})", buttons=get_main_keyboard())
            except SessionPasswordNeededError:
                auth_context['client'] = auth_client
                user_states[event.sender_id] = 'awaiting_2fa_qr'
                await bot.send_message(event.chat_id, "🔐 На аккаунте включен **двухфакторный пароль (2FA)**. Отправьте ваш пароль в чат:")
            finally:
                await auth_client.disconnect()

        except Exception as e:
            await bot.send_message(event.chat_id, f"❌ Ошибка генерации QR-кода: {e}", buttons=get_main_keyboard())

    # --- СТАНДАРТНЫЕ ДЕЙСТВИЯ ---
    elif data == b"check_ban":
        await event.edit("🔄 Проверка статуса аккаунта...")
        res = await UserbotWorker.check_ban()
        await event.edit(res, buttons=get_main_keyboard())

    elif data == b"warmup":
        await event.edit("🔥 Запуск прогрева аккаунта...")
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
        await event.edit("✉️ Отправьте текст сообщения для рассылки:", buttons=[[Button.inline("❌ Отмена", b"main_menu")]])


# ==================== ОБРАБОТКА ТЕКСТОВОГО ВВОДА ====================

@bot.on(events.NewMessage)
async def text_input_handler(event):
    if event.sender_id != ADMIN_ID or event.text.startswith('/'):
        return

    state = user_states.get(event.sender_id)

    # 1. Ввод Прокси
    if state == 'awaiting_proxy_input':
        parts = event.text.strip().split(':')
        if len(parts) in (2, 4):
            try:
                new_id = len(proxies_db)
                p_data = {
                    'id': new_id,
                    'host': parts[0],
                    'port': int(parts[1]),
                    'user': parts[2] if len(parts) == 4 else None,
                    'password': parts[3] if len(parts) == 4 else None
                }
                proxies_db.append(p_data)
                
                global active_proxy_id
                if active_proxy_id is None:
                    active_proxy_id = new_id

                user_states.pop(event.sender_id, None)
                await event.respond("✅ **Прокси успешно добавлен и активирован!**", buttons=get_main_keyboard())
            except ValueError:
                await event.respond("❌ Порт должен быть числом.")
        else:
            await event.respond("❌ Неверный формат. Используйте: `ip:port` или `ip:port:login:password`")

    # 2. Авторизация по номеру: Ввод номера
    elif state == 'awaiting_phone':
        phone = event.text.strip()
        proxy_info = worker.get_current_proxy()
        telethon_proxy = get_telethon_proxy(proxy_info)

        msg = await event.respond("⏳ Отправка кода подтверждения...")

        try:
            auth_client = TelegramClient(USERBOT_SESSION, API_ID, API_HASH, proxy=telethon_proxy)
            await auth_client.connect()
            sent_code = await auth_client.send_code_request(phone)

            auth_context['client'] = auth_client
            auth_context['phone'] = phone
            auth_context['phone_code_hash'] = sent_code.phone_code_hash

            user_states[event.sender_id] = 'awaiting_code'
            await msg.edit("📩 **Код отправлен!** Введите полученный код подтверждения:")
        except Exception as e:
            user_states.pop(event.sender_id, None)
            await msg.edit(f"❌ Ошибка отправки кода: {e}", buttons=get_main_keyboard())

    # 3. Авторизация по номеру: Ввод кода
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
            await event.respond("✅ **Аккаунт успешно авторизован!**", buttons=get_main_keyboard())

        except SessionPasswordNeededError:
            user_states[event.sender_id] = 'awaiting_2fa_phone'
            await event.respond("🔐 Введите ваш **двухфакторный пароль (2FA)**:")
        except PhoneCodeInvalidError:
            await event.respond("❌ Неверный код! Попробуйте ввести еще раз:")
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
            await event.respond("✅ **Пароль принят! Вход выполнен успешно.**", buttons=get_main_keyboard())
        except PasswordHashInvalidError:
            await event.respond("❌ Неверный 2FA пароль. Попробуйте еще раз:")
        except Exception as e:
            user_states.pop(event.sender_id, None)
            await event.respond(f"❌ Ошибка при вводе 2FA: {e}", buttons=get_main_keyboard())

    # 5. Ввод источника парсинга
    elif state == 'awaiting_parse_target':
        target = event.text.strip()
        user_states.pop(event.sender_id, None)
        msg = await event.respond(f"⏳ Запуск парсинга с `{target}`...")
        res = await worker.parse_target(target)
        await msg.edit(res, buttons=get_main_keyboard())

    # 6. Ввод текста для рассылки
    elif state == 'awaiting_broadcast_text':
        text = event.text
        user_states.pop(event.sender_id, None)
        msg = await event.respond("🚀 Запуск рассылки...")
        
        async def progress(s):
            try: await msg.edit(s)
            except Exception: pass

        res = await worker.broadcast(text, progress_callback=progress)
        await msg.edit(res, buttons=get_main_keyboard())


# ==================== ЗАПУСК БОТА ====================
if __name__ == '__main__':
    print("[+] Панель управления Telegram запущена...")
    bot.start(bot_token=BOT_TOKEN)
    bot.run_until_disconnected()
