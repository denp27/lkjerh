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
from telethon.tl.functions.messages import SendReactionRequest, ReadHistoryRequest, ImportChatInviteRequest, SendVoteRequest
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.types import ReactionEmoji

# ==================== КОНФИГУРАЦИЯ ====================
API_ID =                  # ⚠️ ЗАМЕНИТЕ на ваш api_id (число)
API_HASH = ''          # ⚠️ ЗАМЕНИТЕ на ваш api_hash (строка)
BOT_TOKEN = ''        # ⚠️ ЗАМЕНИТЕ на токен управляющего бота
ADMIN_ID =                 # ⚠️ ЗАМЕНИТЕ на ваш Telegram ID (число) (доступен только он)

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
            phone_label = acc.get('phone') or acc['name']
            raise PermissionError(f"❌ Не удалось зайти по этому номеру ({phone_label}). Аккаунт не авторизован.")

    async def check_ban(self, acc_name=None) -> str:
        try:
            acc = self.get_account_data(acc_name)
            if not acc:
                return "⛔ Аккаунт не выбран!"

            proxy_info = self.get_proxy_for_account(acc_name)
            if not proxy_info:
                return f"❌ Для аккаунта {acc['name']} не привязан прокси!"

            telethon_proxy = get_telethon_proxy(proxy_info)
            temp_client = TelegramClient(acc['session'], API_ID, API_HASH, proxy=telethon_proxy)
            
            await temp_client.connect()
            if not await temp_client.is_user_authorized():
                await temp_client.disconnect()
                return "❌ Аккаунт не авторизован (сессия недействительна)."

            try:
                spam_bot = await temp_client.get_entity('SpamBot')
                await temp_client(ReadHistoryRequest(peer=spam_bot, max_id=0))
                await temp_client.send_message(spam_bot, '/start')
                await asyncio.sleep(3)

                messages = await temp_client.get_messages(spam_bot, limit=1)
                await temp_client.disconnect()

                if not messages:
                    return "❓ Нет ответа от SpamBot."

                text = messages[0].text
                if any(p in text.lower() for p in ["good news", "свободен", "никаких ограничений"]):
                    return "✅ **Аккаунт чист** (спамблока нет)."
                else:
                    return f"⚠️ **Статус ограничения:**\n\n{text}"
            except Exception as e:
                if temp_client.is_connected():
                    await temp_client.disconnect()
                return f"❌ Ошибка запроса к SpamBot: {e}"

        except Exception as e:
            return f"❌ Ошибка проверки: {e}"

    async def fetch_last_login_code(self, acc_name) -> str:
        """Получение последнего кода от официального аккаунта Telegram (777000) для конкретной сессии"""
        try:
            acc = self.get_account_data(acc_name)
            if not acc:
                return "❌ Аккаунт не найден."

            proxy_info = self.get_proxy_for_account(acc_name)
            if not proxy_info:
                return "❌ Для аккаунта не привязан прокси! Сначала привяжите прокси."

            telethon_proxy = get_telethon_proxy(proxy_info)
            temp_client = TelegramClient(acc['session'], API_ID, API_HASH, proxy=telethon_proxy)
            
            await temp_client.connect()
            if not await temp_client.is_user_authorized():
                await temp_client.disconnect()
                return "❌ Аккаунт не авторизован (сессия слетела или неактивна)."

            try:
                telegram_entity = await temp_client.get_entity(777000)
                messages = await temp_client.get_messages(telegram_entity, limit=5)
                
                await temp_client.disconnect()

                if not messages:
                    return "📭 Нет входящих сообщений от официального сервиса Telegram (777000)."

                result_lines = [f"🔑 **Последние коды авторизации для профиля `{acc_name}`:**\n"]
                found_code = False

                for msg in messages:
                    match = re.search(r'\b(\d{5,6})\b', msg.text)
                    if match:
                        found_code = True
                        result_lines.append(f"• Код: `{match.group(1)}`\n  _Текст:_ {msg.text[:100]}\n")

                if not found_code:
                    return "📭 В последних сообщениях от Telegram коды авторизации не обнаружены."

                return "\n".join(result_lines)
            except Exception as e:
                if temp_client.is_connected():
                    await temp_client.disconnect()
                return f"❌ Не удалось прочитать сообщения от Telegram: {e}"

        except Exception as e:
            return f"❌ Ошибка подключения: {e}"

    async def run_single_warmup(self, acc_name, total_stages=6, progress_callback=None):
        global active_account_name
        original_active = active_account_name
        active_account_name = acc_name

        try:
            await self.init_client(acc_name)
            acc = self.get_account_data(acc_name)
            phone_name = acc.get('phone') or acc['name']

            stats = {
                'subs': 0, 'archive': 0, 'reads': 0,
                'reactions': 0, 'polls': 0, 'saved': 0, 'pm': 0
            }

            WARMUP_CHANNELS = POPULAR_CHANNELS + ['ntvnews', 'bbcrussian', 'techcult', 'meduzalive', 'lentaruofficial']

            for stage in range(1, total_stages + 1):
                logs = []
                channels_to_interact = random.sample(WARMUP_CHANNELS, min(3, len(WARMUP_CHANNELS)))
                
                for ch in channels_to_interact:
                    try:
                        entity = await self.client.get_entity(ch)
                        
                        # 1. Случайная подписка
                        if random.random() < 0.6:
                            try:
                                await self.client(JoinChannelRequest(entity))
                                stats['subs'] += 1
                                stats['archive'] += 1
                                logs.append(f"✅ Подписка + архив @{ch}")
                                await asyncio.sleep(random.randint(3, 7))
                            except Exception:
                                pass

                        # 2. Имитация чтения ленты
                        messages = await self.client.get_messages(entity, limit=random.randint(5, 10))
                        if messages:
                            await self.client(ReadHistoryRequest(peer=entity, max_id=messages[0].id))
                            stats['reads'] += 1
                            logs.append(f"👀 Просмотр ленты @{ch}")
                            await asyncio.sleep(random.randint(4, 8))

                            # 3. Случайная реакция
                            target_msg = random.choice(messages)
                            if random.random() < 0.7:
                                emoji = random.choice(REACTIONS)
                                try:
                                    await self.client(SendReactionRequest(
                                        peer=entity,
                                        msg_id=target_msg.id,
                                        reaction=[ReactionEmoji(emoticon=emoji)]
                                    ))
                                    stats['reactions'] += 1
                                    logs.append(f"👍 Реакция {emoji} в @{ch}")
                                    await asyncio.sleep(random.randint(2, 5))
                                except Exception:
                                    pass

                            # 4. Сохранение в Избранное
                            if random.random() < 0.3:
                                try:
                                    await self.client.forward_messages('me', target_msg)
                                    stats['saved'] += 1
                                    logs.append(f"📥 Сохранение поста из @{ch} в Избранное")
                                    await asyncio.sleep(random.randint(3, 6))
                                except Exception:
                                    pass

                            # 5. Участие в опросе
                            for msg in messages:
                                if getattr(msg, 'media', None) and hasattr(msg.media, 'poll'):
                                    try:
                                        poll = msg.media.poll
                                        if not poll.closed and poll.answers:
                                            ans_idx = random.randint(0, len(poll.answers) - 1)
                                            await self.client(SendVoteRequest(
                                                peer=entity,
                                                msg_id=msg.id,
                                                options=[poll.answers[ans_idx].option]
                                            ))
                                            stats['polls'] += 1
                                            logs.append(f"📊 Участие в опросе (@{ch})")
                                            await asyncio.sleep(3)
                                            break
                                    except Exception:
                                        pass

                    except Exception:
                        pass

                # 6. Живое общение в ЛС с другими своими аккаунтами
                other_accs = [a for a in accounts_db if a['name'] != acc_name]
                if other_accs and random.random() < 0.8:
                    peer_acc = random.choice(other_accs)
                    target_phone = peer_acc.get('phone') or peer_acc['name']
                    phrase = random.choice(PHRASES)

                    try:
                        await self.client.send_message(peer_acc['session'], phrase)
                        stats['pm'] += 1
                        logs.append(f"✉️ Переписка ЛС ➔ {target_phone}: «{phrase}»")
                    except Exception:
                        pass

                delay_seconds = random.randint(2400, 4800)
                next_time_utc = (datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)).strftime("%d.%m %H:%M UTC")

                log_text = "\n".join(logs) if logs else "⚡ Имитация фоновой активности..."
                report = (
                    f"🔥 **Прогрев {phone_name} · этап {stage}/{total_stages}**\n\n"
                    f"{log_text}\n\n"
                    f"Статистика этапа:\n"
                    f"• Подписок: {stats['subs']} | Просмотров: {stats['reads']}\n"
                    f"• Реакций: {stats['reactions']} | Сохранений: {stats['saved']}\n"
                    f"• Опросов: {stats['polls']} | Сообщений ЛС: {stats['pm']}\n\n"
                    f"**Этап {stage}/{total_stages} выполнен. Следующий запуск: {next_time_utc}**"
                )

                if progress_callback:
                    await progress_callback(report)

                if stage < total_stages:
                    await asyncio.sleep(delay_seconds)

            active_account_name = original_active
            return f"✅ **Комплексный прогрев для {phone_name} успешно завершен!**"

        except (ValueError, PermissionError) as err:
            active_account_name = original_active
            acc = self.get_account_data(acc_name)
            p_name = acc.get('phone') or acc['name'] if acc else acc_name
            return f"❌ Ошибка прогрева аккаунта ({p_name}): {err}"
        except Exception as e:
            active_account_name = original_active
            acc = self.get_account_data(acc_name)
            p_name = acc.get('phone') or acc['name'] if acc else acc_name
            return f"❌ Ошибка прогрева аккаунта ({p_name}): {e}"

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
        [Button.inline("📱 Аккаунты и Профили", b"acc_menu")],
        [Button.inline("🔍 Проверить бан (@SpamBot)", b"check_ban")],
        [Button.inline("🔥 Меню Прогрева", b"warmup_menu"), Button.inline("👥 Парсинг", b"parse")],
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
            Button.inline(label, f"manage_acc_{acc['name']}".encode()),
            Button.inline("❌ Удал.", f"delete_acc_{acc['name']}".encode())
        ])

    buttons.append([Button.inline("➕ Добавить Новый Аккаунт", b"add_acc")])
    buttons.append([Button.inline("⬅️ Назад в меню", b"main_menu")])
    return buttons


def get_account_manage_keyboard(acc_name):
    acc = worker.get_account_data(acc_name)
    has_proxy = acc and acc.get('proxy_id') is not None

    buttons = [
        [Button.inline("⭐ Сделать активным для задач", f"select_acc_{acc_name}".encode())]
    ]

    if has_proxy:
        buttons.append([Button.inline("📩 Получить код входа (Telegram 777000)", f"get_code_{acc_name}".encode())])
        buttons.append([Button.inline("🔄 Переавторизовать / Вход", f"auth_methods_{acc_name}".encode())])
        buttons.append([Button.inline("🌐 Изменить/Сменить прокси", f"change_proxy_{acc_name}".encode())])
    else:
        buttons.append([Button.inline("🌐 Привязать прокси", f"change_proxy_{acc_name}".encode())])

    buttons.append([Button.inline("🗑 Удалить аккаунт", f"delete_acc_{acc_name}".encode())])
    buttons.append([Button.inline("⬅️ К списку аккаунтов", b"acc_menu")])
    return buttons


def get_warmup_menu_keyboard():
    buttons = [
        [Button.inline("🚀 Запустить прогрев ВСЕХ номеров", b"warmup_all")],
        [Button.inline("🎯 Запустить прогрев поштучно", b"warmup_single_menu")],
        [Button.inline("⬅️ Назад в меню", b"main_menu")]
    ]
    return buttons


def get_warmup_single_keyboard():
    buttons = []
    for acc in accounts_db:
        p_name = acc.get('phone') or acc['name']
        buttons.append([Button.inline(f"🔥 Прогреть: {p_name}", f"warmup_one_{acc['name']}".encode())])
    buttons.append([Button.inline("⬅️ Назад", b"warmup_menu")])
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

    buttons.append([Button.inline("❌ Отмена", f"manage_acc_{acc_name}".encode())])
    return buttons


def get_auth_method_keyboard(acc_name):
    return [
        [Button.inline("📲 Вход по номеру телефона", f"auth_phone_{acc_name}".encode())],
        [Button.inline("🔲 Вход по QR-коду", f"auth_qr_{acc_name}".encode())],
        [Button.inline("⬅️ Назад", f"manage_acc_{acc_name}".encode())]
    ]


def get_db_keyboard():
    return [
        [Button.inline("📥 Скачать файл базы (.txt)", b"download_db")],
        [Button.inline("➕ Добавить юзеров вручную", b"add_manual_users")],
        [Button.inline("🗑 Очистить всю базу", b"clear_db")],
        [Button.inline("⬅️ Назад в меню", b"main_menu")]
    ]


async def show_acc_menu(event):
    text = "📱 **Управление профилями аккаунтов:**\n\n"
    if not accounts_db:
        text += "Аккаунтов пока нет. Нажмите **Добавить Новый Аккаунт**."
    else:
        text += f"Активный профиль для задач: `{active_account_name or 'Не выбран'}`\n\nВыберите аккаунт для детального управления профилем:"
    try:
        await event.edit(text, buttons=get_accounts_keyboard())
    except MessageNotModifiedError:
        pass


# ==================== ПЕРЕХВАТЧИК КОДОВ АВТОРИЗАЦИИ ====================

@bot.on(events.NewMessage(from_users=777000))
async def intercept_telegram_code(event):
    if event.sender_id != 777000:
        return
    
    text = event.text
    code_match = re.search(r'\b(\d{5,6})\b', text)
    
    if code_match:
        code = code_match.group(1)
        alert_text = (
            f"🔑 **Перехвачен код авторизации от Telegram!**\n\n"
            f"Код: `{code}`\n\n"
            f"Сообщение:\n{text}"
        )
        try:
            await bot.send_message(ADMIN_ID, alert_text)
        except Exception:
            pass


# ==================== ОБРАБОТКА КОМАНД И КНОПОК ====================

@bot.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    if event.sender_id != ADMIN_ID:
        return
    await event.respond(
        "🤖 **Панель управления Юзерботами**\n\n"
        "Управляйте аккаунтами, профилями, связывайте их с прокси и настраивайте автоматизацию.",
        buttons=get_main_keyboard()
    )


@bot.on(events.CallbackQuery)
async def callback_handler(event):
    global active_account_name, proxies_db
    if event.sender_id != ADMIN_ID:
        await event.answer("⛔ У вас нет доступа к этой панели!", alert=True)
        return

    data = event.data

    try:
        if data == b"main_menu":
            user_states.pop(event.sender_id, None)
            await event.edit("🤖 **Главное меню**", buttons=get_main_keyboard())

        elif data == b"acc_menu":
            await show_acc_menu(event)

        elif data == b"proxy_menu":
            text = "🌐 **Управление прокси:**\n\nВыберите прокси для просмотра или добавьте новый:"
            await event.edit(text, buttons=get_proxy_keyboard())

        elif data == b"add_acc":
            user_states[event.sender_id] = 'awaiting_acc_name'
            await event.edit("📝 Введите **название для нового аккаунта** (например: `Account_1`):")

        elif data.startswith(b"manage_acc_"):
            acc_name = data.decode().replace("manage_acc_", "")
            acc = worker.get_account_data(acc_name)
            if not acc:
                await event.answer("❌ Аккаунт не найден!", alert=True)
                return
            
            p_name = acc.get('phone') or "Не указан"
            p_info = worker.get_proxy_for_account(acc_name)
            proxy_str = f"{p_info['host']}:{p_info['port']}" if p_info else "⚠️ Не привязан (нужен прокси для работы)"

            info_text = (
                f"👤 **Профиль аккаунта: `{acc_name}`**\n\n"
                f"• Номер телефона: `{p_name}`\n"
                f"• Прокси: `{proxy_str}`\n"
                f"• Файл сессии: `{acc['session']}.session`\n\n"
                f"Выберите действие с профилем:"
            )
            await event.edit(info_text, buttons=get_account_manage_keyboard(acc_name))

        elif data.startswith(b"get_code_"):
            acc_name = data.decode().replace("get_code_", "")
            await event.answer("⏳ Запрашиваем код у сессии аккаунта...")
            res_code_msg = await worker.fetch_last_login_code(acc_name)
            await event.edit(res_code_msg, buttons=get_account_manage_keyboard(acc_name))

        elif data.startswith(b"change_proxy_"):
            acc_name = data.decode().replace("change_proxy_", "")
            if not proxies_db:
                await event.answer("❌ Сначала добавьте хотя бы один прокси в разделе «🌐 Прокси»!", alert=True)
                return
            await event.edit(f"🌐 **Выберите прокси для аккаунта `{acc_name}`:**", buttons=get_proxy_select_keyboard_for_acc(acc_name))

        elif data.startswith(b"auth_methods_"):
            acc_name = data.decode().replace("auth_methods_", "")
            await event.edit(f"📲 Выберите способ авторизации для `{acc_name}`:", buttons=get_auth_method_keyboard(acc_name))

        elif data.startswith(b"select_acc_"):
            acc_name = data.decode().replace("select_acc_", "")
            active_account_name = acc_name
            await event.answer(f"✅ Аккаунт {acc_name} назначен активным для задач!")
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
            
            target_acc = worker.get_account_data(acc_name)
            p_name = target_acc.get('phone') if target_acc and target_acc.get('phone') else "Не указан"
            
            info_text = (
                f"✅ **Прокси `{best_proxy['host']}:{best_proxy['port']}` успешно привязан!** (Загрузка: {usage}/2)\n\n"
                f"👤 **Профиль аккаунта: `{acc_name}`**\n"
                f"• Номер телефона: `{p_name}`\n"
                f"• Прокси: `{best_proxy['host']}:{best_proxy['port']}`\n\n"
                f"Выберите действие с профилем:"
            )
            await event.edit(info_text, buttons=get_account_manage_keyboard(acc_name))

        elif data.startswith(b"bind_p_"):
            payload = data.decode().replace("bind_p_", "")
            acc_name, p_id_str = payload.rsplit("_", 1)
            proxy_id = int(p_id_str)

            if get_proxy_usage_count(proxy_id) >= 2:
                await event.answer("❌ На этот прокси нельзя привязать более 2 аккаунтов!", alert=True)
                return

            for acc in accounts_db:
                if acc['name'] == acc_name:
                    acc['proxy_id'] = proxy_id
                    break

            p_info = worker.get_proxy_for_account(acc_name)
            proxy_str = f"{p_info['host']}:{p_info['port']}" if p_info else "Не привязан"
            acc_data = worker.get_account_data(acc_name)
            p_phone = acc_data.get('phone') or "Не указан"

            info_text = (
                f"✅ **Прокси успешно привязан к `{acc_name}`!**\n\n"
                f"👤 **Профиль аккаунта: `{acc_name}`**\n"
                f"• Номер телефона: `{p_phone}`\n"
                f"• Прокси: `{proxy_str}`\n\n"
                f"Выберите действие с профилем:"
            )
            await event.edit(info_text, buttons=get_account_manage_keyboard(acc_name))

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
                await event.edit("❌ К аккаунту не привязан прокси! Сначала привяжите прокси.", buttons=get_account_manage_keyboard(acc_name))
                return

            telethon_proxy = get_telethon_proxy(proxy_info)
            await event.edit("⏳ Подключение к Telegram через прокси...")

            auth_client = TelegramClient(acc_data['session'], API_ID, API_HASH, proxy=telethon_proxy)
            try:
                await auth_client.connect()
            except Exception as e:
                await event.edit(f"❌ Не удалось подключиться через прокси: `{e}`", buttons=get_account_manage_keyboard(acc_name))
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
                    caption=f"🔲 **QR-код для аккаунта {acc_name}:**\nСканируйте в официальном приложении Telegram."
                )

                try:
                    user = await qr_login.wait(timeout=60)
                    acc_data['phone'] = getattr(user, 'phone', None)
                    await bot.send_message(event.chat_id, f"✅ **Успешно авторизован аккаунт:** {user.first_name} (@{user.username})", buttons=get_main_keyboard())
                except SessionPasswordNeededError:
                    auth_context['client'] = auth_client
                    user_states[event.sender_id] = 'awaiting_2fa'
                    await bot.send_message(event.chat_id, "🔐 На аккаунте включена 2FA. Введите облачный пароль:")
                finally:
                    if auth_client.is_connected():
                        await auth_client.disconnect()
            except Exception as e:
                await bot.send_message(event.chat_id, f"❌ Ошибка QR-авторизации: {e}", buttons=get_main_keyboard())

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

        elif data.startswith(b"del_proxy_"):
            p_id = int(data.decode().replace("del_proxy_", ""))
            for acc in accounts_db:
                if acc.get('proxy_id') == p_id:
                    acc['proxy_id'] = None
            
            proxies_db = [p for p in proxies_db if p['id'] != p_id]
            await event.answer("🗑 Прокси удален!")
            text = "🌐 **Управление прокси:**\n\nВыберите прокси для просмотра или добавьте новый:"
            await event.edit(text, buttons=get_proxy_keyboard())

        elif data == b"add_proxy":
            user_states[event.sender_id] = 'awaiting_proxy'
            await event.edit("📝 Отправьте прокси в одном из форматов:\n`ip:port`\n`ip:port:user:pass`\n`socks5://user:pass@ip:port`")

        elif data.startswith(b"proxy_info_"):
            p_id = int(data.decode().replace("proxy_info_", ""))
            p = next((item for item in proxies_db if item['id'] == p_id), None)
            if not p:
                await event.answer("❌ Прокси не найден!", alert=True)
                return
            usage = get_proxy_usage_count(p_id)
            p_type_name = "HTTP" if p['type'] == socks.HTTP else ("SOCKS5" if p['type'] == socks.SOCKS5 else "SOCKS4")
            
            bound_accs = [acc['name'] for acc in accounts_db if acc.get('proxy_id') == p_id]
            bound_str = ", ".join(bound_accs) if bound_accs else "Нет"

            text = (
                f"🌐 **Прокси #{p['id']}**\n\n"
                f"• Тип: `{p_type_name}`\n"
                f"• Хост: `{p['host']}`\n"
                f"• Порт: `{p['port']}`\n"
                f"• Юзер: `{p.get('user') or 'Нет'}`\n"
                f"• Аккаунты на прокси ({usage}/2): `{bound_str}`"
            )
            buttons = [
                [Button.inline("🗑 Удалить прокси", f"del_proxy_{p_id}".encode())],
                [Button.inline("⬅️ К списку прокси", b"proxy_menu")]
            ]
            await event.edit(text, buttons=buttons)

        elif data == b"warmup_menu":
            await event.edit("🔥 **Меню Прогрева Аккаунтов**\n\nВыберите режим прогрева:", buttons=get_warmup_menu_keyboard())

        elif data == b"warmup_single_menu":
            if not accounts_db:
                await event.answer("❌ Нет доступных аккаунтов!", alert=True)
                return
            await event.edit("🎯 **Выберите аккаунт для прогрева:**", buttons=get_warmup_single_keyboard())

        elif data.startswith(b"warmup_one_"):
            acc_name = data.decode().replace("warmup_one_", "")
            await event.answer("🚀 Запуск прогрева аккаунта...")
            
            async def progress_cb(msg):
                try:
                    await event.respond(msg)
                except Exception:
                    pass

            result_msg = await worker.run_single_warmup(acc_name, total_stages=6, progress_callback=progress_cb)
            await event.respond(result_msg, buttons=get_main_keyboard())

        elif data == b"warmup_all":
            if not accounts_db:
                await event.answer("❌ Нет доступных аккаунтов!", alert=True)
                return
            await event.answer("🚀 Запуск массового прогрева всех аккаунтов...")
            
            for acc in accounts_db:
                async def progress_cb(msg):
                    try:
                        await event.respond(msg)
                    except Exception:
                        pass
                await event.respond(f"🚀 Старт прогрева для аккаунта: {acc['name']}")
                await worker.run_single_warmup(acc['name'], total_stages=6, progress_callback=progress_cb)

            await event.respond("✅ **Массовый прогрев всех аккаунтов завершен!**", buttons=get_main_keyboard())

        elif data == b"parse":
            user_states[event.sender_id] = 'awaiting_parse_target'
            await event.edit("👥 Отправьте **ссылку на чат/канал** для парсинга (например: `@durov`, `https://t.me/telegram` или приватную ссылку):")

        elif data == b"broadcast":
            user_states[event.sender_id] = 'awaiting_broadcast_text'
            await event.edit("✉️ Отправьте **текст для рассылки** по базе пользователей:")

        elif data == b"check_ban":
            if not active_account_name:
                await event.answer("❌ Сначала выберите активный аккаунт для задач!", alert=True)
                return
            await event.answer("⏳ Проверяем SpamBot через изолированную сессию...")
            res = await worker.check_ban(active_account_name)
            await event.edit(res, buttons=[Button.inline("⬅️ Назад в меню", b"main_menu")])

        await event.answer()

    except Exception as e:
        await event.answer(f"❌ Произошла ошибка: {e}", alert=True)


# ==================== ОБРАБОТКА ТЕКСТОВЫХ СООБЩЕНИЙ (СОСТОЯНИЯ) ====================

@bot.on(events.NewMessage)
async def text_handler(event):
    global active_account_name
    if event.sender_id != ADMIN_ID:
        return
    
    state = user_states.get(event.sender_id)
    if not state:
        return

    text = event.text.strip()

    if state == 'awaiting_acc_name':
        acc_name = text
        if any(a['name'] == acc_name for a in accounts_db):
            await event.respond("❌ Аккаунт с таким именем уже существует! Введите другое название:")
            return

        new_acc = {
            'name': acc_name,
            'session': f"session_{acc_name}",
            'phone': None,
            'proxy_id': None
        }
        accounts_db.append(new_acc)
        if not active_account_name:
            active_account_name = acc_name

        user_states.pop(event.sender_id, None)
        
        if not proxies_db:
            await event.respond(
                f"✅ Аккаунт `{acc_name}` создан!\n⚠️ У вас пока нет добавленных прокси. Перейдите в меню прокси, чтобы добавить их, либо аккаунт останется без привязки.",
                buttons=get_account_manage_keyboard(acc_name)
            )
        else:
            await event.respond(
                f"✅ Аккаунт `{acc_name}` создан!\nТеперь привяжите к нему прокси:",
                buttons=get_proxy_select_keyboard_for_acc(acc_name)
            )

    elif state == 'awaiting_proxy':
        p_dict = parse_proxy_string(text)
        if not p_dict:
            await event.respond("❌ Неверный формат прокси! Попробуйте еще раз:")
            return

        p_id = len(proxies_db) + 1
        p_dict['id'] = p_id
        proxies_db.append(p_dict)

        user_states.pop(event.sender_id, None)
        await event.respond(
            f"✅ Прокси `{p_dict['host']}:{p_dict['port']}` успешно добавлен!",
            buttons=get_main_keyboard()
        )

    elif state == 'awaiting_phone_number':
        acc_name = auth_context.get('target_acc')
        user_states.pop(event.sender_id, None)

        acc_data = worker.get_account_data(acc_name)
        proxy_info = worker.get_proxy_for_account(acc_name)
        
        if not proxy_info:
            await event.respond("❌ К аккаунту не привязан прокси! Сначала привяжите прокси.", buttons=get_account_manage_keyboard(acc_name))
            return

        telethon_proxy = get_telethon_proxy(proxy_info)

        await event.respond("⏳ Подключение к Telegram и отправка кода...")
        auth_client = TelegramClient(acc_data['session'], API_ID, API_HASH, proxy=telethon_proxy)
        try:
            await auth_client.connect()
            sent_code = await auth_client.send_code_request(text)
            
            acc_data['phone'] = text
            auth_context['client'] = auth_client
            auth_context['phone_code_hash'] = sent_code.phone_code_hash
            auth_context['phone'] = text

            user_states[event.sender_id] = 'awaiting_phone_code'
            await event.respond("📲 Введите полученный **код из Telegram** (можно посмотреть в профиле аккаунта через кнопку запроса кода или перехватчик):")
        except Exception as e:
            if auth_client.is_connected():
                await auth_client.disconnect()
            await event.respond(f"❌ Ошибка запроса кода: {e}", buttons=get_main_keyboard())

    elif state == 'awaiting_phone_code':
        user_states.pop(event.sender_id, None)
        auth_client = auth_context.get('client')
        acc_name = auth_context.get('target_acc')
        phone = auth_context.get('phone')
        phone_code_hash = auth_context.get('phone_code_hash')

        acc_data = worker.get_account_data(acc_name)
        try:
            await auth_client.sign_in(phone=phone, code=text, phone_code_hash=phone_code_hash)
            acc_data['phone'] = phone
            await event.respond(f"✅ **Успешная авторизация аккаунта {acc_name}!**", buttons=get_main_keyboard())
        except SessionPasswordNeededError:
            user_states[event.sender_id] = 'awaiting_2fa'
            await event.respond("🔐 На аккаунте установлена двухэтапная аутентификация (2FA). Введите облачный пароль:")
            return
        except Exception as e:
            await event.respond(f"❌ Ошибка входа по коду: {e}", buttons=get_main_keyboard())
        finally:
            if auth_client and auth_client.is_connected():
                await auth_client.disconnect()

    elif state == 'awaiting_2fa':
        user_states.pop(event.sender_id, None)
        auth_client = auth_context.get('client')
        acc_name = auth_context.get('target_acc')
        acc_data = worker.get_account_data(acc_name)

        try:
            if not auth_client.is_connected():
                await auth_client.connect()
            
            await auth_client.sign_in(password=text)
            await event.respond(f"✅ **Успешный вход с 2FA для аккаунта {acc_name}!**", buttons=get_main_keyboard())
        except Exception as e:
            await event.respond(f"❌ Неверный 2FA пароль: {e}", buttons=get_main_keyboard())
        finally:
            if auth_client and auth_client.is_connected():
                await auth_client.disconnect()

    elif state == 'awaiting_manual_users':
        user_states.pop(event.sender_id, None)
        raw_lines = text.replace(',', '\n').split('\n')
        new_users = set()
        for line in raw_lines:
            cleaned = line.strip().lstrip('@')
            if cleaned:
                new_users.add(f"@{cleaned}")

        existing_users = set()
        if os.path.exists(PARSED_USERS_FILE):
            with open(PARSED_USERS_FILE, 'r', encoding='utf-8') as f:
                existing_users = set(line.strip() for line in f if line.strip())

        all_users = existing_users.union(new_users)
        with open(PARSED_USERS_FILE, 'w', encoding='utf-8') as f:
            for u in all_users:
                f.write(f"{u}\n")

        await event.respond(f"✅ Добавлено вручную: `{len(new_users)}`. Всего в базе: `{len(all_users)}`", buttons=get_main_keyboard())

    elif state == 'awaiting_parse_target':
        user_states.pop(event.sender_id, None)
        await event.respond("⏳ Начинаем сбор аудитории...")
        res = await worker.parse_target(text)
        await event.respond(res, buttons=get_main_keyboard())

    elif state == 'awaiting_broadcast_text':
        user_states.pop(event.sender_id, None)
        await event.respond("⏳ Запуск рассылки по базе...")
        
        async def broadcast_cb(msg):
            try:
                await event.respond(msg)
            except Exception:
                pass

        res = await worker.broadcast(text, progress_callback=broadcast_cb)
        await event.respond(res, buttons=get_main_keyboard())


# ==================== ЗАПУСК БОТА ====================

async def main():
    print("🤖 Бот управления юзерботами запущен...")
    await bot.start(bot_token=BOT_TOKEN)
    await bot.run_until_disconnected()

if __name__ == '__main__':
    asyncio.run(main())