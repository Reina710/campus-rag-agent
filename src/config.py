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


# ------------------------------------------------------------
# 对话模型（DeepSeek）
# ------------------------------------------------------------
DEEPSEEK_KEY = _get("DEEPSEEK_KEY")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
CHAT_MODEL = "deepseek-chat"


# ------------------------------------------------------------
# 向量模型（阿里云百炼）
# ------------------------------------------------------------
DASHSCOPE_KEY = _get("DASHSCOPE_KEY")
DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
EMBEDDING_MODEL = "text-embedding-v3"


# ------------------------------------------------------------
# 检索参数
# ------------------------------------------------------------
# freq=字频 / bm25=分词+IDF / embed=向量 / rrf=融合（除 embed 外都不花钱）
RETRIEVER = "freq"
TOP_K = 3               # 每次挑几块资料喂给模型


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
