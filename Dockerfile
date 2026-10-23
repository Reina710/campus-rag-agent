# 单进程 + 多线程，不是多 worker。
#
# 原因：会话状态（对话历史、命中结果）存在进程内存里。开多个 worker 时，
# 同一个用户的两次请求可能落到不同进程，第二次就找不到自己的会话了，
# 表现为"刷新一下历史没了"。
#
# 要支持多实例，得把会话挪到 Redis 之类的共享存储；这个项目的规模用不上。
# 线程数给 16：请求大部分时间在等模型返回（IO 密集），线程多才能扛住并发。

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 先装依赖再拷代码：代码改动不会让依赖层缓存失效，重建快很多
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

EXPOSE 8000

# 容器内健康检查，和 /health 是同一个口径
HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"

CMD ["sh", "-c", "gunicorn -w 1 --threads 16 -t 120 -b 0.0.0.0:${PORT:-8000} web_demo:app"]
