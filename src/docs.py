# -*- coding: utf-8 -*-
"""
文档管理：把知识库从"写死一个文件"变成"可以上传、可以删除"。

存储布局：
    data/knowledge.md   内置知识库（只读，不允许删/覆盖）
    data/uploads/       用户上传的文档（.md / .txt），会被 knowledge.load_blocks() 合并

上传/删除后必须做三件事，漏一件就会出现"页面上看得到、检索搜不到"：
    1. 清块缓存 knowledge._CACHE
    2. 清工具缓存 kb_tools._BLOCKS（search_knowledge 用的是它）
    3. 增量补向量 retriever.warm_up()（内部会 prune 掉已删文档的旧向量）

为什么不做"整库重建"：54 块全量 embed 要几十次请求；增量补只算新加的那几块。
"""

import os
import re

import config
import knowledge


ALLOWED_EXT = (".md", ".txt")
MAX_BYTES = 512 * 1024          # 单文件上限：够放一份培养方案，也不至于被大文件打爆
BUILTIN_SOURCE = "内置"
BUILTIN_NAME = "knowledge.md"


def upload_dir():
    """上传目录，不存在就建。"""
    d = os.path.join(config.PROJECT_ROOT, "data", "uploads")
    os.makedirs(d, exist_ok=True)
    return d


def _safe_name(name):
    """只取文件名本身（去掉路径），非法字符换成下划线，限制长度。

    防的是 "../" 这类路径穿越：用户传 ../../.env 时不至于写到项目外。
    """
    name = os.path.basename((name or "").replace("\\", "/")).strip()
    name = re.sub(r"[^\w\u4e00-\u9fff.\-]", "_", name)
    return name[:80]


def _builtin_size():
    path = os.path.join(config.PROJECT_ROOT, "data", BUILTIN_NAME)
    return os.path.getsize(path) if os.path.exists(path) else 0


def count_docs():
    """文档数：内置 1 份 + uploads/ 下的文件数。

    不解析、不切块，只数文件——/health 会频繁调用，不能每次都把知识库读一遍。
    """
    n = 1
    d = upload_dir()
    if os.path.isdir(d):
        n += sum(1 for name in os.listdir(d) if name.lower().endswith(ALLOWED_EXT))
    return n


def list_docs():
    """列出内置文档 + 上传文档，各带块数。解析一次，避免每项都重读文件。"""
    blocks = knowledge.load_blocks()
    count = {}
    for b in blocks:
        src = b.get("source", BUILTIN_SOURCE)
        count[src] = count.get(src, 0) + 1

    docs = [{
        "name": BUILTIN_NAME,
        "builtin": True,
        "blocks": count.get(BUILTIN_SOURCE, 0),
        "size": _builtin_size(),
    }]

    d = upload_dir()
    for name in sorted(os.listdir(d)):
        path = os.path.join(d, name)
        if os.path.isfile(path) and name.lower().endswith(ALLOWED_EXT):
            docs.append({
                "name": name,
                "builtin": False,
                "blocks": count.get(name, 0),
                "size": os.path.getsize(path),
            })
    return docs


def save(name, data):
    """保存一个上传文档。返回 (ok, message)。"""
    name = _safe_name(name)
    if not name:
        return False, "文件名不能为空"
    if not name.lower().endswith(ALLOWED_EXT):
        return False, "只支持 .md / .txt"
    if name == BUILTIN_NAME:
        return False, "不能覆盖内置知识库"
    if len(data) > MAX_BYTES:
        return False, "文件超过 512KB"
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return False, "文件不是 UTF-8 编码，请另存为 UTF-8 再传"
    if not text.strip():
        return False, "文件是空的"

    with open(os.path.join(upload_dir(), name), "wb") as f:
        f.write(data)

    n = rebuild()
    return True, "已上传 %s（知识库现有 %d 块）" % (name, n)


def remove(name):
    """删除一个上传文档。内置知识库不允许删。"""
    name = _safe_name(name)
    if name == BUILTIN_NAME:
        return False, "内置知识库不能删除"
    path = os.path.join(upload_dir(), name)
    if not os.path.isfile(path):
        return False, "文件不存在"
    os.remove(path)

    n = rebuild()
    return True, "已删除 %s（知识库现有 %d 块）" % (name, n)


def rebuild():
    """上传/删除后重建：清两级缓存 + 增量补向量。返回当前块数。"""
    knowledge._CACHE = None
    try:
        import kb_tools
        kb_tools._BLOCKS = None
    except Exception:
        pass

    blocks = knowledge.get_blocks()
    try:
        import retriever
        retriever.warm_up(blocks)      # 内部：补缺失向量 + prune 旧向量 + 落盘
    except Exception:
        # 向量服务不可用时不算致命：BM25/字频还能用，只是新块暂时没有向量
        pass
    return len(blocks)
