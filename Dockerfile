FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libopus0 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PREFIX="h." CLUSTER_ID=0 TOTAL_CLUSTERS=1
CMD ["python","bot.py"]
