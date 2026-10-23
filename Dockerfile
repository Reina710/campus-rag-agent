# 单进程 + 多线程，不是多 worker。
#
# 原因：会话状态（对话历史、命中结果）存在进程内存里。开多个 worker 时，
# 同一个用户的两次请求可能落到不同进程，第二次就找不到自己的会话了，
# 表现为"刷新一下历史没了"。
#
# 要支持多实例，得把会话挪到 Redis 之类的共享存储；这个项目的规模用不上。
# 线程数默认 16：请求大部分时间在等模型返回（IO 密集），线程多才扛得住并发。
# 注意 MAX_CONCURRENT_ASKS（默认 8）要小于线程数，给健康检查留出线程。

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000 \
    LOG_JSON=1 \
    WARMUP=1

WORKDIR /app

# 先装依赖再拷代码：代码改动不会让依赖层缓存失效，重建快很多
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# 不用 root 跑：容器里被拿到的代码不该有写系统目录的权限
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p data/memory index \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# 探针用 /livez 而不是 /health：
# 健康检查只该回答"进程还活着吗"；/health 里的依赖状态由监控去抓，
# 用依赖状态做容器探针会把"欠费"变成"反复重启"。
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:' + __import__('os').environ.get('PORT','8000') + '/livez', timeout=3)"

# -w 1：会话在内存，必须单进程
# --threads：IO 密集，靠线程扛并发
# --graceful-timeout：收到 SIGTERM 后给在跑的请求（含 SSE 流）收尾的时间，
#                     太短会把正在生成的回答切断
CMD ["sh", "-c", "gunicorn -w 1 --threads ${THREADS:-16} --timeout 120 --graceful-timeout 30 --keep-alive 5 -b 0.0.0.0:${PORT:-8000} web_demo:app"]
