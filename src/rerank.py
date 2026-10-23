# -*- coding: utf-8 -*-
"""
精排模块：把候选块和问题成对送进 rerank 模型，重新打分排序。

和 embed_search 的差别（这是这一层存在的理由）：
    embed   双塔（bi-encoder）：问题和块各自算成向量，再算余弦
            -> 便宜，但问题和块从没见过面，只能比"整体像不像"
    rerank  交叉编码（cross-encoder）：问题和块拼在一起送进模型，逐字比对
            -> 贵，但能判断"这块到底答没答这个问题"

也就是说：
    embed 负责"从 38 块里捞出 10 块可能相关的"（求全，允许错）
    rerank 负责"这 10 块里到底哪 3 块最该给模型"（求精，必须准）

工程上三条约定：
    1. 失败就返回 None，绝不抛异常 —— 由调用方降级回 RRF 顺序
       （rerank 是加分项，不是命脉；它挂了系统该"稍差但能跑"）
    2. 文档必须带标题 —— 同族块（如"奖学金类型" vs "奖学金评选标准"）
       差别就写在标题上，只送正文反而更难分
    3. 分数只在同一个 query 内部可比 —— 别拿它当阈值卡，不同问题
       的分数尺度不一样，卡了就误杀

用法：
    from rerank import rerank
    ranked = rerank(question, candidates)     # None 表示这次没排成
"""

import config


def rerank(question, candidates):
    """给候选块重新打分。返回按新分数排好的列表；失败返回 None。

    candidates: [{"block": {...}, "score": ...}, ...]（RRF 合并后的候选）
    返回同结构，score 换成 rerank 的相关性分。
    """
    if not candidates:
        return []

    # 延迟 import：dashscope 只是可选依赖，没装也不该让整个项目起不来
    try:
        from dashscope import TextReRank
    except ImportError:
        return None

    # 标题带上：同族块的区分信息在标题里
    docs = ["标题：%s\n正文：%s" % (c["block"]["title"], c["block"]["text"])
            for c in candidates]

    try:
        resp = TextReRank.call(
            model=config.RERANK_MODEL,
            query=question,
            documents=docs,
            top_n=len(docs),            # 全部要回来，自己排序，不依赖服务端截断
            return_documents=False,     # 只要分数，文档本地已有，省流量
            api_key=config.DASHSCOPE_KEY,
        )
    except Exception:
        # 网络断、超时、key 失效 —— 一律当作"这题没精排"，交给上层降级
        return None

    if resp.status_code != 200:
        return None

    try:
        score_of = {r["index"]: r["relevance_score"]
                    for r in resp.output["results"]}
    except (KeyError, TypeError):
        return None

    scored = [{"block": c["block"], "score": score_of.get(i, 0.0)}
              for i, c in enumerate(candidates)]
    scored.sort(key=lambda x: -x["score"])
    return scored
