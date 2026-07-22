import os
import uuid
import time
import logging
import subprocess
import yt_dlp
from config import DOWNLOAD_TEMP_DIR

logger = logging.getLogger(__name__)

class DownloadCancelledError(Exception):
    pass

def get_video_info(url: str) -> dict:
    """Получает информацию о видео (название, доступные форматы)"""
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        return {
            'title': info.get('title', 'Без названия'),
            'duration': info.get('duration', 0),
            'thumbnail': info.get('thumbnail', None),
            'formats': info.get('formats', [])
        }

def search_youtube(query: str, limit: int = 5) -> list:
    """Ищет видео на YouTube по ключевым словам"""
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': 'in_playlist',
        'default_search': 'ytsearch',
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    }
    with yt_dlp.YoutubeDL(f"ytsearch{limit}:{query}", ydl_opts) as ydl:
        info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
        results = []
        entries = info.get('entries', [])
        for entry in entries:
            if entry:
                results.append({
                    'title': entry.get('title', 'Без названия'),
                    'url': f"https://www.youtube.com/watch?v={entry.get('id')}",
                    'duration': entry.get('duration', 0)
                })
        return results

def download_thumbnail(url: str) -> str:
    """Скачивает обложку (превью) видео"""
    info = get_video_info(url)
    thumb_url = info.get('thumbnail')
    if not thumb_url:
        raise ValueError("Обложка не найдена.")
        
    file_id = str(uuid.uuid4())
    output_path = os.path.join(DOWNLOAD_TEMP_DIR, f"thumb_{file_id}.jpg")
    
    import requests
    response = requests.get(thumb_url, timeout=15)
    if response.status_code == 200:
        with open(output_path, 'wb') as f:
            f.write(response.content)
        return output_path
    raise ValueError("Не удалось скачать обложку.")

def get_video_dimensions(file_path: str) -> tuple:
    """Возвращает (width, height) видео для точной ориентации в Telegram"""
    try:
        cmd = [
            'ffprobe', '-v', 'error',
            '-select_streams', 'v:0',
            '-show_entries', 'stream=width,height',
            '-of', 'csv=s=x:p=0',
            file_path
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode == 0 and res.stdout.strip():
            w, h = res.stdout.strip().split('x')
            return int(w), int(h)
    except Exception:
        pass
    return None, None

def compress_video_for_bot_api(input_path: str) -> str:
    """Сжимает видео до 47 МБ, сохраняя точный пропорциональный размер картинки"""
    if not os.path.exists(input_path):
        return input_path
    size = os.path.getsize(input_path)
    if 48 * 1024 * 1024 < size <= 100 * 1024 * 1024:
        ext = os.path.splitext(input_path)[1].lower()
        if ext in ['.mp4', '.mkv', '.mov', '.avi']:
            logger.info(f"Сжатие файла {input_path} ({round(size/(1024*1024),1)} МБ) до 47 МБ...")
            out_path = os.path.splitext(input_path)[0] + "_compressed.mp4"
            cmd = [
                'ffmpeg', '-y', '-i', input_path,
                '-fs', '47M',
                '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '26', '-preset', 'ultrafast',
                '-c:a', 'aac', '-b:a', '128k',
                out_path
            ]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                try:
                    os.remove(input_path)
                except Exception:
                    pass
                return out_path
    return input_path

def download_media(url: str, quality: str = '1080p', progress_callback=None, cancel_check_callback=None, time_range: str = None) -> str:
    """Скачивает медиа по ссылке с отслеживанием прогресса и отмены"""
    file_id = str(uuid.uuid4())
    
    def ytdlp_progress_hook(d):
        if cancel_check_callback and cancel_check_callback():
            raise yt_dlp.utils.DownloadCancelled("Скачивание отменено пользователем.")
            
        if d['status'] == 'downloading' and progress_callback:
            total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
            downloaded = d.get('downloaded_bytes', 0)
            speed = d.get('speed', 0)
            percent = (downloaded / total * 100) if total > 0 else 0
            
            progress_callback({
                'percent': percent,
                'downloaded_mb': round(downloaded / (1024 * 1024), 1),
                'total_mb': round(total / (1024 * 1024), 1) if total > 0 else 0,
                'speed_mb': round(speed / (1024 * 1024), 1) if speed else 0
            })

    out_template = os.path.join(DOWNLOAD_TEMP_DIR, f"{file_id}.%(ext)s")
    
    if quality == 'mp3':
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': out_template,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'progress_hooks': [ytdlp_progress_hook],
            'quiet': True,
            'no_warnings': True,
            'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        }
    else:
        height = 1080
        if quality == '720p':
            height = 720
        elif quality == '480p':
            height = 480
            
        ydl_opts = {
            'format': f'bestvideo[height<={height}]+bestaudio/best[height<={height}]/bestvideo+bestaudio/best',
            'merge_output_format': 'mp4',
            'outtmpl': out_template,
            'progress_hooks': [ytdlp_progress_hook],
            'quiet': True,
            'no_warnings': True,
            'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        }
        
    if time_range:
        parts = time_range.split('-')
        start_s = parse_time(parts[0].strip('*'))
        end_s = parse_time(parts[1])
        ydl_opts['download_ranges'] = yt_dlp.utils.download_range_func(None, [(start_s, end_s)])
        ydl_opts['force_keyframes_at_cuts'] = True

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=True)
            
            final_path = None
            for file_name in os.listdir(DOWNLOAD_TEMP_DIR):
                if file_name.startswith(file_id) and not file_name.endswith('.part'):
                    final_path = os.path.join(DOWNLOAD_TEMP_DIR, file_name)
                    break
            
            if not final_path or not os.path.exists(final_path):
                raise FileNotFoundError("Скачанный файл не был найден на диске.")
                
            # Оптимизация для мгновенной отправки видео от 48 до 100 МБ
            final_path = compress_video_for_bot_api(final_path)
            return final_path
            
        except yt_dlp.utils.DownloadCancelled:
            logger.info("Скачивание остановлено пользователем.")
            raise DownloadCancelledError("Отменено.")
        except Exception as e:
            logger.error(f"Ошибка при скачивании {url}: {e}")
            raise e

def trim_local_file(input_path: str, time_range: str) -> str:
    """Быстрая вырезка фрагмента из локального файла с помощью FFmpeg"""
    parts = time_range.split('-')
    start_sec = parse_time(parts[0])
    end_sec = parse_time(parts[1])
    duration = end_sec - start_sec
    if duration <= 0:
        raise ValueError("Время окончания должно быть больше времени начала.")
        
    ext = os.path.splitext(input_path)[1].lower()
    file_id = str(uuid.uuid4())
    output_path = os.path.join(DOWNLOAD_TEMP_DIR, f"trimmed_{file_id}{ext}")
    
    cmd = [
        'ffmpeg', '-y',
        '-ss', str(start_sec),
        '-i', input_path,
        '-t', str(duration),
        '-c', 'copy',
        output_path
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if res.returncode != 0 or not os.path.exists(output_path):
        cmd = [
            'ffmpeg', '-y',
            '-ss', str(start_sec),
            '-i', input_path,
            '-t', str(duration),
            output_path
        ]
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        
    return output_path

def convert_local_to_mp3(input_path: str) -> str:
    """Конвертирует локальный медиафайл (видео/аудио) в MP3"""
    file_id = str(uuid.uuid4())
    output_path = os.path.join(DOWNLOAD_TEMP_DIR, f"audio_{file_id}.mp3")
    cmd = [
        'ffmpeg', '-y',
        '-i', input_path,
        '-vn', '-acodec', 'libmp3lame', '-ab', '192k',
        output_path
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    return output_path

def parse_time(time_str: str) -> float:
    """Конвертирует 'MM:SS' или 'HH:MM:SS' или секунды в float"""
    parts = time_str.strip().split(':')
    if len(parts) == 1:
        return float(parts[0])
    elif len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    elif len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    return 0.0
