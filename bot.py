import os
import time
import logging
import asyncio
import uuid
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
import config
import database
import downloader
import helper

logger = logging.getLogger(__name__)

bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher()

class BotStates(StatesGroup):
    waiting_for_trim_range = State()
    waiting_for_local_trim_range = State()
    waiting_for_invite_code = State()
    waiting_for_broadcast_msg = State()

pending_downloads = {}  # req_id -> {'url', 'title'}
active_downloads = {}   # req_id -> {'cancelled': False}
uploaded_files = {}     # file_req_id -> {'file_id', 'file_name', 'media_type'}

def get_main_reply_keyboard(user_id: int) -> types.ReplyKeyboardMarkup:
    """Создает постоянную нижнюю клавиатуру над полем ввода сообщения"""
    builder = ReplyKeyboardBuilder()
    builder.button(text="🚀 Старт")
    builder.button(text="📊 Статус")
    if user_id in config.ADMIN_IDS:
        builder.button(text="⚙️ Админ")
    builder.adjust(3 if user_id in config.ADMIN_IDS else 2)
    return builder.as_markup(resize_keyboard=True, persistent=True)

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
        target_msg = event.message
    else:
        user_id = event.from_user.id
        username = event.from_user.username or ""
        first_name = event.from_user.first_name or ""
        target_msg = event

    # АДМИНИСТРАТОР ВСЕГДА ОДОБРЕН БЕЗ КАКИХ-ЛИБО ПОДТВЕРЖДЕНИЙ
    if user_id in config.ADMIN_IDS:
        return True

    is_approved = database.add_user(user_id, username, first_name)
    if is_approved:
        return True
        
    if target_msg:
        await target_msg.answer(
            "🔒 **Доступ ограничен.**\n\n"
            "Ваш запрос на использование бота отправлен Администратору.\n"
            "Пожалуйста, подождите подтверждения доступа.",
            parse_mode="Markdown"
        )
    
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Разрешить доступ", callback_data=f"adm_allow:{user_id}")
    builder.button(text="❌ Отклонить доступ", callback_data=f"adm_reject:{user_id}")
    builder.adjust(2)
    
    user_mention = f"@{username}" if username else first_name
    admin_text = (
        f"🔔 **Новый запрос на доступ к боту!**\n\n"
        f"👤 Пользователь: **{first_name}** ({user_mention})\n"
        f"🆔 ID: `{user_id}`\n"
    )
    
    for admin_id in config.ADMIN_IDS:
        try:
            await bot.send_message(chat_id=admin_id, text=admin_text, reply_markup=builder.as_markup(), parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Не удалось отправить уведомление админу {admin_id}: {e}")
            
    return False

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
        
    is_sub, channels = await check_user_subscription(user_id)
    welcome_text = (
        f"Привет, {message.from_user.first_name}! 👋\n\n"
        f"Я персональный бот для скачивания и редактирования медиа.\n\n"
        f"✨ **Что я умею:**\n"
        f"• Скачивать видео из YouTube, TikTok, Instagram, Facebook (до 2 ГБ).\n"
        f"• ✂️ **Вырезать фрагмент из любого отправленного видео или аудио!**\n"
        f"• 🎵 Конвертировать любое видео в MP3.\n"
        f"• 🔍 Искать ролик на YouTube прямо по названию.\n\n"
        f"📊 `/stats` — Ваша статистика\n"
    )
    if user_id in config.ADMIN_IDS:
        welcome_text += f"⚙️ `/admin` — Панель администратора\n"
        
    reply_kb = get_main_reply_keyboard(user_id)
    if not is_sub:
        welcome_text += "\n⚠️ Пожалуйста, подпишитесь на каналы ниже для доступа:"
        await message.answer(welcome_text, reply_markup=get_subscription_keyboard(channels))
    else:
        welcome_text += "\n📥 Отправьте мне **ссылку**, **файл** или **текст для поиска**!"
        await message.answer(welcome_text, reply_markup=reply_kb)

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
        
        if f_info['media_type'] == 'audio':
            await bot.send_audio(chat_id=message.from_user.id, audio=types.FSInputFile(trimmed_path), caption=caption, parse_mode="Markdown")
        else:
            await bot.send_video(chat_id=message.from_user.id, video=types.FSInputFile(trimmed_path), caption=caption, parse_mode="Markdown")
            
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

# --- ОБРАБОТКА ССЫЛОК И ПОИСКА ---

@dp.message(F.text.regexp(r'^https?://[^\s]+'))
async def handle_link(message: types.Message):
    if not await ensure_approved_access(message):
        return
        
    user_id = message.from_user.id
    url = message.text.strip()
    
    is_sub, channels = await check_user_subscription(user_id)
    if not is_sub:
        await message.answer("⚠️ Для скачивания подпишитесь на каналы:", reply_markup=get_subscription_keyboard(channels))
        return
        
    status_msg = await message.answer("🔍 Анализирую ссылку, подождите...")
    
    try:
        info = await asyncio.to_thread(downloader.get_video_info, url)
        
        req_id = f"dl_{os.urandom(6).hex()}"
        pending_downloads[req_id] = {
            'url': url,
            'title': info['title']
        }
        
        builder = InlineKeyboardBuilder()
        builder.button(text="🎬 1080p (Высокое)", callback_data=f"q:{req_id}:1080p")
        builder.button(text="🎬 720p (Среднее)", callback_data=f"q:{req_id}:720p")
        builder.button(text="🎬 480p (Низкое)", callback_data=f"q:{req_id}:480p")
        builder.button(text="🎵 MP3 (Только Аудио)", callback_data=f"q:{req_id}:mp3")
        builder.button(text="✂️ Вырезать фрагмент", callback_data=f"trim_init:{req_id}")
        builder.button(text="🖼 Обложка (4K)", callback_data=f"thumb:{req_id}")
        builder.adjust(2, 2, 2)
        
        caption = f"🎥 **{info['title']}**\n\nВыберите качество или действие:"
        await status_msg.delete()
        await message.answer(caption, reply_markup=builder.as_markup(), parse_mode="Markdown")
        
    except Exception as e:
        logger.error(f"Ошибка разбора ссылки: {e}")
        await status_msg.edit_text("❌ Не удалось получить информацию о видео. Проверьте ссылку.")

@dp.message(F.text & ~F.text.startswith("/"))
async def handle_search_query(message: types.Message):
    if not await ensure_approved_access(message):
        return
        
    query = message.text.strip()
    status_msg = await message.answer(f"🔍 Ищу на YouTube: **{query}**...", parse_mode="Markdown")
    
    results = await asyncio.to_thread(downloader.search_youtube, query, 5)
    if not results:
        await status_msg.edit_text("❌ Ничего не найдено по вашему запросу.")
        return
        
    builder = InlineKeyboardBuilder()
    text = f"🔎 **Результаты поиска по запросу:** `{query}`\n\n"
    
    for idx, item in enumerate(results, 1):
        req_id = f"dl_{os.urandom(6).hex()}"
        pending_downloads[req_id] = {
            'url': item['url'],
            'title': item['title']
        }
        text += f"{idx}. **{item['title']}**\n"
        builder.button(text=f"🎬 Скачать #{idx}", callback_data=f"q:{req_id}:1080p")
        
    builder.adjust(2)
    await status_msg.delete()
    await message.answer(text, reply_markup=builder.as_markup(), parse_mode="Markdown")

@dp.callback_query(F.data.startswith("thumb:"))
async def cb_download_thumb(callback: types.CallbackQuery):
    if not await ensure_approved_access(callback):
        return
    _, req_id = callback.data.split(":")
    if req_id not in pending_downloads:
        await callback.message.edit_text("❌ Ссылка устарела.")
        await callback.answer()
        return
        
    url = pending_downloads[req_id]['url']
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
    if req_id not in pending_downloads:
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
    
    if req_id not in pending_downloads:
        await message.answer("❌ Ссылка устарела.")
        await state.clear()
        return
        
    req = pending_downloads[req_id]
    url = req['url']
    title = req['title']
    
    status_msg = await message.answer(f"⏳ Скачиваю и вырезаю фрагмент `{time_range}` из **{title}**...")
    await state.clear()
    
    try:
        file_path = await asyncio.to_thread(downloader.download_media, url, '1080p', None, None, time_range)
        file_size_mb = round(os.path.getsize(file_path) / (1024 * 1024), 2)
        caption = f"✂️ **{title}** [{time_range}]"
        
        if os.path.getsize(file_path) <= 50 * 1024 * 1024:
            await bot.send_video(chat_id=message.from_user.id, video=types.FSInputFile(file_path), caption=caption, parse_mode="Markdown")
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
    if req_id not in pending_downloads:
        await callback.message.edit_text("❌ Ссылка устарела. Отправьте ее заново.")
        await callback.answer()
        return
        
    req = pending_downloads[req_id]
    url = req['url']
    title = req['title']
    
    active_downloads[req_id] = {'cancelled': False}
    cancel_builder = InlineKeyboardBuilder()
    cancel_builder.button(text="❌ Отменить", callback_data=f"cancel:{req_id}")
    
    await callback.message.edit_text(
        f"⏳ Скачиваю **{title}** [{quality}]...\nПрогресс: 0%",
        reply_markup=cancel_builder.as_markup(),
        parse_mode="Markdown"
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
            
            progress_text = (
                f"⏳ Скачиваю **{title[:40]}** [{quality}]\n\n"
                f"📊 Прогресс: **{percent:.1f}%**\n"
                f"📦 Загружено: **{d_mb} МБ** / **{t_mb} МБ**\n"
                f"⚡ Скорость: **{speed} МБ/с**"
            )
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.run_coroutine_threadsafe(
                        callback.message.edit_text(progress_text, reply_markup=cancel_builder.as_markup(), parse_mode="Markdown"),
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
        
        await callback.message.edit_text(f"📤 Загружаю файл в Telegram ({file_size_mb} МБ)...")
        caption = f"✅ **{title}** [{quality}]"
        
        if file_size <= 49 * 1024 * 1024:
            input_file = types.FSInputFile(file_path)
            ext = os.path.splitext(file_path)[1].lower()
            if ext in ['.mp4', '.mkv', '.mov', '.avi']:
                get_dim = getattr(downloader, 'get_video_dimensions', None)
                w, h = get_dim(file_path) if callable(get_dim) else (None, None)
                await bot.send_video(
                    chat_id=user_id,
                    video=input_file,
                    caption=caption,
                    width=w,
                    height=h,
                    supports_streaming=True,
                    parse_mode="Markdown"
                )
            elif ext == '.mp3':
                await bot.send_audio(chat_id=user_id, audio=input_file, caption=caption, parse_mode="Markdown")
            else:
                await bot.send_document(chat_id=user_id, document=input_file, caption=caption, parse_mode="Markdown")
        else:
            caption_helper = f"✅ **{title}** [{quality}] (Отправлено через помощника)"
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
