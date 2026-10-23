# 部署手册

这份文档描述的是**怎么把它部署起来**，不代表已经部署。仓库里没有生产环境地址，
也没有任何托管平台的配置——所有步骤都是本地可验证的。

---

## 0. 先看三个边界

这三条不是"以后优化"，是架构选择，决定了它能扛多大：

| 边界 | 后果 | 要突破得改什么 |
|---|---|---|
| 单进程（`-w 1`） | 只能靠线程扛并发，吃满一台机器的 CPU 就上不去 | 会话挪 Redis + 多 worker |
| 会话在进程内存 | 重启后会话丢失（历史还在磁盘，但会话对象要重建），不能水平扩多实例 | 同上 |
| 指标在进程内 | 多实例时每个进程各算各的，`/metrics` 不能直接相加 | 接 Prometheus 多实例抓取 |

对"校园知识库问答"这个量级，单进程 16 线程足够；写清楚是为了不让人误以为它能直接扛全校流量。

---

## 1. 本地验证（不花钱的部分）

```bash
# 语法与配置自检：知识库能不能切块、检索器名字对不对、目录可不可写
python -c "import sys; sys.path.insert(0,'src'); import selfcheck; print(selfcheck.run(strict=False))"

# 离线回归：不调用任何外部接口，5 秒跑完，确认检索没退化
python src/eval_pipeline.py --offline
```

自检里 `errors` 非空会直接拒绝启动——**这是故意的**。容器起来但知识库是空的，
比容器起不来的排查成本高得多。

## 2. 本地验证（花钱的部分）

```bash
python web_demo.py            # 另开终端
python tests/test_online.py   # 15 项，约 15 秒，花几分钱
```

覆盖：探针、404 是否返回 JSON、request_id、会话隔离、流式事件顺序、
流式正文与最终答案一致、限流。

---

## 3. 用 Docker 跑起来

```bash
cp .env.example .env          # 填 DEEPSEEK_KEY / DASHSCOPE_KEY / APP_SECRET
docker compose up -d --build
docker compose logs -f
curl -s localhost:8000/readyz | python -m json.tool
```

镜像里的关键设计：

- **非 root 运行**（uid 10001），容器被拿到代码也没有写系统目录的权限
- **探针用 `/livez`**，不是 `/health`——依赖欠费不该触发容器重启
- **`--graceful-timeout 30`**：收到 SIGTERM 后给在跑的回答 30 秒收尾，
  否则滚动更新会把正在生成的流式回答切断
- **`index/` 不进镜像**：容器首次启动会重新 embed 54 个块（约 6 次请求），
  用启动预热一次性付掉，不留给第一个访客

---

## 4. 反向代理（必须配，否则流式会被缓冲）

Flask 自带的服务器只能本地用。对外要挂在 nginx / Caddy 后面终止 TLS。

nginx 关键片段：

```nginx
location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;

    # 流式输出的三条命门
    proxy_buffering off;          # 不缓冲，否则 delta 会攒成一坨再吐
    proxy_cache off;
    proxy_read_timeout 120s;      # 要大于模型单次响应时间

    # 站点在 HTTPS 下时，cookie 才能设 Secure
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

配套改两个环境变量：

```
TRUST_PROXY=1        # 现在确实在代理后面了，可以信 X-Forwarded-For
COOKIE_SECURE=1      # 有 TLS 了，cookie 只走 HTTPS
```

**顺序很重要**：没有 TLS 就设 `COOKIE_SECURE=1`，浏览器会直接不存 cookie，
表现为"每次刷新都换会话、历史全丢"——这个坑排查起来很像"服务有 bug"。

---

## 5. 环境变量速查

| 变量 | 默认 | 说明 |
|---|---|---|
| `DEEPSEEK_KEY` | 空 | 必填，缺了问答只会返回兜底提示 |
| `DASHSCOPE_KEY` | 空 | 缺了向量与 rerank 不可用，检索降级为 BM25 |
| `APP_SECRET` | 随机 | **生产必填**。不填则重启丢会话、多实例验签失败 |
| `PORT` | 8000 | 监听端口 |
| `LOG_JSON` | 1 | 1=结构化日志，0=人读格式 |
| `LOG_LEVEL` | INFO | DEBUG 会把第三方库的调用细节也打出来 |
| `WARMUP` | 1 | 启动预热 jieba 与块向量 |
| `WARMUP_LLM` | 0 | 启动时预热模型连接（会花钱，默认关） |
| `MAX_CONCURRENT_ASKS` | 8 | 并发闸门，满了直接 503 不排队 |
| `RATE_LIMIT_PER_MIN` | 12 | 单 IP 每分钟配额 |
| `TRUST_PROXY` | 0 | 在反向代理后面才设 1 |
| `COOKIE_SECURE` | 0 | 有 HTTPS 才设 1 |
| `THREADS` | 16 | gunicorn 线程数，要大于 `MAX_CONCURRENT_ASKS` |

---

## 6. 上线检查清单

- [ ] `APP_SECRET` 已填且不是示例值
- [ ] `.env` 没有进 git（`git status` 干净）
- [ ] `/readyz` 返回 200 且 `blocks` 等于知识库实际块数
- [ ] `/livez`、`/readyz` 已接进平台探针，且**探针打的是 `/livez`**
- [ ] 反向代理已关 `proxy_buffering`，流式确认是"一个字一个字出来"
- [ ] 有 TLS 才开 `COOKIE_SECURE=1`
- [ ] `TRUST_PROXY` 与实际拓扑一致（不在代理后面却开 1 = 限流可绕过）
- [ ] 日志已接入平台（一行一 JSON，可按 `event` / `rid` 过滤）
- [ ] `/metrics` 已被抓取（或至少有人在看 p95）
- [ ] 限流额度与预算匹配：`RATE_LIMIT_PER_MIN × 并发 × 单价` 算一遍最坏情况
- [ ] `tests/test_online.py` 在目标环境跑过一次全绿
- [ ] 回滚方式明确（镜像 tag / 上一个 commit）

---

## 7. 出问题怎么查

日志是一行一个 JSON，字段固定：

```json
{"ts":"2026-10-23T21:04:11.113","level":"INFO","logger":"kb.web",
 "msg":"http_request","event":"http_request","path":"/ask_stream",
 "method":"POST","status":200,"cost_ms":5130,"ip":"127.0.0.1",
 "rid":"8f2ac1b7d3e4","stream":true}
```

按 `rid` 能把一次请求的所有行串起来；响应头里也带着 `X-Request-Id`，
用户报错时让他把这一串发过来即可。

| 症状 | 先看什么 | 常见原因 |
|---|---|---|
| 首字很慢（>5s） | `/metrics` 的 `kb_ask_latency_p95_seconds` | 冷启动（`WARMUP=0`）、模型侧变慢、rerank 超时后重试 |
| 大量 429 | `kb_ratelimit_hits_total` | 额度太小；或 `TRUST_PROXY` 配错导致所有人共用一个 IP |
| 大量 503 | `kb_concurrency_rejected_total` | 并发闸门满了：模型变慢导致线程被占满 |
| 刷新就丢历史 | 日志里 `session` 字段是否一直在变 | `COOKIE_SECURE=1` 但没有 HTTPS；或 `APP_SECRET` 每次重启在变 |
| 流式变"一次性吐出" | 反向代理配置 | `proxy_buffering` 没关 |
| 答不出内容但服务正常 | `/health` 的 `warnings` | 缺 key 或索引与知识库块数不一致 |

---

## 8. 要扩到多实例，改什么

不是改参数，是改存储：

1. 会话从 `SessionStore`（进程内存）换成 Redis，key 为 `sid`，value 存
   messages + last_seen，锁改成分布式锁（SETNX）
2. 记忆落盘从本地文件换成对象存储或数据库
3. 限流计数从进程内滑动窗口换成 Redis 计数
4. `-w 1` 可以放开，前面挂负载均衡，粘性会话不再必需
5. 指标改成每个实例各自被 Prometheus 抓取，用 `sum by (job)` 聚合

这一整套在"给面试官演示一个能跑的 RAG Agent"这个目标下不值得做，
写在这里是为了面试被问"怎么扩容"时能答得有边界感。
