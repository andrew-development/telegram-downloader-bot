import os
import html
import time
import logging
import asyncio
import uuid
from aiogram import Bot, Dispatcher, types, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
import config
import database
import downloader
import helper

logger = logging.getLogger(__name__)

session = AiohttpSession(timeout=300)
bot = Bot(token=config.BOT_TOKEN, session=session)
dp = Dispatcher()

class BotStates(StatesGroup):
    waiting_for_trim_range = State()
    waiting_for_local_trim_range = State()
    waiting_for_invite_code = State()
    waiting_for_broadcast_msg = State()
    waiting_for_search_keywords = State()
    waiting_for_music_keywords = State()
    waiting_for_clip_keywords = State()

pending_downloads = {}  # req_id -> {'url', 'title'}
active_downloads = {}   # req_id -> {'cancelled': False}
uploaded_files = {}     # file_req_id -> {'file_id', 'file_name', 'media_type'}
active_searches = {}    # search_id -> {'results', 'index', 'query', ...}

def get_main_reply_keyboard(user_id: int) -> types.ReplyKeyboardMarkup:
    """Создает постоянную клавиатуру и гарантирует отображение синей кнопки Меню в Telegram"""
    builder = ReplyKeyboardBuilder()
    builder.button(text="🔍 Поиск контента")
    builder.button(text="🎵 Поиск музыки (MP3)")
    builder.button(text="🎬 Видеоклипы")
    builder.button(text="📊 Статус")
    if user_id in config.ADMIN_IDS:
        builder.button(text="⚙️ Админ")
    builder.adjust(2, 2)
    return builder.as_markup(resize_keyboard=True, persistent=True)

async def setup_bot_commands():
    """Устанавливает имя бота MediaFlow и официальное меню команд Telegram"""
    commands = [
        types.BotCommand(command="start", description="🚀 Перезапуск и главное меню"),
        types.BotCommand(command="search", description="🔍 Поиск контента (Видео / Фото)"),
        types.BotCommand(command="music", description="🎵 Поиск музыки (MP3)"),
        types.BotCommand(command="clips", description="🎬 Поиск видеоклипов"),
        types.BotCommand(command="stats", description="📊 Ваша статистика скачиваний"),
        types.BotCommand(command="admin", description="⚙️ Панель администратора"),
    ]
    try:
        await bot.set_my_name("MediaFlow")
        await bot.set_my_short_description("🚀 MediaFlow — ваш персональный бот для скачивания и поиска медиаконтента!")
        await bot.set_my_description("🌟 Привет! Я MediaFlow — персональный бот для анонимного скачивания и поиска видео, музыки MP3 и клипов без регистрации!")
        await bot.set_my_commands(commands)
        logger.info("✅ Имя бота 'MediaFlow' и официальное меню Telegram (6 пунктов) успешно зарегистрированы!")
    except Exception as e:
        logger.error(f" Ошибка установки настроек бота: {e}")

async def check_user_subscription(user_id: int) -> tuple[bool, list[dict]]:
    if not config.REQUIRED_CHANNELS:
        return True, []
    not_subscribed_channels = []
    for channel_id in config.REQUIRED_CHANNELS:
        try:
            member = await bot.get_chat_member(chat_id=channel_id, user_id=user_id)
            if member.status not in ['member', 'administrator', 'creator']:
                chat = await bot.get_chat(channel_id)
                invite_link = chat.username and f"https://t.me/{chat.username}" or chat.invite_link
                not_subscribed_channels.append({
                    'id': channel_id,
                    'title': chat.title or "Обязательный канал",
                    'link': invite_link or f"t.me/c/{str(channel_id).replace('-100', '')}"
                })
        except Exception as e:
            logger.error(f"Ошибка проверки подписки: {e}")
            continue
    return (len(not_subscribed_channels) == 0), not_subscribed_channels

def get_subscription_keyboard(channels: list[dict]) -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for ch in channels:
        builder.button(text=f"📢 Подписаться на {ch['title']}", url=ch['link'])
    builder.button(text="✅ Я подписался", callback_data="check_sub")
    builder.adjust(1)
    return builder.as_markup()

async def ensure_approved_access(event: types.Message | types.CallbackQuery) -> bool:
    if isinstance(event, types.CallbackQuery):
        user_id = event.from_user.id
        username = event.from_user.username or ""
        first_name = event.from_user.first_name or ""
    else:
        user_id = event.from_user.id
        username = event.from_user.username or ""
        first_name = event.from_user.first_name or ""

    database.add_user(user_id, username, first_name)
    return True

@dp.message(F.text == "🚀 Старт")
async def msg_btn_start(message: types.Message):
    await cmd_start(message)

@dp.message(F.text.in_({"📊 Статус", "📊 Статистика"}))
async def msg_btn_stats(message: types.Message):
    await cmd_stats(message)

@dp.message(F.text == "⚙️ Админ")
async def msg_btn_admin(message: types.Message):
    await cmd_admin(message)

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    args = message.text.split(maxsplit=1)
    
    if len(args) > 1:
        code = args[1].strip()
        if database.use_access_code(user_id, code):
            await message.answer("🎉 Инвайт-код активирован! Вам успешно предоставлен доступ к боту.")
            
    approved = await ensure_approved_access(message)
    if not approved:
        return
        
    welcome_text = (
        f"Привет, {message.from_user.first_name}! 👋\n\n"
        f"Я **MediaFlow** — твой персональный бот для скачивания и поиска медиа.\n\n"
        f"✨ **Что я умею:**\n"
        f"• Скачивать видео из YouTube, TikTok, Instagram, Facebook (до 2 ГБ).\n"
        f"• ✂️ **Вырезать фрагмент из любого отправленного видео или аудио!**\n"
        f"• 🎵 Конвертировать любое видео в чистый MP3.\n\n"
        f"📋 **Главное меню и функции:**\n"
        f"🔍 `/search` — Поиск контента (Видео / Фото)\n"
        f"🎵 `/music` — Поиск музыки (MP3)\n"
        f"🎬 `/clips` — Поиск видеоклипов\n"
        f"📊 `/stats` — Ваша статистика скачиваний\n"
    )
    if user_id in config.ADMIN_IDS:
        welcome_text += f"⚙️ `/admin` — Панель администратора\n"
        
    welcome_text += "\nℹ️ *Примечание: Любые сообщения со значком ↗ под кнопками — это встроенная авто-реклама Telegram, бот её не рекомендует.*\n"
        
    is_sub, channels = await check_user_subscription(user_id)
    if not is_sub:
        welcome_text += "\n⚠️ Пожалуйста, подпишитесь на каналы ниже для доступа:"
        await message.answer(welcome_text, reply_markup=get_subscription_keyboard(channels), parse_mode="Markdown")
    else:
        welcome_text += "\n📥 Отправьте мне **ссылку**, **файл** или выберите команду в меню **Menu** (слева внизу)!"
        await message.answer(welcome_text, reply_markup=get_main_reply_keyboard(user_id), parse_mode="Markdown")

@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    if message.from_user.id not in config.ADMIN_IDS:
        return
    g_stats = database.get_global_stats()
    text = (
        f"⚙️ **Панель Администратора**\n\n"
        f"👥 Всего пользователей: **{g_stats['total_users']}**\n"
        f"✅ Одобренных пользователей: **{g_stats['approved_users']}**\n"
        f"📦 Всего скачиваний: **{g_stats['total_downloads']}** шт.\n"
        f"💾 Общий объем: **{g_stats['total_mb']} МБ** ({(g_stats['total_mb']/1024):.2f} ГБ)\n"
    )
    builder = InlineKeyboardBuilder()
    builder.button(text="🔑 Создать инвайт-код", callback_data="adm_gen_code")
    builder.button(text="📢 Рассылка пользователям", callback_data="adm_broadcast")
    builder.button(text="👥 Пользователи и скачивания", callback_data="adm_users_list")
    builder.adjust(1)
    await message.answer(text, reply_markup=builder.as_markup(), parse_mode="Markdown")

@dp.callback_query(F.data == "adm_gen_code")
async def cb_adm_gen_code(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in config.ADMIN_IDS:
        return
    await callback.message.answer("✏️ Введите новое имя для инвайт-кода (например: `TORONTO2026`):", parse_mode="Markdown")
    await state.set_state(BotStates.waiting_for_invite_code)
    await callback.answer()

@dp.message(BotStates.waiting_for_invite_code)
async def process_invite_code_input(message: types.Message, state: FSMContext):
    if message.from_user.id not in config.ADMIN_IDS:
        return
    code = message.text.strip().upper()
    database.create_access_code(code, message.from_user.id)
    bot_info = await bot.get_me()
    invite_url = f"https://t.me/{bot_info.username}?start={code}"
    await message.answer(f"✅ **Инвайт-код создан!**\n\n🔑 Код: `{code}`\n🔗 Ссылка:\n{invite_url}", parse_mode="Markdown")
    await state.clear()

@dp.callback_query(F.data == "adm_broadcast")
async def cb_adm_broadcast(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id not in config.ADMIN_IDS:
        return
    await callback.message.answer("📣 Введите текст или прикрепите сообщение для рассылки всем одобренным пользователям:")
    await state.set_state(BotStates.waiting_for_broadcast_msg)
    await callback.answer()

@dp.message(BotStates.waiting_for_broadcast_msg)
async def process_broadcast_input(message: types.Message, state: FSMContext):
    if message.from_user.id not in config.ADMIN_IDS:
        return
    users = database.get_all_approved_users()
    count = 0
    await message.answer(f"⏳ Начинаю рассылку для {len(users)} пользователей...")
    for u_id in users:
        try:
            await message.copy_to(chat_id=u_id)
            count += 1
            await asyncio.sleep(0.05)
        except Exception:
            pass
    await message.answer(f"✅ Рассылка завершена. Успешно доставлено: {count} пользователям.")
    await state.clear()

@dp.callback_query(F.data == "adm_users_list")
async def cb_adm_users_list(callback: types.CallbackQuery):
    if callback.from_user.id not in config.ADMIN_IDS:
        return
    users_stats = database.get_all_users_detailed_stats()
    
    if not users_stats:
        await callback.message.answer("👥 В базе пока нет зарегистрированных пользователей.")
        await callback.answer()
        return
        
    text = "👥 **Детальная статистика по пользователям:**\n\n"
    for idx, u in enumerate(users_stats, start=1):
        status_icon = "🟢" if u['is_approved'] else "🔴"
        uname = f"@{u['username']}" if u['username'] != 'Без юзернейма' else u['username']
        text += (
            f"{idx}. {status_icon} **{u['first_name']}** ({uname})\n"
            f"   🆔 ID: `{u['user_id']}`\n"
            f"   📦 Скачано файлов: **{u['downloads_count']} шт.**\n"
            f"   💾 Общий объем: **{u['total_mb']} МБ** ({(u['total_mb']/1024):.2f} ГБ)\n"
            f"   -----------------------------------\n"
        )
        
    await callback.message.answer(text, parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data.startswith("adm_allow:"))
async def cb_adm_allow(callback: types.CallbackQuery):
    if callback.from_user.id not in config.ADMIN_IDS:
        return
    target_id = int(callback.data.split(":")[1])
    database.approve_user(target_id)
    await callback.message.edit_text(f"✅ **Доступ успешно РАЗРЕШЕН** для пользователя (ID: `{target_id}`).", parse_mode="Markdown")
    await callback.answer("Доступ разрешен.")
    try:
        await bot.send_message(chat_id=target_id, text="🎉 **Вам разрешен доступ к боту!** Теперь вы можете отправлять ссылки и файлы.", parse_mode="Markdown")
    except Exception:
        pass

@dp.callback_query(F.data.startswith("adm_reject:"))
async def cb_adm_reject(callback: types.CallbackQuery):
    if callback.from_user.id not in config.ADMIN_IDS:
        return
    target_id = int(callback.data.split(":")[1])
    database.reject_user(target_id)
    await callback.message.edit_text(f"❌ **Запрос на доступ ОТКЛОНЕН** (ID: `{target_id}`).", parse_mode="Markdown")
    await callback.answer("Запрос отклонен.")
    try:
        await bot.send_message(chat_id=target_id, text="К сожалению, ваш запрос на доступ был отклонен администратором.")
    except Exception:
        pass

@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    if not await ensure_approved_access(message):
        return
    user_id = message.from_user.id
    stats = database.get_user_stats(user_id)
    
    text = f"📊 **Ваша статистика скачиваний:**\n\n"
    text += f"📦 Всего скачано файлов: **{stats['total_count']}**\n"
    text += f"💾 Общий объем: **{stats['total_mb']} МБ** ({(stats['total_mb']/1024):.2f} ГБ)\n\n"
    
    if stats['by_platform']:
        text += "🌐 **По платформам:**\n"
        for platform, count in stats['by_platform']:
            text += f"• {platform}: {count} шт.\n"
        text += "\n"
        
    if stats['recent']:
        text += "📜 **Последние скачивания:**\n"
        for title, quality, size_mb, date in stats['recent']:
            text += f"• `{title[:30]}`... [{quality}] — {size_mb} МБ\n"
            
    await message.answer(text, reply_markup=get_main_reply_keyboard(user_id), parse_mode="Markdown")

# --- ОБРАБОТКА ЗАГРУЖЕННЫХ ПОЛЬЗОВАТЕЛЕМ ФАЙЛОВ (ВИДЕО/АУДИО) ---

@dp.message(F.video | F.audio | F.voice | F.document)
async def handle_user_uploaded_file(message: types.Message):
    """Обрабатывает загруженные пользователем файлы прямо в чат Telegram"""
    if not await ensure_approved_access(message):
        return
        
    file_obj = message.video or message.audio or message.voice or message.document
    if not file_obj:
        return
        
    file_id = file_obj.file_id
    file_name = getattr(file_obj, 'file_name', 'Загруженный_файл')
    
    req_id = f"f_{os.urandom(6).hex()}"
    uploaded_files[req_id] = {
        'file_id': file_id,
        'file_name': file_name,
        'media_type': 'video' if message.video else ('audio' if message.audio or message.voice else 'document')
    }
    
    builder = InlineKeyboardBuilder()
    builder.button(text="✂️ Вырезать фрагмент", callback_data=f"local_trim:{req_id}")
    builder.button(text="🎵 Сконвертировать в MP3", callback_data=f"local_mp3:{req_id}")
    builder.adjust(1)
    
    await message.answer(
        f"📁 **Получен файл:** `{file_name}`\n\nВыберите желаемое действие:",
        reply_markup=builder.as_markup(),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data.startswith("local_trim:"))
async def cb_local_trim_init(callback: types.CallbackQuery, state: FSMContext):
    _, req_id = callback.data.split(":")
    if req_id not in uploaded_files:
        await callback.message.edit_text("❌ Файл устарел. Загрузите его заново.")
        await callback.answer()
        return
        
    await state.update_data(file_req_id=req_id)
    await callback.message.answer(
        "✂️ **Вырезка фрагмента из вашего файла**\n\n"
        "Введите отрезок времени в формате `ММ:СС - ММ:СС` (например: `00:15 - 00:45` или `01:10 - 02:30`):",
        parse_mode="Markdown"
    )
    await state.set_state(BotStates.waiting_for_local_trim_range)
    await callback.answer()

@dp.message(BotStates.waiting_for_local_trim_range)
async def process_local_trim_input(message: types.Message, state: FSMContext):
    time_range = message.text.strip()
    data = await state.get_data()
    req_id = data.get('file_req_id')
    
    if req_id not in uploaded_files:
        await message.answer("❌ Файл не найден.")
        await state.clear()
        return
        
    f_info = uploaded_files[req_id]
    status_msg = await message.answer("⏳ Скачиваю файл для обработки...")
    await state.clear()
    
    local_path = None
    trimmed_path = None
    try:
        # Скачиваем файл из Telegram локально
        tg_file = await bot.get_file(f_info['file_id'])
        ext = os.path.splitext(tg_file.file_path)[1] or '.mp4'
        local_path = os.path.join(config.DOWNLOAD_TEMP_DIR, f"user_{uuid.uuid4()}{ext}")
        await bot.download_file(tg_file.file_path, local_path)
        
        await status_msg.edit_text(f"✂️ Вырезаю отрезок `{time_range}` через FFmpeg...")
        
        # Запускаем мгновенную вырезку через FFmpeg
        trimmed_path = await asyncio.to_thread(downloader.trim_local_file, local_path, time_range)
        file_size_mb = round(os.path.getsize(trimmed_path) / (1024 * 1024), 2)
        
        caption = f"✂️ **Вырезанный фрагмент** [{time_range}]"
        
        if os.path.getsize(trimmed_path) <= 49 * 1024 * 1024:
            try:
                if f_info['media_type'] == 'audio':
                    await bot.send_audio(chat_id=message.from_user.id, audio=types.FSInputFile(trimmed_path), caption=caption, parse_mode="Markdown")
                else:
                    await bot.send_video(chat_id=message.from_user.id, video=types.FSInputFile(trimmed_path), caption=caption, parse_mode="Markdown")
            except Exception as se:
                if "file is too big" in str(se).lower() or "file_too_large" in str(se).lower():
                    await helper.send_large_file(chat_id=message.from_user.id, file_path=trimmed_path, caption=caption)
                else:
                    raise se
        else:
            await helper.send_large_file(chat_id=message.from_user.id, file_path=trimmed_path, caption=caption)
            
        database.log_download(message.from_user.id, "telegram_file", f"Отрезок {time_range}", file_size_mb, "local_trim")
        await status_msg.delete()
        
    except Exception as e:
        logger.error(f"Ошибка вырезки файла: {e}")
        await status_msg.edit_text(f"❌ Ошибка вырезки: {e}")
    finally:
        for p in [local_path, trimmed_path]:
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass

@dp.callback_query(F.data.startswith("local_mp3:"))
async def cb_local_mp3(callback: types.CallbackQuery):
    _, req_id = callback.data.split(":")
    if req_id not in uploaded_files:
        await callback.message.edit_text("❌ Файл не найден.")
        await callback.answer()
        return
        
    f_info = uploaded_files[req_id]
    await callback.message.edit_text("🎵 Конвертирую файл в MP3...")
    
    local_path = None
    mp3_path = None
    try:
        tg_file = await bot.get_file(f_info['file_id'])
        ext = os.path.splitext(tg_file.file_path)[1] or '.mp4'
        local_path = os.path.join(config.DOWNLOAD_TEMP_DIR, f"user_{uuid.uuid4()}{ext}")
        await bot.download_file(tg_file.file_path, local_path)
        
        mp3_path = await asyncio.to_thread(downloader.convert_local_to_mp3, local_path)
        file_size_mb = round(os.path.getsize(mp3_path) / (1024 * 1024), 2)
        
        await bot.send_audio(chat_id=callback.from_user.id, audio=types.FSInputFile(mp3_path), caption="🎵 **Конвертированное аудио (MP3)**", parse_mode="Markdown")
        database.log_download(callback.from_user.id, "telegram_file", f"{f_info['file_name']}", file_size_mb, "mp3_convert")
        await callback.message.delete()
        
    except Exception as e:
        logger.error(f"Ошибка конвертации в MP3: {e}")
        await callback.message.edit_text(f"❌ Ошибка конвертации: {e}")
    finally:
        for p in [local_path, mp3_path]:
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass

def format_caption(title: str, prefix: str = "✅", suffix: str = "") -> str:
    """Форматирует безопасный caption для Telegram (гарантированно <= 1000 символов)"""
    safe_t = html.escape(title)
    ad_note = "\n\nℹ️ <i>Блок со значком ↗ ниже — это авто-реклама Telegram.</i>"
    overhead = len(prefix) + len(suffix) + len(ad_note) + 30
    max_len = 980 - overhead
    if len(safe_t) > max_len:
        safe_t = safe_t[:max_len - 3] + "..."
    if suffix:
        return f"{prefix} <b>{safe_t}</b> [{suffix}]{ad_note}"
    return f"{prefix} <b>{safe_t}</b>{ad_note}"

# --- ОБРАБОТКА ССЫЛОК И ПОИСКА ---

@dp.message(F.text.startswith(("http://", "https://")))
async def handle_link(message: types.Message):
    user_id = message.from_user.id
    url = message.text.strip()
    logger.info(f"🔗 Получена ссылка от пользователя ID {user_id}: {url}")
    
    if not await ensure_approved_access(message):
        return
        
    is_sub, channels = await check_user_subscription(user_id)
    if not is_sub:
        await message.answer("⚠️ Для скачивания подпишитесь на каналы:", reply_markup=get_subscription_keyboard(channels))
        return

    # Автоматическое мгновенное скачивание 480p для всех ссылок (YouTube, Facebook, Instagram, TikTok, Snapchat и др.)
    status_msg = await message.answer(
        "⏳ Анализирую и скачиваю видео (~480p)...",
        reply_markup=get_main_reply_keyboard(user_id)
    )
    
    raw_title = "Видео по вашей ссылке"
    try:
        info = await asyncio.to_thread(downloader.get_video_info, url)
        if info and info.get('title'):
            raw_title = info['title']
    except Exception as e:
        logger.warning(f"Не удалось предварительно получить заголовок: {e}")
        
    safe_title = html.escape(raw_title)
    
    req_id = f"dl_{os.urandom(6).hex()}"
    active_downloads[req_id] = {'cancelled': False}
    
    cancel_builder = InlineKeyboardBuilder()
    cancel_builder.button(text="❌ Отменить", callback_data=f"cancel:{req_id}")
    
    last_update_time = [0]
    
    def on_progress(p):
        now = time.time()
        if now - last_update_time[0] >= 2.0:
            last_update_time[0] = now
            percent = p.get('percent', 0)
            d_mb = p.get('downloaded_mb', 0)
            t_mb = p.get('total_mb', 0)
            speed = p.get('speed_mb', 0)
            
            if percent > 0 and t_mb > 0:
                progress_text = (
                    f"⏳ Скачиваю <b>{safe_title[:40]}</b> [480p]\n\n"
                    f"📊 Прогресс: <b>{percent:.1f}%</b>\n"
                    f"📦 Загружено: <b>{d_mb} МБ</b> / <b>{t_mb} МБ</b>\n"
                    f"⚡ Скорость: <b>{speed} МБ/с</b>"
                )
            elif percent > 0:
                progress_text = (
                    f"⏳ Скачиваю <b>{safe_title[:40]}</b> [480p]\n\n"
                    f"📊 Подготовка потока: <b>{percent:.1f}%</b>\n"
                    f"⚡ Обработка видео..."
                )
            else:
                progress_text = (
                    f"⏳ Скачиваю <b>{safe_title[:40]}</b> [480p]\n\n"
                    f"📦 Загружено: <b>{d_mb} МБ</b>\n"
                    f"⚡ Скорость: <b>{speed} МБ/с</b>"
                )
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.run_coroutine_threadsafe(
                        status_msg.edit_text(progress_text, reply_markup=cancel_builder.as_markup(), parse_mode="HTML"),
                        loop
                    )
            except Exception:
                pass

    def check_cancelled():
        return active_downloads.get(req_id, {}).get('cancelled', False)

    try:
        file_path = await asyncio.to_thread(downloader.download_media, url, '480p', on_progress, check_cancelled)
        if check_cancelled():
            raise downloader.DownloadCancelledError("Отменено.")
            
        if not file_path or not os.path.exists(file_path):
            raise FileNotFoundError("Файл не найден на диске.")

        file_size = os.path.getsize(file_path)
        file_size_mb = round(file_size / (1024 * 1024), 2)
        
        caption = format_caption(raw_title, prefix="✅", suffix="480p")
        
        if file_size <= 49 * 1024 * 1024:
            input_file = types.FSInputFile(file_path)
            ext = os.path.splitext(file_path)[1].lower()
            if ext in ['.mp4', '.mkv', '.mov', '.avi']:
                await bot.send_video(chat_id=user_id, video=input_file, caption=caption, parse_mode="HTML")
            else:
                await bot.send_document(chat_id=user_id, document=input_file, caption=caption, parse_mode="HTML")
        else:
            caption_helper = format_caption(raw_title, prefix="✅", suffix="480p (Помощник)")
            success = await helper.send_large_file(chat_id=user_id, file_path=file_path, caption=caption_helper)
            if not success:
                raise Exception("Не удалось отправить файл через юзербота.")
                
        database.log_download(user_id, url, raw_title, file_size_mb, '480p')
        try:
            await status_msg.delete()
        except Exception:
            pass
        if os.path.exists(file_path):
            os.remove(file_path)
        return
    except Exception as e:
        err_str = str(e).lower()
        logger.error(f"Ошибка автоматического скачивания: {e}")
        try:
            if "registered users" in err_str or "private" in err_str or "login" in err_str:
                await status_msg.edit_text(
                    "🔒 <b>Это приватное видео автора</b> (доступно только друзьям автора или в закрытой группе).\n\n"
                    "💡 Общедоступные ролики скачиваются автоматически!",
                    parse_mode="HTML"
                )
            else:
                await status_msg.edit_text(f"❌ Не удалось скачать видео. Проверьте ссылку или попробуйте другую.")
        except Exception:
            pass
        return

@dp.message(Command("search"))
@dp.message(F.text.contains("Поиск контента"))
async def start_media_search(message: types.Message, state: FSMContext):
    if not await ensure_approved_access(message):
        return
    await state.set_state(BotStates.waiting_for_search_keywords)
    await message.answer(
        "🔍 **Поиск видео и контента**\n\nВведите тему или ключевые слова (например: `американские акции` или `ремонт авто`):",
        parse_mode="Markdown"
    )

@dp.message(Command("clips"))
@dp.message(F.text.contains("Видеоклипы"))
async def start_clip_search(message: types.Message, state: FSMContext):
    if not await ensure_approved_access(message):
        return
    await state.set_state(BotStates.waiting_for_clip_keywords)
    await message.answer(
        "🎬 **Поиск официальных видеоклипов**\n\nВведите название клипа или исполнителя (опечатки автоматически исправляются, например: `Michael Jackson` или `Анна Асти`):",
        parse_mode="Markdown"
    )

@dp.message(BotStates.waiting_for_clip_keywords)
async def process_clip_query(message: types.Message, state: FSMContext):
    await state.clear()
    query = message.text.strip()
    status_msg = await message.answer(f"🔎 Ищу музыкальные видеоклипы: **{query}**...", parse_mode="Markdown")
    
    results = await asyncio.to_thread(downloader.search_music_videos, query, 50)
    if not results:
        await status_msg.edit_text("❌ Ничего не найдено по вашему запросу. Попробуйте уточнить запрос.")
        return
        
    search_id = f"s_{os.urandom(6).hex()}"
    active_searches[search_id] = {
        'results': results,
        'index': 0,
        'query': query,
        'is_clip': True
    }
    await status_msg.delete()
    await send_search_card(message.chat.id, search_id)

@dp.message(Command("music"))
@dp.message(F.text.contains("Поиск музыки"))
async def start_music_search(message: types.Message, state: FSMContext):
    if not await ensure_approved_access(message):
        return
    await state.set_state(BotStates.waiting_for_music_keywords)
    await message.answer(
        "🎧 **Поиск музыки (MP3)**\n\nВведите название песни или исполнителя (опечатки автоматически исправляются, например: `Queen` или `Баста`):",
        parse_mode="Markdown"
    )

@dp.message(BotStates.waiting_for_music_keywords)
async def process_music_query(message: types.Message, state: FSMContext):
    await state.clear()
    query = message.text.strip()
    status_msg = await message.answer(f"🔎 Ищу аудиотреки: **{query}**...", parse_mode="Markdown")
    
    results = await asyncio.to_thread(downloader.search_music, query, 50)
    if not results:
        await status_msg.edit_text("❌ Ничего не найдено по вашему запросу. Попробуйте уточнить название.")
        return
        
    search_id = f"s_{os.urandom(6).hex()}"
    active_searches[search_id] = {
        'results': results,
        'index': 0,
        'query': query,
        'is_music': True
    }
    await status_msg.delete()
    await send_search_card(message.chat.id, search_id)

@dp.message(BotStates.waiting_for_search_keywords)
async def process_search_query(message: types.Message, state: FSMContext):
    if not await ensure_approved_access(message):
        return
    await state.clear()
    query = message.text.strip()
    
    status_msg = await message.answer(f"🔎 Ищу видео: **{query}**...", parse_mode="Markdown")
    results = await asyncio.to_thread(downloader.search_media, "YouTube", query, "video", 50)
    if not results:
        await status_msg.edit_text("❌ Ничего не найдено по вашему запросу.")
        return

    search_id = f"s_{os.urandom(6).hex()}"
    active_searches[search_id] = {
        'results': results,
        'index': 0,
        'query': query,
        'platform': 'YouTube',
        'media_type': 'video',
        'is_music': False
    }
    await status_msg.delete()
    await send_search_card(message.chat.id, search_id)

@dp.message(F.text & ~F.text.startswith("/") & ~F.text.startswith(("http://", "https://")))
async def fallback_text_search(message: types.Message, state: FSMContext):
    if any(k in message.text for k in ["Поиск контента", "Поиск музыки", "Видеоклипы", "Статус", "Админ"]):
        return
    current_state = await state.get_state()
    if current_state:
        return
    await process_search_query(message, state)

async def send_search_card(chat_id: int, search_id: str, message_to_edit: types.Message = None):
    search_data = active_searches.get(search_id)
    if not search_data:
        return
    results = search_data['results']
    idx = search_data['index']
    item = results[idx]
    
    is_music = search_data.get('is_music', False)
    is_clip = search_data.get('is_clip', False)
    
    req_id = f"dl_{os.urandom(6).hex()}"
    pending_downloads[req_id] = {
        'url': item['url'],
        'title': item['title']
    }
    database.save_pending_download(req_id, item['url'], item['title'])
    
    total = len(results)
    uploader = html.escape(item.get('uploader', 'Неизвестно'))
    duration = item.get('duration_str', 'Неизвестно')
    views = item.get('views_str', 'Неизвестно')
    title_esc = html.escape(item['title'])
    
    if is_music:
        caption = (
            f"🎵 <b>Найденный аудиотрек [{idx+1}/{total}]</b>\n\n"
            f"📌 <b>Название</b>: {title_esc}\n"
            f"👤 <b>Исполнитель/Канал</b>: {uploader}\n"
            f"⏱ <b>Длительность</b>: {duration}\n"
            f"👁 <b>Просмотры</b>: {views}\n"
        )
    elif is_clip:
        caption = (
            f"🎬 <b>Найденный видеоклип [{idx+1}/{total}]</b>\n\n"
            f"📌 <b>Клип</b>: {title_esc}\n"
            f"👤 <b>Автор/Канал</b>: {uploader}\n"
            f"⏱ <b>Длительность</b>: {duration}\n"
            f"👁 <b>Просмотры</b>: {views}\n"
        )
    else:
        media_icon = "🎬" if search_data.get('media_type') == 'video' else "🖼"
        caption = (
            f"{media_icon} <b>Результат поиска [{idx+1}/{total}]</b>\n\n"
            f"📌 <b>Название</b>: {title_esc}\n"
            f"👤 <b>Автор/Канал</b>: {uploader}\n"
            f"⏱ <b>Длительность</b>: {duration}\n"
            f"👁 <b>Просмотры</b>: {views}\n"
            f"🌐 <b>Платформа</b>: {search_data.get('platform', 'YouTube')}\n"
        )

    builder = InlineKeyboardBuilder()
    if is_music:
        builder.button(text="🎵 Скачать MP3", callback_data=f"q:{req_id}:mp3")
    elif is_clip:
        builder.button(text="🎬 1080p (Высокое)", callback_data=f"q:{req_id}:1080p")
        builder.button(text="🎬 720p (Среднее)", callback_data=f"q:{req_id}:720p")
        builder.button(text="🎬 480p (Низкое)", callback_data=f"q:{req_id}:480p")
        builder.button(text="🎬 360p (Эконом)", callback_data=f"q:{req_id}:360p")
        builder.button(text="🎵 Извлечь MP3", callback_data=f"q:{req_id}:mp3")
        builder.adjust(2, 2, 1)
    else:
        if search_data.get('media_type') == 'photo':
            builder.button(text="🖼 Скачать обложку", callback_data=f"thumb:{req_id}")
        else:
            builder.button(text="🎬 Скачать 720p", callback_data=f"q:{req_id}:720p")
            builder.button(text="🎬 Скачать 1080p", callback_data=f"q:{req_id}:1080p")
            builder.button(text="🎬 Скачать 360p (Эконом)", callback_data=f"q:{req_id}:360p")
            builder.button(text="🎵 Только Аудио (MP3)", callback_data=f"q:{req_id}:mp3")
            builder.adjust(2, 2)
            
    nav_buttons = []
    if idx > 0:
        nav_buttons.append(types.InlineKeyboardButton(text="⬅️ Назад", callback_data=f"snav:{search_id}:prev"))
    if idx < total - 1:
        nav_buttons.append(types.InlineKeyboardButton(text="Вперед ➡️", callback_data=f"snav:{search_id}:next"))
    if nav_buttons:
        builder.row(*nav_buttons)
        
    builder.row(
        types.InlineKeyboardButton(text="🔄 Свежие варианты", callback_data=f"snav:{search_id}:refresh"),
        types.InlineKeyboardButton(text="❌ Закрыть", callback_data=f"snav:{search_id}:close")
    )

    photo_url = item.get('thumbnail')
    if photo_url:
        caption = f'<a href="{photo_url}">&#8203;</a>' + caption

    if message_to_edit:
        try:
            await message_to_edit.edit_text(caption, reply_markup=builder.as_markup(), parse_mode="HTML", disable_web_page_preview=False)
            return
        except Exception as edit_err:
            logger.warning(f"⚠️ Ошибка редактирования карточки: {edit_err}")

    await bot.send_message(chat_id, caption, reply_markup=builder.as_markup(), parse_mode="HTML", disable_web_page_preview=False)

@dp.callback_query(F.data.startswith("snav:"))
async def cb_search_nav(callback: types.CallbackQuery):
    _, search_id, action = callback.data.split(":")
    search_data = active_searches.get(search_id)
    if not search_data:
        await callback.answer("Поиск устарел.", show_alert=True)
        return
        
    if action == "close":
        active_searches.pop(search_id, None)
        await callback.message.delete()
        await callback.answer("Поиск закрыт.")
        return
    elif action == "prev":
        search_data['index'] = max(0, search_data['index'] - 1)
    elif action == "next":
        search_data['index'] = min(len(search_data['results']) - 1, search_data['index'] + 1)
    elif action == "refresh":
        total_len = len(search_data['results'])
        new_idx = search_data['index'] + 5
        if new_idx >= total_len:
            new_idx = 0
        search_data['index'] = new_idx
        await callback.answer(f"Показаны варианты {new_idx+1}-{min(new_idx+5, total_len)} из {total_len}!")
        await send_search_card(callback.message.chat.id, search_id, callback.message)
        return
        
    await send_search_card(callback.message.chat.id, search_id, callback.message)
    await callback.answer()

@dp.callback_query(F.data.startswith("thumb:"))
async def cb_download_thumb(callback: types.CallbackQuery):
    if not await ensure_approved_access(callback):
        return
    _, req_id = callback.data.split(":")
    req = pending_downloads.get(req_id) or database.get_pending_download(req_id)
    if not req:
        await callback.message.edit_text("❌ Ссылка устарела.")
        await callback.answer()
        return
        
    url = req['url']
    await callback.message.edit_text("🖼 Скачиваю обложку высокого разрешения...")
    
    try:
        thumb_path = await asyncio.to_thread(downloader.download_thumbnail, url)
        input_file = types.FSInputFile(thumb_path)
        await bot.send_photo(chat_id=callback.from_user.id, photo=input_file, caption="🖼 Обложка видео")
        await callback.message.delete()
        if os.path.exists(thumb_path):
            os.remove(thumb_path)
    except Exception as e:
        await callback.message.edit_text(f"❌ Ошибка скачивания обложки: {e}")

@dp.callback_query(F.data.startswith("trim_init:"))
async def cb_trim_init(callback: types.CallbackQuery, state: FSMContext):
    _, req_id = callback.data.split(":")
    req = pending_downloads.get(req_id) or database.get_pending_download(req_id)
    if not req:
        await callback.message.edit_text("❌ Ссылка устарела.")
        await callback.answer()
        return
        
    await state.update_data(trim_req_id=req_id)
    await callback.message.answer(
        "✂️ **Вырезка фрагмента по ссылке**\n\n"
        "Введите отрезок времени в формате `ММ:СС - ММ:СС` (например: `01:15 - 03:45`):",
        parse_mode="Markdown"
    )
    await state.set_state(BotStates.waiting_for_trim_range)
    await callback.answer()

@dp.message(BotStates.waiting_for_trim_range)
async def process_trim_input(message: types.Message, state: FSMContext):
    time_range = message.text.strip()
    data = await state.get_data()
    req_id = data.get('trim_req_id')
    
    req = pending_downloads.get(req_id) or database.get_pending_download(req_id)
    if not req:
        await message.answer("❌ Ссылка устарела.")
        await state.clear()
        return
        
    url = req['url']
    title = req['title']
    
    status_msg = await message.answer(f"⏳ Скачиваю и вырезаю фрагмент `{time_range}` из **{title[:50]}**...")
    await state.clear()
    
    try:
        file_path = await asyncio.to_thread(downloader.download_media, url, '1080p', None, None, time_range)
        file_size_mb = round(os.path.getsize(file_path) / (1024 * 1024), 2)
        caption = format_caption(title, prefix="✂️", suffix=time_range)
        
        if os.path.getsize(file_path) <= 49 * 1024 * 1024:
            try:
                await bot.send_video(chat_id=message.from_user.id, video=types.FSInputFile(file_path), caption=caption, parse_mode="HTML")
            except Exception as se:
                if "file is too big" in str(se).lower() or "file_too_large" in str(se).lower():
                    await helper.send_large_file(chat_id=message.from_user.id, file_path=file_path, caption=caption)
                else:
                    raise se
        else:
            await helper.send_large_file(chat_id=message.from_user.id, file_path=file_path, caption=caption)
            
        database.log_download(message.from_user.id, url, f"{title} [{time_range}]", file_size_mb, "trim")
        await status_msg.delete()
        if os.path.exists(file_path):
            os.remove(file_path)
    except Exception as e:
        logger.error(f"Ошибка вырезки: {e}")
        await status_msg.edit_text(f"❌ Ошибка вырезки: {e}")

@dp.callback_query(F.data.startswith("cancel:"))
async def cb_cancel(callback: types.CallbackQuery):
    _, req_id = callback.data.split(":")
    if req_id in active_downloads:
        active_downloads[req_id]['cancelled'] = True
        await callback.message.edit_text("🛑 Отмена скачивания по вашему запросу...")
        await callback.answer("Скачивание прервано.")

@dp.callback_query(F.data.startswith("q:"))
async def cb_download(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if not await ensure_approved_access(callback):
        return
        
    is_sub, channels = await check_user_subscription(user_id)
    if not is_sub:
        await callback.message.edit_text("⚠️ Вы отписались от каналов:", reply_markup=get_subscription_keyboard(channels))
        await callback.answer("Скачивание заблокировано.", show_alert=True)
        return
        
    _, req_id, quality = callback.data.split(":")
    req = pending_downloads.get(req_id) or database.get_pending_download(req_id)
    if not req:
        await callback.message.edit_text("❌ Ссылка устарела. Отправьте ее заново.")
        await callback.answer()
        return
        
    url = req['url']
    title = req['title']
    safe_title = html.escape(title)
    
    active_downloads[req_id] = {'cancelled': False}
    cancel_builder = InlineKeyboardBuilder()
    cancel_builder.button(text="❌ Отменить", callback_data=f"cancel:{req_id}")
    
    await callback.message.edit_text(
        f"⏳ Скачиваю <b>{safe_title[:50]}</b> [{quality}]...\nПрогресс: 0%",
        reply_markup=cancel_builder.as_markup(),
        parse_mode="HTML"
    )
    await callback.answer()
    
    last_update_time = [0]
    
    def on_progress(p):
        now = time.time()
        if now - last_update_time[0] >= 2.0:
            last_update_time[0] = now
            percent = p['percent']
            d_mb = p['downloaded_mb']
            t_mb = p['total_mb']
            speed = p['speed_mb']
            
            if percent > 0 and t_mb > 0:
                progress_text = (
                    f"⏳ Скачиваю <b>{safe_title[:40]}</b> [{quality}]\n\n"
                    f"📊 Прогресс: <b>{percent:.1f}%</b>\n"
                    f"📦 Загружено: <b>{d_mb} МБ</b> / <b>{t_mb} МБ</b>\n"
                    f"⚡ Скорость: <b>{speed} МБ/с</b>"
                )
            elif percent > 0:
                progress_text = (
                    f"⏳ Скачиваю <b>{safe_title[:40]}</b> [{quality}]\n\n"
                    f"📊 Подготовка потока: <b>{percent:.1f}%</b>\n"
                    f"⚡ Обработка видео..."
                )
            else:
                progress_text = (
                    f"⏳ Скачиваю <b>{safe_title[:40]}</b> [{quality}]\n\n"
                    f"📦 Загружено: <b>{d_mb} МБ</b>\n"
                    f"⚡ Скорость: <b>{speed} МБ/с</b>"
                )
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.run_coroutine_threadsafe(
                        callback.message.edit_text(progress_text, reply_markup=cancel_builder.as_markup(), parse_mode="HTML"),
                        loop
                    )
            except Exception:
                pass

    def check_cancelled():
        return active_downloads.get(req_id, {}).get('cancelled', False)

    file_path = None
    try:
        file_path = await asyncio.to_thread(downloader.download_media, url, quality, on_progress, check_cancelled)
        
        if check_cancelled():
            raise downloader.DownloadCancelledError("Отменено.")
            
        if not os.path.exists(file_path):
            raise FileNotFoundError("Файл не найден на диске.")
            
        file_size = os.path.getsize(file_path)
        file_size_mb = round(file_size / (1024 * 1024), 2)
        
        cancel_b = InlineKeyboardBuilder()
        cancel_b.button(text="🛑 Отменить загрузку", callback_data=f"cancel_dl:{req_id}")
        await callback.message.edit_text(
            f"📤 Загружаю файл в Telegram ({file_size_mb} МБ)...",
            reply_markup=cancel_b.as_markup()
        )
        caption = format_caption(title, prefix="✅", suffix=quality)
        
        if file_size <= 49 * 1024 * 1024:
            input_file = types.FSInputFile(file_path)
            ext = os.path.splitext(file_path)[1].lower()
            if quality == 'mp3' or ext == '.mp3':
                await bot.send_audio(chat_id=user_id, audio=input_file, caption=caption, parse_mode="HTML")
            elif ext in ['.mp4', '.mkv', '.mov', '.avi']:
                try:
                    w, h = await asyncio.wait_for(asyncio.to_thread(downloader.get_video_dimensions, file_path), timeout=3.0)
                except Exception:
                    w, h = None, None
                await bot.send_video(
                    chat_id=user_id,
                    video=input_file,
                    caption=caption,
                    width=w,
                    height=h,
                    supports_streaming=True,
                    parse_mode="HTML"
                )
            else:
                await bot.send_document(chat_id=user_id, document=input_file, caption=caption, parse_mode="HTML")
        else:
            caption_helper = format_caption(title, prefix="✅", suffix=f"{quality} (Помощник)")
            success = await helper.send_large_file(chat_id=user_id, file_path=file_path, caption=caption_helper)
            if not success:
                raise Exception("Не удалось отправить файл через юзербота.")
                
        database.log_download(user_id, url, title, file_size_mb, quality)
        await callback.message.delete()
        
    except downloader.DownloadCancelledError:
        await callback.message.edit_text("🛑 Скачивание было отменено.")
    except Exception as e:
        logger.error(f"Ошибка при скачивании или отправке: {e}")
        await callback.message.edit_text(f"❌ Произошла ошибка: {str(e)[:100]}")
        
    finally:
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception:
                pass
        pending_downloads.pop(req_id, None)
        active_downloads.pop(req_id, None)
