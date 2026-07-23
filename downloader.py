import os
import re
import uuid
import time
import html
import logging
import subprocess
import requests
import yt_dlp
from config import DOWNLOAD_TEMP_DIR

logger = logging.getLogger(__name__)

class DownloadCancelledError(Exception):
    pass

def resolve_redirect_url(url: str) -> str:
    """Раскрывает и нормализует короткие ссылки (Shorts, youtu.be, Facebook share, TikTok vt, Twitter x.com)"""
    url = url.strip()
    if 'youtube.com/shorts/' in url:
        match = re.search(r'shorts/([a-zA-Z0-9_-]+)', url)
        if match:
            clean_yt = f"https://www.youtube.com/watch?v={match.group(1)}"
            logger.info(f"🔄 Преобразована ссылка Shorts: {url} -> {clean_yt}")
            return clean_yt
    elif 'youtu.be/' in url:
        match = re.search(r'youtu\.be/([a-zA-Z0-9_-]+)', url)
        if match:
            clean_yt = f"https://www.youtube.com/watch?v={match.group(1)}"
            logger.info(f"🔄 Преобразована ссылка youtu.be: {url} -> {clean_yt}")
            return clean_yt
    elif 'facebook.com/share/' in url or 'fb.watch/' in url:
        try:
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
            resp = requests.head(url, allow_redirects=True, timeout=3, headers=headers)
            if resp.url and resp.url != url:
                logger.info(f"🔄 Раскрыт редирект Facebook: {url} -> {resp.url}")
                return resp.url
        except Exception as e:
            logger.warning(f"⚠️ Ошибка раскрытия редиректа Facebook {url}: {e}")
    return url

# --- БЫСТРЫЕ СПЕЦИАЛИЗИРОВАННЫЕ ДВИЖКИ ДЛЯ ПЛАТФОРМ ---

def fetch_fast_tiktok(url: str) -> dict | None:
    try:
        r = requests.post('https://www.tikwm.com/api/', data={'url': url}, timeout=4)
        if r.status_code == 200:
            d = r.json().get('data', {})
            play_url = d.get('play')
            title = d.get('title') or 'TikTok Video'
            if play_url:
                logger.info("⚡ Успешное получение TikTok через TikWM API")
                return {'url': play_url, 'title': title, 'direct': True}
    except Exception as e:
        logger.warning(f"⚠️ TikWM API недоступен: {e}")
    return None

def fetch_fast_twitter(url: str) -> dict | None:
    try:
        r = requests.get(f'https://twitsave.com/info?url={url}', timeout=4)
        if r.status_code == 200:
            urls = re.findall(r'https://[^\s"\'<>]+\.mp4[^\s"\'<>]*', r.text)
            title_m = re.search(r'<p class="text-gray-600[^"]*">([^<]+)</p>', r.text)
            title = title_m.group(1).strip() if title_m else 'Twitter Video'
            if urls:
                logger.info("⚡ Успешное получение Twitter через TwitSave API")
                return {'url': urls[0], 'title': title, 'direct': True}
    except Exception as e:
        logger.warning(f"⚠️ TwitSave API недоступен: {e}")
    return None

def fetch_loader_to_url(video_url: str, quality: str = '1080p') -> str | None:
    """Обход блокировок YouTube через автономный движок Loader.to"""
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'}
    
    target_formats = ['1080', '720', '480', '360']
    if quality == '720p':
        target_formats = ['720', '1080', '480', '360']
    elif quality == '480p':
        target_formats = ['480', '720', '360']
    elif quality == 'mp3':
        target_formats = ['mp3']

    for fmt in target_formats:
        try:
            logger.info(f"🚀 Попытка выгрузки YouTube через Loader.to (формат {fmt})...")
            r1 = requests.get(f'https://loader.to/ajax/download.php?format={fmt}&url={video_url}', headers=headers, timeout=5)
            if r1.status_code == 200:
                d1 = r1.json()
                progress_url = d1.get('progress_url')
                if progress_url:
                    for _ in range(12):
                        time.sleep(1)
                        r2 = requests.get(progress_url, headers=headers, timeout=5)
                        if r2.status_code == 200:
                            d2 = r2.json()
                            d_url = d2.get('download_url')
                            if d_url:
                                logger.info(f"✅ Успешно получен прямой поток Loader.to ({fmt})!")
                                return d_url
        except Exception as e:
            logger.warning(f"⚠️ Loader.to ({fmt}) не ответил: {e}")
            continue
    return None

def get_video_info(url: str) -> dict:
    """Универсальное получение метаданных видео с автоматическим перебором клиентов и фолбэков"""
    clean_url = resolve_redirect_url(url)
    
    # 1. Проверяем быструю выгрузку для TikTok
    if 'tiktok.com' in clean_url:
        fast_tt = fetch_fast_tiktok(clean_url)
        if fast_tt:
            return {'title': fast_tt['title'], 'duration': 0, 'thumbnail': None, 'formats': [], 'direct_info': fast_tt}
            
    # 2. Проверяем быструю выгрузку для Twitter / X
    if 'x.com' in clean_url or 'twitter.com' in clean_url:
        fast_tw = fetch_fast_twitter(clean_url)
        if fast_tw:
            return {'title': fast_tw['title'], 'duration': 0, 'thumbnail': None, 'formats': [], 'direct_info': fast_tw}

    # 3. Безопасные клиенты YouTube (android_creator, tv_embedded, android_embedded)
    client_combos = [
        ['android_creator'],
        ['tv_embedded'],
        ['android_embedded'],
        ['android_vr']
    ]
    
    last_exc = None
    for combo in client_combos:
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'socket_timeout': 10,
            'retries': 2,
            'nocheckcertificate': True,
            'geo_bypass': True,
            'js_runtimes': {'node': {}},
            'extractor_args': {
                'youtube': {'player_client': combo},
                'facebook': {'facebook_mobile': [False]}
            },
            'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        }
        cookie_path = os.path.join(os.path.dirname(__file__), 'cookies.txt')
        if os.path.exists(cookie_path):
            ydl_opts['cookiefile'] = cookie_path
            
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(clean_url, download=False)
                return {
                    'title': info.get('title', 'Видео по вашей ссылке'),
                    'duration': info.get('duration', 0),
                    'thumbnail': info.get('thumbnail', None),
                    'formats': info.get('formats', [])
                }
        except Exception as e:
            last_exc = e
            logger.warning(f"⚠️ Попытка вытащить метаданные клиентом {combo} не удалась: {e}")
            continue

    logger.warning(f"⚠️ Все попытки yt_dlp завершились: {last_exc}. Используется безопасный fallback.")
    return {
        'title': 'Видео по вашей ссылке',
        'duration': 0,
        'thumbnail': None,
        'formats': []
    }

def search_youtube(query: str, limit: int = 5) -> list:
    """Ищет видео на YouTube по ключевым словам"""
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': 'in_playlist',
        'default_search': 'ytsearch',
        'nocheckcertificate': True,
        'geo_bypass': True,
        'extractor_args': {
            'youtube': {'player_client': ['android_creator', 'tv_embedded']}
        },
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    }
    try:
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
    except Exception as e:
        logger.error(f"Ошибка поиска на YouTube: {e}")
        return []

def download_thumbnail(url: str) -> str:
    """Скачивает обложку (превью) видео"""
    info = get_video_info(url)
    thumb_url = info.get('thumbnail')
    if not thumb_url:
        raise ValueError("Обложка не найдена.")
        
    file_id = str(uuid.uuid4().hex)
    output_path = os.path.join(DOWNLOAD_TEMP_DIR, f"thumb_{file_id}.jpg")
    
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

def ensure_h264_for_ios(file_path: str) -> str:
    """Конвертирует AV1/VP9 (которые заставляют iPhone показывать статичный кадр) в H.264"""
    if not os.path.exists(file_path):
        return file_path
    ext = os.path.splitext(file_path)[1].lower()
    if ext not in ['.mp4', '.mkv', '.mov', '.avi']:
        return file_path
        
    try:
        cmd_probe = [
            'ffprobe', '-v', 'error',
            '-select_streams', 'v:0',
            '-show_entries', 'stream=codec_name',
            '-of', 'default=noprint_wrappers=1:nokey=1',
            file_path
        ]
        res_probe = subprocess.run(cmd_probe, capture_output=True, text=True)
        codec = res_probe.stdout.strip().lower()
        
        if codec == 'h264':
            return file_path
            
        logger.info(f"Кодек '{codec}' заставляет iPhone зависать. Быстро конвертирую в H.264...")
        out_path = os.path.splitext(file_path)[0] + "_h264.mp4"
        cmd = [
            'ffmpeg', '-y', '-i', file_path,
            '-vf', 'scale=trunc(iw/2)*2:trunc(ih/2)*2',
            '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-profile:v', 'main',
            '-movflags', '+faststart',
            '-preset', 'ultrafast',
            '-c:a', 'copy',
            out_path
        ]
        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if res.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            try:
                os.remove(file_path)
            except Exception:
                pass
            return out_path
    except Exception as e:
        logger.error(f"Ошибка проверки или конвертации в H.264: {e}")
        
    return file_path

def compress_video_for_bot_api(input_path: str) -> str:
    """Сжимает видео от 48 МБ до 100 МБ до 47 МБ для мгновенной отправки через Bot API"""
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
                '-vf', 'scale=trunc(iw/2)*2:trunc(ih/2)*2',
                '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-profile:v', 'main',
                '-movflags', '+faststart',
                '-crf', '26', '-preset', 'ultrafast',
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

def download_direct_url(direct_url: str, output_path: str, progress_callback=None, cancel_check_callback=None) -> str:
    """Скачивает файл по прямому MP4 URL с отслеживанием прогресса"""
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    response = requests.get(direct_url, headers=headers, stream=True, timeout=15)
    response.raise_for_status()
    total_length = int(response.headers.get('content-length', 0))
    
    downloaded = 0
    start_time = time.time()
    last_update = 0
    
    with open(output_path, 'wb') as f:
        for chunk in response.iter_content(chunk_size=1024 * 64):
            if cancel_check_callback and cancel_check_callback():
                raise DownloadCancelledError("Отменено.")
            if chunk:
                f.write(chunk)
                downloaded += len(chunk)
                now = time.time()
                if progress_callback and (now - last_update >= 1.5):
                    last_update = now
                    elapsed = now - start_time
                    speed = downloaded / elapsed if elapsed > 0 else 0
                    percent = (downloaded / total_length * 100) if total_length > 0 else 0
                    progress_callback({
                        'percent': percent,
                        'downloaded_mb': round(downloaded / (1024 * 1024), 1),
                        'total_mb': round(total_length / (1024 * 1024), 1) if total_length > 0 else 0,
                        'speed_mb': round(speed / (1024 * 1024), 1) if speed else 0
                    })
    return output_path

def download_media(url: str, quality: str = '1080p', progress_callback=None, cancel_check_callback=None, time_range: str = None) -> str:
    """Скачивает медиа по ссылке с умным выбором движка, отслеживанием прогресса и отмены"""
    clean_url = resolve_redirect_url(url)
    file_id = str(uuid.uuid4().hex)
    out_path = os.path.join(DOWNLOAD_TEMP_DIR, f"{file_id}.mp4")

    # 1. Быстрое скачивание для TikTok
    if 'tiktok.com' in clean_url:
        fast_tt = fetch_fast_tiktok(clean_url)
        if fast_tt:
            download_direct_url(fast_tt['url'], out_path, progress_callback, cancel_check_callback)
            out_path = ensure_h264_for_ios(out_path)
            out_path = compress_video_for_bot_api(out_path)
            return out_path

    # 2. Быстрое скачивание для Twitter / X
    if 'x.com' in clean_url or 'twitter.com' in clean_url:
        fast_tw = fetch_fast_twitter(clean_url)
        if fast_tw:
            download_direct_url(fast_tw['url'], out_path, progress_callback, cancel_check_callback)
            out_path = ensure_h264_for_ios(out_path)
            out_path = compress_video_for_bot_api(out_path)
            return out_path

    # 3. Автономная выгрузка YouTube через Loader.to (Полный обход IP блокировок YouTube)
    if 'youtube.com' in clean_url or 'youtu.be' in clean_url:
        loader_stream = fetch_loader_to_url(clean_url, quality)
        if loader_stream:
            download_direct_url(loader_stream, out_path, progress_callback, cancel_check_callback)
            out_path = ensure_h264_for_ios(out_path)
            out_path = compress_video_for_bot_api(out_path)
            return out_path

    # 4. Резервное скачивание через yt_dlp с незаблокированными клиентами (android_creator, tv_embedded)
    def ytdlp_progress_hook(d):
        try:
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
        except Exception as pe:
            if isinstance(pe, yt_dlp.utils.DownloadCancelled):
                raise pe
            pass

    out_template = os.path.join(DOWNLOAD_TEMP_DIR, f"{file_id}.%(ext)s")
    
    height = 1080
    if quality == '720p':
        height = 720
    elif quality == '480p':
        height = 480

    client_combos = [
        ['android_creator'],
        ['tv_embedded'],
        ['android_embedded'],
        ['android_vr']
    ]

    last_error = None
    for combo in client_combos:
        common_opts = {
            'nocheckcertificate': True,
            'geo_bypass': True,
            'js_runtimes': {'node': {}},
            'socket_timeout': 15,
            'retries': 3,
            'fragment_retries': 3,
            'extractor_args': {
                'youtube': {'player_client': combo},
                'facebook': {'facebook_mobile': [False]}
            },
            'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        }
        cookie_path = os.path.join(os.path.dirname(__file__), 'cookies.txt')
        if os.path.exists(cookie_path):
            common_opts['cookiefile'] = cookie_path

        if quality == 'mp3':
            ydl_opts = {
                **common_opts,
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
            }
        else:
            ydl_opts = {
                **common_opts,
                'format': f'bestvideo[height<={height}]+bestaudio/best[height<={height}]/best',
                'merge_output_format': 'mp4',
                'outtmpl': out_template,
                'progress_hooks': [ytdlp_progress_hook],
                'quiet': True,
                'no_warnings': True,
            }
            
        if time_range:
            parts = time_range.split('-')
            start_s = parse_time(parts[0].strip('*'))
            end_s = parse_time(parts[1])
            ydl_opts['download_ranges'] = yt_dlp.utils.download_range_func(None, [(start_s, end_s)])
            ydl_opts['force_keyframes_at_cuts'] = True

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                info = ydl.extract_info(clean_url, download=True)
                final_path = None
                for file_name in os.listdir(DOWNLOAD_TEMP_DIR):
                    if file_name.startswith(file_id) and not file_name.endswith('.part'):
                        final_path = os.path.join(DOWNLOAD_TEMP_DIR, file_name)
                        break
                
                if final_path and os.path.exists(final_path):
                    final_path = ensure_h264_for_ios(final_path)
                    final_path = compress_video_for_bot_api(final_path)
                    return final_path
            except yt_dlp.utils.DownloadCancelled:
                logger.info("Скачивание остановлено пользователем.")
                raise DownloadCancelledError("Отменено.")
            except Exception as e:
                last_error = e
                logger.warning(f"⚠️ Ошибка скачивания клиентом {combo}: {e}")
                continue

    if last_error:
        raise last_error
    raise FileNotFoundError("Скачанный файл не был найден на диске.")

def trim_local_file(input_path: str, time_range: str) -> str:
    """Быстрая вырезка фрагмента из локального файла с помощью FFmpeg"""
    parts = time_range.split('-')
    start_sec = parse_time(parts[0])
    end_sec = parse_time(parts[1])
    duration = end_sec - start_sec
    if duration <= 0:
        raise ValueError("Время окончания должно быть больше времени начала.")
        
    ext = os.path.splitext(input_path)[1].lower()
    file_id = str(uuid.uuid4().hex)
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
    file_id = str(uuid.uuid4().hex)
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
