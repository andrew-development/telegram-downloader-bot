import asyncio
import logging
from bot import bot, dp
from helper import helper_app
import database
import config

# Настройка детального логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

async def main():
    # 1. Проверяем корректность конфигурации
    if not config.BOT_TOKEN or config.BOT_TOKEN == "your_bot_token_here":
        logger.error("❌ Заполните BOT_TOKEN в файле .env!")
        return
    if not config.API_ID or config.API_ID == "your_api_id_here" or not config.API_HASH or config.API_HASH == "your_api_hash_here":
        logger.error("❌ Заполните API_ID и API_HASH в файле .env!")
        return
        
    # 2. Инициализируем базу данных
    logger.info("Инициализация базы данных...")
    database.init_db()

    # 3. Запуск юзербота-помощника
    logger.info("Запуск юзербота-помощника...")
    # При первом запуске в терминале появится запрос на ввод телефона и кода подтверждения
    await helper_app.start()
    
    # Проверяем успешность входа
    me = await helper_app.get_me()
    logger.info(f"✅ Юзербот-помощник запущен под аккаунтом: @{me.username or me.first_name} (ID: {me.id})")

    try:
        # 4. Запуск основного бота (aiogram)
        logger.info("Запуск основного бота Telegram...")
        # Сбрасываем старые обновления, накопившиеся пока бот был оффлайн
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        # 5. Гарантированно выключаем юзербота при остановке скрипта
        logger.info("Остановка юзербота-помощника...")
        await helper_app.stop()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен вручную.")
