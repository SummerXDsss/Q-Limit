"""
WSGI 入口（gunicorn 等生产服务器使用）
进程内有钉钉 Stream 线程和配置热更新等状态，只能单 worker 运行：
    gunicorn --workers 1 --threads 8 wsgi:app
"""
from app import app, start_background_services

start_background_services()
