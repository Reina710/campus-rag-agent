# -*- coding: utf-8 -*-
"""
集中管理所有可调参数和密钥。

项目参数如果散落在各文件里，改一个数字要翻遍整个项目。集中到这一个文件，
改参数只动这里，评测时对比不同配置也只看这里。

密钥不写在代码里，改从 .env 文件读（见 _load_env）。这样仓库无论打包发给谁、
还是推到 GitHub，都不会把密钥泄露出去。
"""

import os
from pathlib import Path

# 项目根目录 = 本文件（src/）的上一级。
# 代码放在 src/ 里，但 data/、index/、.env 都留在项目根，用这个变量统一
# 定位，避免各处用 __file__ 去猜相对路径。
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_env():
    """读项目根下的 .env 文件，把 KEY=VALUE 逐行写进环境变量。

    规则：
    - 跳过空行、# 开头的注释行、没有 = 的行
    - 只在环境变量里还没有同名 key 时才写入（外部传入的优先）
    """
    env_file = PROJECT_ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_env()


def _get(key, default=""):
    """读环境变量，读不到返回 default。"""
    return os.environ.get(key, default)


def _int_env(key, default):
    """环境变量转整数，非法值退回默认（部署平台填错不至于起不来）。"""
    try:
        return int(_get(key) or default)
    except (TypeError, ValueError):
        return default


# ------------------------------------------------------------
# 对话模型（DeepSeek）
# ------------------------------------------------------------
DEEPSEEK_KEY = _get("DEEPSEEK_KEY")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
CHAT_MODEL = "deepseek-chat"

# 超时与重试（上线必配，不配的话一次网络抖动就是一次 500）
LLM_TIMEOUT = 25          # 单次请求最多等几秒
LLM_MAX_RETRIES = 2       # 超时/5xx 时重试几次（4xx 不重试，重试也没用）


# ------------------------------------------------------------
# 向量模型（阿里云百炼）
# ------------------------------------------------------------
DASHSCOPE_KEY = _get("DASHSCOPE_KEY")
DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
EMBEDDING_MODEL = "text-embedding-v3"


# ------------------------------------------------------------
# 检索参数
# ------------------------------------------------------------
# freq=字频 / bm25=分词+IDF / embed=向量 / rrf=融合 / hybrid=宽召回+RRF+rerank
# 前四个是"一步到位"，hybrid 是"三段流水线"，多花一次 rerank 的钱换排序精度
RETRIEVER = "hybrid"
TOP_K = 3               # 每次挑几块资料喂给模型

# BM25 的两个超参（标准做法，1.2~2.0 / 0.5~0.75 是常用区间）
# k1 控制词频饱和：k1 越大，同一词重复出现越值钱；太大就变成"堆词就赢"
# b  控制长度惩罚：b=0 完全不看长度，b=1 完全按长度归一
BM25_K1 = 1.5
BM25_B = 0.75


# ------------------------------------------------------------
# 混合检索（宽召回 -> RRF 合并 -> rerank 精排）
# ------------------------------------------------------------
RECALL_K = 20           # 每路宽召回多少条；两路并集才是候选池
                        # 比 TOP_K 大是故意的：召回阶段求全，后面再筛
RERANK_N = 10           # 送进 rerank 的候选数（RRF 合并后取前 N 条）
                        # 候选越多越不容易漏，但请求体越大越慢
RERANK_ENABLED = True   # False = 跳过精排，直接用 RRF 顺序（省钱/压测/降级用）
RERANK_MODEL = "gte-rerank-v2"   # 阿里云百炼的交叉编码重排模型


# ------------------------------------------------------------
# 切块参数
# ------------------------------------------------------------
CHUNK_MODE = "heading"  # "heading"=按 ## 标题切（默认）；"fixed"=按固定字数硬切
CHUNK_SIZE = 300        # fixed 模式下，每块多少字
CHUNK_OVERLAP = 50      # fixed 模式下，相邻块重叠多少字


# ------------------------------------------------------------
# 向量索引文件（块向量落盘，重启不用重新 embed）
# ------------------------------------------------------------
INDEX_DIR = "index"                     # 索引目录（相对项目根）
INDEX_FILE = "block_vectors.json"       # 存 {块 key -> 向量} 的 json 文件


# ------------------------------------------------------------
# 上下文参数
# ------------------------------------------------------------
MAX_TURNS = 6           # messages 最多保留几轮对话（超出砍最老的）
                        # 注意：这是"条数"上限，已被下面的 token 预算取代

CONTEXT_MAX_TOKENS = 4000   # 对话历史的总 token 预算，超出就裁剪
                            # DeepSeek 上下文很大，这里设小一点便于观察裁剪过程


# ------------------------------------------------------------
# Agent 模式
# ------------------------------------------------------------
#   "rag"  = 代码先检索，把资料塞进 system，模型被动接收
#   "tool" = 把检索包成工具，模型自己决定什么时候查、查什么
AGENT_MODE = "tool"

MAX_LOOPS = 4           # 工具调用最多循环几圈，防止模型无限调用


# ------------------------------------------------------------
# 交互体验开关（app 里开启，评测里自动关闭）
# ------------------------------------------------------------
SHOW_THINKING = True    # True = 终端显示 Agent 的中间动作（思考/调工具/工具返回）
SUGGEST_FOLLOWUP = True # True = 回答尾部附"猜你想问"，基于对话猜 2 个后续问题


# ------------------------------------------------------------
# 持久化记忆
# ------------------------------------------------------------
MEMORY_ENABLED = True       # True = 退出时存历史、启动时读回历史
MEMORY_MAX_MESSAGES = 20    # 最多存最近多少条消息，防止文件无限增长


# ------------------------------------------------------------
# 上线相关：多会话 / 限流 / 服务
# ------------------------------------------------------------
SESSION_TTL_SECONDS = 1800   # 会话空闲 30 分钟就回收（释放内存）
MAX_SESSIONS = 200           # 进程内最多同时保留多少个会话，超了淘汰最久没动的
SESSION_COOKIE = "kb_sid"    # 浏览器里存会话号的 cookie 名

RATE_LIMIT_PER_MIN = 12      # 同一 IP 每分钟最多问几次（模型调用是花钱的）

# 同时在跑的问答数上限。超过就直接 503，不排队——
# 排队的结果是浏览器超时，用户不知道发生了什么；503 至少能读到"稍后再试"。
# 取值要比 gunicorn 的 --threads 小：留出线程给健康检查、静态页面，
# 否则健康检查会被长请求饿死，平台误判服务挂了。
MAX_CONCURRENT_ASKS = _int_env("MAX_CONCURRENT_ASKS", 8)

# 只在 HTTPS 下发 cookie。本地 http 调试必须关，否则浏览器直接不存 cookie，
# 表现为"每次刷新都换会话"。生产由反向代理终结 TLS 后打开。
COOKIE_SECURE = _get("COOKIE_SECURE") == "1"

# 是否信任 X-Forwarded-For 里的客户端 IP。
# 默认 False：这个头客户端自己也能伪造，直接信它等于限流形同虚设。
# 只有确定跑在反向代理/负载均衡后面（代理会覆盖这个头）时才设 TRUST_PROXY=1。
TRUST_PROXY = _get("TRUST_PROXY") == "1"

APP_SECRET = _get("APP_SECRET")   # cookie 签名密钥，不设则每次启动随机生成


PORT = _int_env("PORT", 8000)
