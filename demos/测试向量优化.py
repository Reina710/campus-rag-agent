# -*- coding: utf-8 -*-
"""
向量检索性能测试 —— 一次跑完，直接看"三层优化"省了多少次调用。

怎么跑：
    在 ca-test 环境里执行本文件（jieba 和 openai 只装在那个环境）：
        python 测试向量优化.py

它会依次演示三种场景，并把每次"打了几次电话"打出来：
    场景 1（冷启动）  ：索引还没有 → 批量算 36 块 + 1 个问题
    场景 2（重启后）  ：索引在磁盘上 → 36 块全读盘，只算 1 个问题
    场景 3（缓存命中）：同一次运行里再问 → 连问题都只算 1 次

对照（优化前）：每问一题，36 块 + 1 问题 = 37 次网络请求。
"""

import os
import shutil
import sys

# 核心代码在 src/ 里，把 src/ 加进搜索路径，才能 import 到
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import config
config.RETRIEVER = "embed"   # 强制走向量检索，才能看到 embed 的调用次数

import retriever
import knowledge


# ---- 计数器：给 embed / embed_batch 两个函数各套一层"记账" ----
# 原理：把原来的函数存下来，换成一个"先 +1 再调用原函数"的新函数。
# 这样不改 retriever 一行代码，就能数出它到底调了几次接口。
_counter = {"embed": 0, "embed_batch": 0}
_real_embed = retriever.embed
_real_batch = retriever.embed_batch


def _counted_embed(text):
    _counter["embed"] += 1
    return _real_embed(text)


def _counted_batch(texts):
    _counter["embed_batch"] += 1
    return _real_batch(texts)


retriever.embed = _counted_embed
retriever.embed_batch = _counted_batch


def _reset_counter():
    _counter["embed"] = 0
    _counter["embed_batch"] = 0


def _report(label):
    print("  %s" % label)
    print("    单段 embed 调用 %d 次，批量 embed_batch 调用 %d 次" %
          (_counter["embed"], _counter["embed_batch"]))
    print("    块缓存里现在有 %d 块向量" % len(retriever._block_vec_cache))
    print()


def main():
    blocks = knowledge.load_blocks()
    print("知识库共 %d 块\n" % len(blocks))

    # 先把索引文件和进程内缓存都清掉，保证是"纯冷启动"
    idx_dir = os.path.join(config.PROJECT_ROOT, config.INDEX_DIR)
    if os.path.exists(idx_dir):
        shutil.rmtree(idx_dir)
    retriever._block_vec_cache.clear()

    # ---- 场景 1：冷启动，索引还没有 ----
    _reset_counter()
    print("【场景 1】冷启动（索引不存在，第一次跑）")
    retriever.search("研究生的培养方案有哪些要求", blocks, 3)
    _report("36 块走批量（1 次 embed_batch）+ 问题走单段（1 次 embed）")

    # ---- 场景 2：模拟重启 —— 清掉进程内缓存，但索引文件留在磁盘 ----
    _reset_counter()
    retriever._block_vec_cache.clear()
    print("【场景 2】模拟重启（进程缓存清空，索引在磁盘上）")
    retriever.search("奖学金怎么申请", blocks, 3)
    _report("36 块从磁盘读回（0 次 embed_batch）+ 问题 1 次 embed")

    # ---- 场景 3：同一次运行里再问，缓存直接命中 ----
    _reset_counter()
    print("【场景 3】同一次运行里再问一句")
    retriever.search("论文怎么开题", blocks, 3)
    _report("块和问题都在，只算 1 次问题 embed")

    print("对照：优化前每问一题 = 36 块 + 1 问题 = 37 次网络请求。")


if __name__ == "__main__":
    main()
