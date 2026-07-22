import asyncio
import logging
import os
import sys
from aiohttp import web
from bot import bot, dp
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

async def main():
    # 1. ПЕРВЫМ ДЕЛОМ запускаем веб-сервер для Render Health Check
    await start_web_server()
    
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
        logger.error(f" Ошибка БД: {db_e}")

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
        logger.info("Запуск основного бота Telegram...")
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    except Exception as be:
        logger.error(f"❌ Критическая ошибка основного бота: {be}")
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
