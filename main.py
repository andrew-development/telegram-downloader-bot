import asyncio
import logging
import os
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
    # 1. ПЕРВЫМ ДЕЛОМ запускаем веб-сервер, чтобы Render моментально прошел Health Check!
    await start_web_server()
    
    if not config.BOT_TOKEN or config.BOT_TOKEN == "your_bot_token_here":
        logger.error("❌ Заполните BOT_TOKEN!")
        return
    if not config.API_ID or config.API_ID == "your_api_id_here" or not config.API_HASH or config.API_HASH == "your_api_hash_here":
        logger.error("❌ Заполните API_ID и API_HASH!")
        return
        
    logger.info("Инициализация базы данных...")
    database.init_db()

    logger.info("Запуск юзербота-помощника...")
    await helper_app.start()
    
    me = await helper_app.get_me()
    logger.info(f"✅ Юзербот-помощник запущен под аккаунтом: @{me.username or me.first_name} (ID: {me.id})")

    try:
        logger.info("Запуск основного бота Telegram...")
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        logger.info("Остановка юзербота-помощника...")
        await helper_app.stop()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен вручную.")
