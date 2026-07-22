import asyncio
import logging
import os
import sys
import aiohttp
from aiohttp import web
from bot import bot, dp, setup_bot_commands
from helper import helper_app
import database
import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

async def handle_healthcheck(request):
    """Простой эндпоинт для проверки работы хостингом Render (HTTP 200 OK)"""
    return web.Response(text="Telegram Bot is alive and running!", status=200)

async def start_web_server():
    """Запускает мини веб-сервер на порту PORT мгновенно при старте"""
    port = int(os.getenv("PORT", 8080))
    app = web.Application()
    app.router.add_get('/', handle_healthcheck)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logger.info(f"🌐 Веб-сервер проверки здоровья успешно запущен на порту {port}")

async def keep_alive_ping():
    """Фоновый пинг 24/7 для предотвращения ухода Render в спящий режим"""
    url = os.getenv("RENDER_EXTERNAL_URL", "https://telegram-downloader-bot-zxyq.onrender.com")
    logger.info(f"🔄 Запуск 24/7 Keep-Alive пингера для {url}...")
    await asyncio.sleep(15)
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.get(url, timeout=10) as resp:
                    logger.info(f"💓 Keep-Alive ping status: {resp.status}")
            except Exception as e:
                logger.warning(f"⚠️ Keep-Alive ping warning: {e}")
            await asyncio.sleep(300) # пинг каждые 5 минут

async def main():
    # 1. ПЕРВЫМ ДЕЛОМ запускаем веб-сервер для Render Health Check
    await start_web_server()
    
    # 2. Запускаем фоновый 24/7 пингер
    asyncio.create_task(keep_alive_ping())
    
    if not config.BOT_TOKEN or config.BOT_TOKEN == "your_bot_token_here":
        logger.error("❌ Заполните BOT_TOKEN!")
        return
    if not config.API_ID or config.API_ID == "your_api_id_here" or not config.API_HASH or config.API_HASH == "your_api_hash_here":
        logger.error("❌ Заполните API_ID и API_HASH!")
        return
        
    logger.info("Инициализация базы данных...")
    try:
        database.init_db()
    except Exception as db_e:
        logger.error(f"❌ Ошибка БД: {db_e}")

    logger.info("Запуск юзербота-помощника...")
    helper_started = False
    try:
        await helper_app.start()
        me = await helper_app.get_me()
        logger.info(f"✅ Юзербот-помощник запущен под аккаунтом: @{me.username or me.first_name} (ID: {me.id})")
        helper_started = True
    except Exception as he:
        logger.error(f"⚠️ Юзербот не смог запуститься: {he}. Основной бот продолжит работу для файлов до 50 МБ.")

    try:
        # Бесконечный цикл с авто-перезапуском polling при любых сетевых сбоях
        while True:
            try:
                logger.info("🚀 Запуск основного бота Telegram (Long-Polling)...")
                await bot.delete_webhook(drop_pending_updates=False)
                await setup_bot_commands()
                await dp.start_polling(bot, handle_signals=False)
            except Exception as be:
                logger.error(f"⚠️ Сбой сети или сессии polling: {be}. Автоматический перезапуск через 3 секунды...")
                await asyncio.sleep(3)
    except Exception as fatal_e:
        logger.error(f"❌ Критическая ошибка основного бота: {fatal_e}")
    finally:
        if helper_started:
            logger.info("Остановка юзербота-помощника...")
            try:
                await helper_app.stop()
            except Exception:
                pass

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен вручную.")
    except Exception as uncaught:
        logger.error(f"❌ Неперехваченное исключение: {uncaught}")
        sys.exit(1)
