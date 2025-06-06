# Base image gọn nhẹ
FROM python:3.12-slim

# Cài FFmpeg + Opus cho voice
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg libopus0 git && \
    rm -rf /var/lib/apt/lists/*

# Thư mục làm việc
WORKDIR /app

# Cài Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY . .

# Biến môi trường (trùng khớp tên code)
ENV PREFIX="h." \
    CLUSTER_ID=0 \
    TOTAL_CLUSTERS=1 \
    LAVALINK_HOST="lava2.horizxon.studio" \
    LAVALINK_PORT=80 \
    LAVALINK_PASSWORD="horizxon.studio" \
    LAVALINK_SECURE="false"

# Mặc định khởi chạy file chính
CMD ["python", "bot.py"]
