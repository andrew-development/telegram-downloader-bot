import os
import logging
from pyrogram import Client
import config

logger = logging.getLogger(__name__)

# Инициализируем клиент Pyrogram.
# Он создаст файл сессии helper_session.session в текущей папке.
helper_app = Client(
    name="helper_session",
    api_id=config.API_ID,
    api_hash=config.API_HASH,
    workdir=os.path.dirname(os.path.abspath(__file__))
)

async def send_large_file(chat_id: int, file_path: str, caption: str = None) -> bool:
    """
    Отправляет файл пользователю от лица юзербота-помощника.
    Поддерживает отправку видео, аудио и документов весом до 2 ГБ.
    """
    try:
        ext = os.path.splitext(file_path)[1].lower()
        
        # Разрешаем пир (пользователя), чтобы Pyrogram обновил кэш
        try:
            await helper_app.get_users(chat_id)
        except Exception as pe:
            logger.warning(f"Предупреждение при разрешении пользователя {chat_id}: {pe}. Пробуем отправить напрямую.")
            
        # Отправляем видео
        if ext in ['.mp4', '.mkv', '.mov', '.avi']:
            logger.info(f"Отправка видео {file_path} пользователю {chat_id} через юзербота...")
            await helper_app.send_video(
                chat_id=chat_id,
                video=file_path,
                caption=caption
            )
        # Отправляем аудио
        elif ext == '.mp3':
            logger.info(f"Отправка аудио {file_path} пользователю {chat_id} через юзербота...")
            await helper_app.send_audio(
                chat_id=chat_id,
                audio=file_path,
                caption=caption
            )
        # Отправляем как документ (все остальные форматы)
        else:
            logger.info(f"Отправка документа {file_path} пользователю {chat_id} через юзербота...")
            await helper_app.send_document(
                chat_id=chat_id,
                document=file_path,
                caption=caption
            )
        return True
    except Exception as e:
        logger.error(f"Ошибка при отправке файла через юзербота-помощника: {e}")
        return False
