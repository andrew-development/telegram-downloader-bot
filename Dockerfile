FROM python:3.12-slim

# Устанавливаем системные зависимости (FFmpeg)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Копируем требования и устанавливаем библиотеки
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем все файлы проекта
COPY . .

# Создаем папку для загрузок
RUN mkdir -p downloads

# Запускаем бота в unbuffered режиме
CMD ["python", "-u", "main.py"]
