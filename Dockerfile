FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai

WORKDIR /app

# 国内构建可传入镜像源：--build-arg PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
ARG PIP_INDEX_URL=https://pypi.org/simple
COPY requirements.txt .
RUN pip install --index-url "$PIP_INDEX_URL" -r requirements.txt gunicorn==23.0.0

COPY . .

# 数据库和 .env 都放在 /app/data，由数据卷持久化
RUN useradd --create-home --uid 1000 qlimit \
    && mkdir -p /app/data \
    && chown -R qlimit:qlimit /app/data
USER qlimit

ENV FLASK_DEBUG=false \
    SQLITE_DB_PATH=/app/data/stock_analysis.db \
    ENV_FILE_PATH=/app/data/.env

VOLUME /app/data
EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/api/bot/status', timeout=4)"

# 进程内有钉钉 Stream 线程和配置热更新等状态，必须单 worker；并发靠线程
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "8", "--timeout", "300", "--access-logfile", "-", "wsgi:app"]
