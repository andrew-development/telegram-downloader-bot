import os
import subprocess
import yt_dlp
import uuid
import logging
import requests
from config import DOWNLOAD_TEMP_DIR

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class DownloadCancelledError(Exception):
    pass

def get_video_info(url: str):
    """Базовая информация о видео"""
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
            title = info.get('title', 'Без названия')
            duration = info.get('duration', 0)
            thumbnail = info.get('thumbnail')
            return {
                'title': title,
                'duration': duration,
                'thumbnail': thumbnail,
                'url': url,
            }
        except Exception as e:
            logger.error(f"Ошибка при извлечении информации для {url}: {e}")
            raise e

def search_youtube(query: str, limit: int = 5) -> list[dict]:
    """Поиск видео на YouTube по поисковому запросу"""
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': 'in_playlist',
        'skip_download': True,
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    }
    search_url = f"ytsearch{limit}:{query}"
    results = []
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(search_url, download=False)
            entries = info.get('entries', [])
            for entry in entries:
                if entry:
                    v_url = entry.get('url') or f"https://www.youtube.com/watch?v={entry.get('id')}"
                    results.append({
                        'title': entry.get('title', 'Без названия'),
                        'url': v_url,
                        'duration': entry.get('duration', 0)
                    })
        except Exception as e:
            logger.error(f"Ошибка при поиске на YouTube для '{query}': {e}")
    return results

def download_thumbnail(url: str) -> str:
    """Скачивает обложку/превью видео в максимальном разрешении"""
    info = get_video_info(url)
    thumb_url = info.get('thumbnail')
    if not thumb_url:
        raise ValueError("Обложка для данного видео не найдена.")
        
    file_id = str(uuid.uuid4())
    save_path = os.path.join(DOWNLOAD_TEMP_DIR, f"thumb_{file_id}.jpg")
    
    resp = requests.get(thumb_url, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }, timeout=15)
    
    if resp.status_code == 200:
        with open(save_path, 'wb') as f:
            f.write(resp.content)
        return save_path
    else:
        raise Exception(f"Не удалось скачать обложку, статус: {resp.status_code}")

def download_media(url: str, quality: str, progress_callback=None, cancel_checker=None, time_range: str = None) -> str:
    """Скачивает медиафайл или указанный временной отрезок"""
    file_id = str(uuid.uuid4())
    
    def ytdlp_progress_hook(d):
        if cancel_checker and cancel_checker():
            raise yt_dlp.utils.DownloadCancelled("Скачивание отменено пользователем")
            
        if d['status'] == 'downloading' and progress_callback:
            downloaded = d.get('downloaded_bytes', 0)
            total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
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
    
    # Попытка вырезки без перекодировки (-c copy) — происходит мгновенно
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
        # Если без перекодировки не вышло, делаем точную перекодировку
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
