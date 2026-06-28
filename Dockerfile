FROM python:3.12-slim

# ffmpeg 用于合并/转封装，aria2 提供多线程下载
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg aria2 && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

ENV DOWNLOAD_DIR=/downloads \
    DATA_DIR=/data \
    THREADS=16

VOLUME ["/downloads", "/data"]
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
