# -*- coding: utf-8 -*-
"""
启动预热：把"第一个用户才会踩到"的初始化成本挪到启动阶段。

为什么需要：
    流式输出测试时量到过 11 秒的首字节——同一台机器、第二次请求只要 1.4 秒。
    差的这 10 秒全是冷启动：
        - jieba 第一次分词要把词典从磁盘读进内存（约 1 秒）
        - 块向量第一次要从索引文件读、缺的块还要联网 embed
        - 到模型服务的第一条连接要建 TLS

    这些成本不会消失，只是决定由谁付：要么第一个访客付，要么启动时付。
    容器起来时没人在等，所以应该让启动付。

注意：
    LLM 那一路不预热。要想预热就得发一次真实请求，花钱且收益只有几秒；
    真要这么干就显式设 WARMUP_LLM=1，别默认打开。
"""

import logging
import os
import time

import config
import knowledge
import retriever

log = logging.getLogger("kb.warmup")


def run(with_llm=None):
    """跑一遍预热，返回每一步的耗时（毫秒）。异常不外抛——预热失败不该阻止启动。"""
    if with_llm is None:
        with_llm = os.environ.get("WARMUP_LLM") == "1"

    cost = {}

    # 1. 分词器
    t0 = time.time()
    try:
        import jieba
        jieba.initialize()
        jieba.lcut("预热")          # 触发一次真实分词，确保缓存建好
        cost["jieba_ms"] = int((time.time() - t0) * 1000)
    except Exception as e:
        cost["jieba_ms"] = -1
        log.warning("jieba 预热失败（不影响启动）：%s: %s", type(e).__name__, e)

    # 2. 知识块 + 块向量
    t0 = time.time()
    try:
        blocks = knowledge.get_blocks()
        n = retriever.warm_up(blocks)
        cost["vectors_ms"] = int((time.time() - t0) * 1000)
        cost["vectors"] = n
        cost["blocks"] = len(blocks)
    except Exception as e:
        cost["vectors_ms"] = -1
        log.warning("块向量预热失败（首个请求会补算）：%s: %s", type(e).__name__, e)

    # 3. 可选：模型连通性（一条极短的请求）
    if with_llm:
        t0 = time.time()
        try:
            import agent
            agent._get_client().chat.completions.create(
                model=config.CHAT_MODEL,
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=1,
            )
            cost["llm_ms"] = int((time.time() - t0) * 1000)
        except Exception as e:
            cost["llm_ms"] = -1
            log.warning("模型预热失败：%s: %s", type(e).__name__, e)

    log.info("warmup done: %s", cost)
    return cost
