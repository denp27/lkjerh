import asyncio
import os
import random
import re
import io
import qrcode
import socks
from datetime import datetime, timedelta, timezone

from telethon import TelegramClient, events, Button
from telethon.errors import (
    PeerFloodError,
    UserAlreadyParticipantError,
    SessionPasswordNeededError,
    PhoneCodeInvalidError,
    PasswordHashInvalidError,
    MessageNotModifiedError
)
from telethon.tl.functions.messages import SendReactionRequest, ReadHistoryRequest, ImportChatInviteRequest
from telethon.tl.types import ReactionEmoji

# ==================== КОНФИГУРАЦИЯ ====================
API_ID = 12345678                  # ⚠️ ЗАМЕНИТЕ на ваш api_id (число)
API_HASH = 'YOUR_API_HASH'          # ⚠️ ЗАМЕНИТЕ на ваш api_hash (строка)
BOT_TOKEN = 'YOUR_BOT_TOKEN'        # ⚠️ ЗАМЕНИТЕ на токен управляющего бота
ADMIN_ID = 123456789                # ⚠️ ЗАМЕНИТЕ на ваш Telegram ID (число)

BOT_SESSION = 'admin_bot_session'
PARSED_USERS_FILE = 'parsed_users.txt'
POPULAR_CHANNELS = ['durov', 'telegram', 'rbc_news', 'ria_realtime', 'tass_agency', 'mash']
REACTIONS = ['👍', '🔥', '👏', '❤️', '🤔', '🎉']
PHRASES = ["Как сам?", "Йо", "Привет!", "Что делаешь?", "Как дела?", "Норм всё", "Давай позже", "Ок"]

# ==================== БАЗЫ ДАННЫХ В ПАМЯТИ ====================
proxies_db = []
accounts_db = []
active_account_name = None

user_states = {}
auth_context = {}


# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ПРОКСИ ====================

def get_proxy_usage_count(proxy_id):
    """Подсчет количества аккаунтов, привязанных к конкретному прокси"""
    return sum(1 for acc in accounts_db if acc.get('proxy_id') == proxy_id)

def get_best_available_proxy():
    """Автовыбор прокси (макс 2 аккаунта на 1 прокси)"""
    free_proxies = []
    half_busy_proxies = []

    for p in proxies_db:
        count = get_proxy_usage_count(p['id'])
        if count == 0:
            free_proxies.append(p)
        elif count == 1:
            half_busy_proxies.append(p)

    if free_proxies:
        return random.choice(free_proxies)
    if half_busy_proxies:
        return random.choice(half_busy_proxies)

    return None

def get_telethon_proxy(proxy_dict):
    """Преобразование словаря прокси в формат Telethon / python-socks"""
    if not proxy_dict:
        return None

    p_type = proxy_dict.get('type', socks.HTTP)
    if p_type == socks.SOCKS5:
        proxy_type = 'socks5'
    elif p_type == socks.SOCKS4:
        proxy_type = 'socks4'
    else:
        proxy_type = 'http'

    return {
        'proxy_type': proxy_type,
        'addr': proxy_dict['host'],
        'port': proxy_dict['port'],
        'username': proxy_dict.get('user'),
        'password': proxy_dict.get('password'),
        'rdns': True
    }

def parse_proxy_string(raw_text: str):
    """Парсер прокси формата user:pass@ip:port или ip:port:user:pass или ip:port"""
    text = raw_text.strip()
    p_type = None

    if text.startswith("socks5://"):
        p_type = socks.SOCKS5
        text = text.replace("socks5://", "")
    elif text.startswith("socks4://"):
        p_type = socks.SOCKS4
        text = text.replace("socks4://", "")
    elif text.startswith("http://") or text.startswith("https://"):
        p_type = socks.HTTP
        text = re.sub(r'^https?://', '', text)

    user = None
    password = None

    if '@' in text:
        auth_part, net_part = text.split('@', 1)
        if ':' in auth_part:
            user, password = auth_part.split(':', 1)
        parts = net_part.split(':')
    else:
        parts = text.split(':')

    if len(parts) == 2:
        host = parts[0]
        try:
            port = int(parts[1])
        except ValueError:
            return None
    elif len(parts) == 4:
        host = parts[0]
        try:
            port = int(parts[1])
        except ValueError:
            return None
        user = parts[2]
        password = parts[3]
    else:
        return None

    if p_type is None:
        if port in (1080, 1081, 9050, 9150):
            p_type = socks.SOCKS5
        else:
            p_type = socks.HTTP

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

    def get_account_data(self, acc_name=None):
        global active_account_name, accounts_db
        target_name = acc_name or active_account_name
        for acc in accounts_db:
            if acc['name'] == target_name:
                return acc
        return None

    def get_proxy_for_account(self, acc_name=None):
        acc = self.get_account_data(acc_name)
        if not acc or acc['proxy_id'] is None:
            return None
        for p in proxies_db:
            if p['id'] == acc['proxy_id']:
                return p
        return None

    async def init_client(self, acc_name=None):
        acc = self.get_account_data(acc_name)
        if not acc:
            raise ValueError("⛔ Аккаунт не выбран! Выберите активный аккаунт в меню.")

        proxy_info = self.get_proxy_for_account(acc['name'])
        if not proxy_info:
            raise ValueError(f"⛔ Для аккаунта {acc['name']} не привязан прокси!")

        telethon_proxy = get_telethon_proxy(proxy_info)

        if self.client and self.client.is_connected():
            await self.client.disconnect()

        self.client = TelegramClient(acc['session'], API_ID, API_HASH, proxy=telethon_proxy)
        await self.client.connect()

        if not await self.client.is_user_authorized():
            raise PermissionError(f"🔒 Аккаунт {acc['name']} не авторизован! Выполните вход.")

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
            return f"❌ Ошибка проверки: {e}"

    async def warm_up(self, total_stages=6, progress_callback=None):
        """Прогрев аккаунта: 6 заходов со случайными интервалами и детальным отчетом"""
        try:
            await self.init_client()
            acc = self.get_account_data()
            phone_name = acc.get('phone') or acc['name']

            stats = {
                'subs': 0,
                'archive': 0,
                'reads': 0,
                'reactions': 0,
                'comments': 0,
                'pm': 0
            }

            for stage in range(1, total_stages + 1):
                logs = []

                # --- 1. Чтение каналов ---
                channels_to_read = random.sample(POPULAR_CHANNELS, min(5, len(POPULAR_CHANNELS)))
                for ch in channels_to_read:
                    try:
                        entity = await self.client.get_entity(ch)
                        messages = await self.client.get_messages(entity, limit=3)
                        if messages:
                            await self.client(ReadHistoryRequest(peer=entity, max_id=messages[0].id))
                            stats['reads'] += 1
                            logs.append(f"👀 Почитал @{ch}")
                        await asyncio.sleep(random.randint(2, 5))
                    except Exception:
                        pass

                # --- 2. Реакции ---
                target_react_channel = random.choice(channels_to_read)
                try:
                    entity = await self.client.get_entity(target_react_channel)
                    messages = await self.client.get_messages(entity, limit=7)
                    for msg in messages:
                        if random.random() < 0.6:
                            emoji = random.choice(REACTIONS)
                            try:
                                await self.client(SendReactionRequest(
                                    peer=entity,
                                    msg_id=msg.id,
                                    reaction=[ReactionEmoji(emoticon=emoji)]
                                ))
                                stats['reactions'] += 1
                                logs.append(f"👍 Реакция в @{target_react_channel}")
                                await asyncio.sleep(random.randint(3, 6))
                            except Exception:
                                pass
                except Exception:
                    pass

                # --- 3. Имитация ЛС (между своими аккаунтами) ---
                other_accs = [a for a in accounts_db if a['name'] != acc['name']]
                if other_accs:
                    peer_acc = random.choice(other_accs)
                    target_phone = peer_acc.get('phone') or peer_acc['name']
                    phrase = random.choice(PHRASES)

                    try:
                        await self.client.send_message(peer_acc['session'], phrase)
                        stats['pm'] += 1
                        logs.append(f"✉️ {phone_name} ➔ {target_phone}: «{phrase}»")
                    except Exception:
                        pass

                # Интервал до следующего захода (установлен от 20 до 45 минут для тестов)
                delay_seconds = random.randint(1200, 2700)
                next_time_utc = (datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)).strftime("%d.%m %H:%M UTC")

                log_text = "\n".join(logs)
                report = (
                    f"🔥 **Прогрев {phone_name} · заход {stage}/{total_stages}**\n\n"
                    f"{log_text}\n\n"
                    f"Подписок: {stats['subs']} (архив {stats['archive']}) · чтение: {stats['reads']} · "
                    f"реакции: {stats['reactions']} · комментарии: {stats['comments']} · ЛС: {stats['pm']}\n"
                    f"**Заход {stage}/{total_stages} готов. Следующий заход: {next_time_utc}**"
                )

                if progress_callback:
                    await progress_callback(report)

                if stage < total_stages:
                    await asyncio.sleep(delay_seconds)

            return f"✅ **Полный прогрев ({total_stages} заходов) для {phone_name} завершен!**"

        except (ValueError, PermissionError) as err:
            return str(err)
        except Exception as e:
            acc = self.get_account_data()
            p_name = acc.get('phone') or acc['name'] if acc else "Аккаунт"
            return f"⚠️ **{p_name} — не подключился**\nОшибка: {e}"

    async def parse_target(self, target: str, limit: int = 300) -> str:
        try:
            await self.init_client()
            entity = None

            if "t.me/+" in target or "joinchat" in target:
                hash_match = re.search(r'(?:joinchat/|\+|\/join\/)([a-zA-Z0-9_-]+)', target)
                if not hash_match:
                    return "❌ Некорректный формат приватной ссылки."

                invite_hash = hash_match.group(1)
                try:
                    result = await self.client(ImportChatInviteRequest(invite_hash))
                    if hasattr(result, 'chats') and result.chats:
                        entity = result.chats[0]
                    elif hasattr(result, 'chat'):
                        entity = result.chat
                    else:
                        dialogs = await self.client.get_dialogs(limit=5)
                        entity = dialogs[0].entity
                except UserAlreadyParticipantError:
                    entity = await self.client.get_entity(target)
            else:
                entity = await self.client.get_entity(target)

            if not entity:
                return "❌ Не удалось получить доступ к чату."

            parsed_users = set()
            try:
                async for user in self.client.iter_participants(entity, limit=limit):
                    if user.username and not user.bot:
                        clean_uname = user.username.lstrip('@')
                        parsed_users.add(f"@{clean_uname}")
            except Exception:
                pass

            if len(parsed_users) < 5:
                try:
                    async for message in self.client.iter_messages(entity, limit=200):
                        if message.sender_id and getattr(message.sender, 'username', None):
                            if not message.sender.bot:
                                clean_uname = message.sender.username.lstrip('@')
                                parsed_users.add(f"@{clean_uname}")
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
                    await progress_callback(f"✉️ **Рассылка:** [{index+1}/{len(users)}] для {username}...")

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
        [Button.inline("📱 Аккаунты и Сессии", b"acc_menu")],
        [Button.inline("🔍 Проверить бан (@SpamBot)", b"check_ban")],
        [Button.inline("🔥 Запустить прогрев", b"warmup"), Button.inline("👥 Парсинг", b"parse")],
        [Button.inline("✉️ Рассылка", b"broadcast")],
        [Button.inline("📂 База пользователей", b"db_menu"), Button.inline("🌐 Прокси", b"proxy_menu")]
    ]


def get_accounts_keyboard():
    buttons = []
    for acc in accounts_db:
        status = "✅ " if acc['name'] == active_account_name else ""
        
        p_str = "Без прокси"
        if acc['proxy_id'] is not None:
            p = next((p for p in proxies_db if p['id'] == acc['proxy_id']), None)
            if p:
                p_str = f"{p['host']}:{p['port']}"

        label = f"{status}{acc['name']} ({p_str})"
        
        buttons.append([
            Button.inline(label, f"select_acc_{acc['name']}".encode()),
            Button.inline("❌", f"delete_acc_{acc['name']}".encode())
        ])

    buttons.append([Button.inline("➕ Добавить Новый Аккаунт", b"add_acc")])
    buttons.append([Button.inline("⬅️ Назад в меню", b"main_menu")])
    return buttons


def get_proxy_keyboard():
    buttons = []
    type_names = {socks.HTTP: "HTTP", socks.SOCKS5: "SOCKS5", socks.SOCKS4: "SOCKS4"}

    for p in proxies_db:
        proto = type_names.get(p['type'], 'PROXY')
        usage = get_proxy_usage_count(p['id'])
        label = f"[{proto}] {p['host']}:{p['port']} ({usage}/2 акк.)"
        buttons.append([Button.inline(label, f"proxy_info_{p['id']}".encode())])

    buttons.append([Button.inline("➕ Добавить прокси", b"add_proxy")])
    buttons.append([Button.inline("⬅️ Назад в меню", b"main_menu")])
    return buttons


def get_proxy_select_keyboard_for_acc(acc_name):
    buttons = []
    buttons.append([Button.inline("🎲 Автовыбор прокси (свободный/1 акк)", f"auto_bind_{acc_name}".encode())])
    
    for p in proxies_db:
        proto = "HTTP" if p['type'] == socks.HTTP else "SOCKS5"
        usage = get_proxy_usage_count(p['id'])
        status_tag = f"[{usage}/2]" if usage < 2 else "[2/2 MAX]"
        label = f"{status_tag} [{proto}] {p['host']}:{p['port']}"
        buttons.append([Button.inline(label, f"bind_p_{acc_name}_{p['id']}".encode())])

    buttons.append([Button.inline("❌ Отмена", b"acc_menu")])
    return buttons


def get_auth_method_keyboard(acc_name):
    return [
        [Button.inline("📲 Вход по номеру телефона", f"auth_phone_{acc_name}".encode())],
        [Button.inline("🔲 Вход по QR-коду", f"auth_qr_{acc_name}".encode())],
        [Button.inline("⬅️ Отмена", b"acc_menu")]
    ]


def get_db_keyboard():
    return [
        [Button.inline("📥 Скачать файл базы (.txt)", b"download_db")],
        [Button.inline("➕ Добавить юзеров вручную", b"add_manual_users")],
        [Button.inline("🗑 Очистить всю базу", b"clear_db")],
        [Button.inline("⬅️ Назад в меню", b"main_menu")]
    ]


async def show_acc_menu(event):
    text = "📱 **Управление аккаунтами и привязками:**\n\n"
    if not accounts_db:
        text += "Аккаунтов пока нет. Нажмите **Добавить Новый Аккаунт**."
    else:
        text += f"Активный аккаунт: `{active_account_name or 'Не выбран'}`\n\nСписок аккаунтов (нажмите ❌ для удаления):"
    try:
        await event.edit(text, buttons=get_accounts_keyboard())
    except MessageNotModifiedError:
        pass


# ==================== ОБРАБОТКА КОМАНД И КНОПОК ====================

@bot.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    if event.sender_id != ADMIN_ID:
        return
    await event.respond(
        "🤖 **Панель управления Юзерботами**\n\n"
        "Управляйте аккаунтами, связывайте их с персональными прокси и работайте с базой парсинга.",
        buttons=get_main_keyboard()
    )


@bot.on(events.CallbackQuery)
async def callback_handler(event):
    global active_account_name
    if event.sender_id != ADMIN_ID:
        return

    data = event.data

    try:
        if data == b"main_menu":
            user_states.pop(event.sender_id, None)
            await event.edit("🤖 **Главное меню**", buttons=get_main_keyboard())

        elif data == b"acc_menu":
            await show_acc_menu(event)

        elif data == b"add_acc":
            user_states[event.sender_id] = 'awaiting_acc_name'
            await event.edit("📝 Введите **название для нового аккаунта** (например: `Account_1`):")

        elif data.startswith(b"select_acc_"):
            acc_name = data.decode().split("_")[2]
            active_account_name = acc_name
            await event.answer(f"✅ Выбран аккаунт: {acc_name}")
            await show_acc_menu(event)

        elif data.startswith(b"delete_acc_"):
            acc_name = data.decode().replace("delete_acc_", "")
            target_acc = next((a for a in accounts_db if a['name'] == acc_name), None)
            if target_acc:
                session_file = f"{target_acc['session']}.session"
                if os.path.exists(session_file):
                    try:
                        os.remove(session_file)
                    except Exception:
                        pass
                
                accounts_db.remove(target_acc)
                if active_account_name == acc_name:
                    active_account_name = accounts_db[0]['name'] if accounts_db else None

                await event.answer(f"🗑 Аккаунт {acc_name} удален!")
            else:
                await event.answer("❌ Аккаунт не найден!")

            await show_acc_menu(event)

        elif data.startswith(b"auto_bind_"):
            acc_name = data.decode().replace("auto_bind_", "")
            best_proxy = get_best_available_proxy()

            if not best_proxy:
                await event.answer("⚠️ Все прокси заполнены! (Максимум 2 аккаунта на 1 прокси). Добавьте новый прокси.", alert=True)
                return

            for acc in accounts_db:
                if acc['name'] == acc_name:
                    acc['proxy_id'] = best_proxy['id']
                    break

            usage = get_proxy_usage_count(best_proxy['id'])
            await event.edit(
                f"✅ **Автоматически выбран прокси:** `{best_proxy['host']}:{best_proxy['port']}` (Загрузка: {usage}/2)\n\n"
                f"Выберите способ входа для **{acc_name}**:",
                buttons=get_auth_method_keyboard(acc_name)
            )

        elif data.startswith(b"bind_p_"):
            parts = data.decode().split("_")
            acc_name = parts[2]
            proxy_id = int(parts[3])

            if get_proxy_usage_count(proxy_id) >= 2:
                await event.answer("❌ На этот прокси нельзя привязать более 2 аккаунтов!", alert=True)
                return

            for acc in accounts_db:
                if acc['name'] == acc_name:
                    acc['proxy_id'] = proxy_id
                    break

            await event.edit(
                f"✅ **Прокси привязан к {acc_name}!**\n\nВыберите способ входа:",
                buttons=get_auth_method_keyboard(acc_name)
            )

        elif data.startswith(b"auth_phone_"):
            acc_name = data.decode().replace("auth_phone_", "")
            auth_context['target_acc'] = acc_name
            user_states[event.sender_id] = 'awaiting_phone_number'
            await event.edit(f"📲 Введите **номер телефона** для аккаунта `{acc_name}` (в международном формате, например: `+79991234567`):")

        elif data.startswith(b"auth_qr_"):
            acc_name = data.decode().replace("auth_qr_", "")
            acc_data = worker.get_account_data(acc_name)
            proxy_info = worker.get_proxy_for_account(acc_name)

            if not proxy_info:
                await event.edit("❌ К аккаунту не привязан прокси!")
                return

            telethon_proxy = get_telethon_proxy(proxy_info)
            await event.edit("⏳ Подключение к Telegram...")

            auth_client = TelegramClient(acc_data['session'], API_ID, API_HASH, proxy=telethon_proxy)
            try:
                await auth_client.connect()
            except Exception as e:
                await event.edit(f"❌ Не удалось подключиться через прокси: `{e}`")
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
                    caption=f"🔲 **QR-код для аккаунта {acc_name}:**\nСканируйте в приложении Telegram."
                )

                try:
                    user = await qr_login.wait(timeout=60)
                    acc_data['phone'] = getattr(user, 'phone', None)
                    await bot.send_message(event.chat_id, f"✅ **Успешно вошли:** {user.first_name} (@{user.username})", buttons=get_main_keyboard())
                except SessionPasswordNeededError:
                    auth_context['client'] = auth_client
                    user_states[event.sender_id] = 'awaiting_2fa'
                    await bot.send_message(event.chat_id, "🔐 Введите 2FA пароль:")
                finally:
                    if auth_client.is_connected():
                        await auth_client.disconnect()
            except Exception as e:
                await bot.send_message(event.chat_id, f"❌ Ошибка QR-авторизации: {e}")

        elif data == b"db_menu":
            total_count = 0
            if os.path.exists(PARSED_USERS_FILE):
                with open(PARSED_USERS_FILE, 'r', encoding='utf-8') as f:
                    total_count = len([line for line in f if line.strip()])

            await event.edit(
                f"📂 **Управление Базой Пользователей**\n\n"
                f"Всего юзеров в базе: `{total_count}` шт.",
                buttons=get_db_keyboard()
            )

        elif data == b"download_db":
            if not os.path.exists(PARSED_USERS_FILE):
                await event.answer("❌ База пуста!", alert=True)
                return
            await bot.send_file(event.chat_id, PARSED_USERS_FILE, caption="📂 Файл базы спаршенных пользователей.")

        elif data == b"add_manual_users":
            user_states[event.sender_id] = 'awaiting_manual_users'
            await event.edit("📝 Отправьте список **юзернеймов** (по одному на строку или через пробел):")

        elif data == b"clear_db":
            if os.path.exists(PARSED_USERS_FILE):
                os.remove(PARSED_USERS_FILE)
            await event.answer("🗑 База полностью очищена!")
            await event.edit(
                "📂 **Управление Базой Пользователей**\n\nВсего юзеров в базе: `0` шт.",
                buttons=get_db_keyboard()
            )

        elif data == b"proxy_menu":
            await event.edit("🌐 **Список сохраненных Прокси (Макс. 2 акк./прокси):**", buttons=get_proxy_keyboard())

        elif data == b"add_proxy":
            user_states[event.sender_id] = 'awaiting_proxy_input'
            await event.edit(
                "🌐 **Отправьте прокси в любом из форматов:**\n\n"
                "• `168.181.53.243:8000`\n"
                "• `168.181.53.243:8000:user:pass`\n"
                "• `socks5://192.168.1.1:1080:user:pass`",
                buttons=[[Button.inline("❌ Отмена", b"proxy_menu")]]
            )

        elif data == b"check_ban":
            await event.edit("🔄 Проверка статуса в @SpamBot...")
            res = await worker.check_ban()
            await event.edit(res, buttons=get_main_keyboard())

        elif data == b"warmup":
            await event.edit("🔥 Запуск прогрева (6 этапов)...")
            async def progress(t):
                try: await event.edit(t)
                except Exception: pass
            res = await worker.warm_up(total_stages=6, progress_callback=progress)
            await event.edit(res, buttons=get_main_keyboard())

        elif data == b"parse":
            user_states[event.sender_id] = 'awaiting_parse_target'
            await event.edit("👥 Отправьте ссылку на чат/канал для парсинга:")

        elif data == b"broadcast":
            user_states[event.sender_id] = 'awaiting_broadcast_text'
            await event.edit("✉️ Отправьте текст для рассылки:")

    except MessageNotModifiedError:
        pass


# ==================== ОБРАБОТКА ВВОДА ТЕКСТА ====================

@bot.on(events.NewMessage)
async def text_input_handler(event):
    global active_account_name

    if event.sender_id != ADMIN_ID or event.text.startswith('/'):
        return

    state = user_states.get(event.sender_id)

    if state == 'awaiting_acc_name':
        acc_name = event.text.strip().replace(" ", "_")
        session_file = f"session_{acc_name}"

        new_acc = {
            'name': acc_name,
            'session': session_file,
            'proxy_id': None,
            'phone': None
        }
        accounts_db.append(new_acc)

        active_account_name = acc_name
        user_states.pop(event.sender_id, None)

        if not proxies_db:
            await event.respond(
                f"✅ Аккаунт `{acc_name}` создан!\n\n⚠️ **Нет доступных прокси.** Сначала добавьте прокси в меню Прокси.",
                buttons=get_main_keyboard()
            )
        else:
            await event.respond(
                f"✅ Аккаунт `{acc_name}` создан!\n\nВыберите прокси вручную или используйте Автовыбор:",
                buttons=get_proxy_select_keyboard_for_acc(acc_name)
            )

    elif state == 'awaiting_phone_number':
        phone = event.text.strip().replace(" ", "")
        acc_name = auth_context.get('target_acc')
        acc_data = worker.get_account_data(acc_name)
        proxy_info = worker.get_proxy_for_account(acc_name)

        telethon_proxy = get_telethon_proxy(proxy_info)
        msg = await event.respond("⏳ Подключение через прокси и отправка кода...")

        auth_client = TelegramClient(acc_data['session'], API_ID, API_HASH, proxy=telethon_proxy)
        try:
            await auth_client.connect()
            sent_code = await auth_client.send_code_request(phone)

            acc_data['phone'] = phone
            auth_context['client'] = auth_client
            auth_context['phone'] = phone
            auth_context['phone_code_hash'] = sent_code.phone_code_hash

            user_states[event.sender_id] = 'awaiting_phone_code'
            await msg.edit(f"📩 Код отправлен на номер `{phone}`.\n\nВведите **5-значный код** из сообщения:")
        except Exception as e:
            user_states.pop(event.sender_id, None)
            await msg.edit(f"❌ Ошибка отправки кода: `{e}`", buttons=get_main_keyboard())

    elif state == 'awaiting_phone_code':
        code = event.text.strip().replace("-", "").replace(" ", "")
        auth_client = auth_context.get('client')
        phone = auth_context.get('phone')
        phone_code_hash = auth_context.get('phone_code_hash')

        msg = await event.respond("⏳ Проверка кода...")
        try:
            user = await auth_client.sign_in(phone, code, phone_code_hash=phone_code_hash)
            await auth_client.disconnect()

            user_states.pop(event.sender_id, None)
            auth_context.clear()
            await msg.edit(f"✅ **Успешно авторизован!** Имя: {user.first_name} (@{user.username})", buttons=get_main_keyboard())

        except SessionPasswordNeededError:
            user_states[event.sender_id] = 'awaiting_2fa'
            await msg.edit("🔐 На аккаунте включен 2FA пароль. Введите ваш **пароль Двухшаговой проверки**:")

        except PhoneCodeInvalidError:
            await msg.edit("❌ **Неверный код!** Попробуйте ввести код еще раз:")

        except Exception as e:
            await auth_client.disconnect()
            user_states.pop(event.sender_id, None)
            auth_context.clear()
            await msg.edit(f"❌ Ошибка входа: `{e}`", buttons=get_main_keyboard())

    elif state == 'awaiting_2fa':
        password = event.text.strip()
        auth_client = auth_context.get('client')
        msg = await event.respond("⏳ Проверка 2FA пароля...")

        try:
            user = await auth_client.sign_in(password=password)
            await auth_client.disconnect()

            user_states.pop(event.sender_id, None)
            auth_context.clear()
            await msg.edit(f"✅ **Успешно авторизован с 2FA!** Имя: {user.first_name} (@{user.username})", buttons=get_main_keyboard())

        except PasswordHashInvalidError:
            await msg.edit("❌ **Неверный 2FA пароль!** Попробуйте еще раз:")

        except Exception as e:
            await auth_client.disconnect()
            user_states.pop(event.sender_id, None)
            auth_context.clear()
            await msg.edit(f"❌ Ошибка 2FA авторизации: `{e}`", buttons=get_main_keyboard())

    elif state == 'awaiting_proxy_input':
        proxy_data = parse_proxy_string(event.text)
        if proxy_data:
            new_id = len(proxies_db)
            proxy_data['id'] = new_id
            proxies_db.append(proxy_data)

            user_states.pop(event.sender_id, None)
            await event.respond("✅ **Прокси успешно сохранен!**", buttons=get_main_keyboard())
        else:
            await event.respond("❌ Неверный формат прокси. Повторите ввод:")

    elif state == 'awaiting_manual_users':
        raw_users = re.findall(r'@?([a-zA-Z0-9_]{5,32})', event.text)
        if raw_users:
            existing_users = set()
            if os.path.exists(PARSED_USERS_FILE):
                with open(PARSED_USERS_FILE, 'r', encoding='utf-8') as f:
                    existing_users = set(line.strip() for line in f if line.strip())

            new_users = {f"@{u.lstrip('@')}" for u in raw_users}
            all_users = existing_users.union(new_users)

            with open(PARSED_USERS_FILE, 'w', encoding='utf-8') as f:
                for u in all_users:
                    f.write(f"{u}\n")

            user_states.pop(event.sender_id, None)
            await event.respond(f"✅ Добавлено новых юзеров: `{len(new_users)}`\nВсего в базе: `{len(all_users)}`", buttons=get_main_keyboard())
        else:
            await event.respond("❌ Юзернеймы не найдены в тексте.")

    elif state == 'awaiting_parse_target':
        target = event.text.strip()
        user_states.pop(event.sender_id, None)
        msg = await event.respond(f"⏳ Парсинг `{target}`...")
        res = await worker.parse_target(target)
        await msg.edit(res, buttons=get_main_keyboard())

    elif state == 'awaiting_broadcast_text':
        text = event.text
        user_states.pop(event.sender_id, None)
        msg = await event.respond("🚀 Запуск рассылки...")

        async def progress(s):
            try: await msg.edit(s)
            except Exception: pass

        res = await worker.broadcast(text, progress_callback=progress)
        await msg.edit(res, buttons=get_main_keyboard())


# ==================== ЗАПУСК ====================
if __name__ == '__main__':
    print("[+] Панель Юзерботов запущена...")
    bot.start(bot_token=BOT_TOKEN)
    bot.run_until_disconnected()
