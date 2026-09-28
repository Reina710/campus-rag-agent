# -*- coding: utf-8 -*-
"""
检索器模块：给一个问题，从知识库里挑出最相关的几块。

四个检索器干的是同一件事（打分 + 排序 + 取 Top-K），只是"怎么算相似度"不同：
    freq_search   字频版：逐字匹配，不花钱不联网
    bm25_search   分词 + IDF：治"字面匹配不准"，不花钱不联网
    embed_search  向量版：按语义匹配，要联网
    rrf_search    融合版：bm25 + embed 两路排名融合

都留着是为了能切换、能对比，评测模块会给出各自的命中率。
"""

import math
import os
import json
import config
import jieba


# ============================================================
# 检索器 A：字频版
# ============================================================
def to_vector(text):
    """把一段文字变成"每个字出现了几次"的字典。"""
    vec = {}
    for ch in text:
        if ch.strip():                       # 空格、换行不算
            vec[ch] = vec.get(ch, 0) + 1
    return vec


def cosine(a, b):
    """余弦相似度（字典版字频向量用），越大越像。"""
    dot = 0.0
    for k in a:
        if k in b:
            dot += a[k] * b[k]
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def cosine_embed(a, b):
    """余弦相似度（列表版向量用），越大越像。"""
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def tokenize(text):
    """用 jieba 把文字切词，并过滤掉没区分力的噪声词。

    过滤规则：
        1. 非空
        2. 长度 >= 2（丢掉"的/是/了"这种单字虚词）
        3. 只留字母数字（丢掉标点）
    三条都过才算一个有效的词。
    """
    tokens = []
    for w in jieba.lcut(text):
        if w.strip() and len(w) >= 2 and w.isalnum():
            tokens.append(w)
    return tokens


def _score_of(item):
    """给排序用：从 {"block": ..., "score": ...} 里取分数。"""
    return item["score"]


def freq_search(question, blocks, top_k):
    """字频检索：问题算什么字，资料算什么字，对上多少算多少分。"""
    q_vec = to_vector(question)
    scored = []
    for b in blocks:
        b_vec = to_vector(b["title"] + b["text"])
        scored.append({"block": b, "score": cosine(q_vec, b_vec)})
    scored.sort(key=_score_of, reverse=True)
    return scored[:top_k]


# ============================================================
# 检索器 C：BM25（jieba 分词 + IDF），治"字面匹配不准"
# ============================================================
def build_idf(blocks):
    """扫一遍全部块，给每个词算 IDF 权重。

    IDF 的核心：越稀有的词越有区分力。一个词只出现在 1 个块里，IDF 大；
    块块都有的词（如"研究生"）IDF 小。返回 {词: idf值}。
    """
    N = len(blocks)              # 总块数
    df = {}                      # df[词] = 这个词出现在几个块里

    # 第 1 步：统计每个词出现在几个块（块内重复不重复计，所以 set 去重）
    for b in blocks:
        words = set(tokenize(b["title"] + b["text"]))
        for w in words:
            df[w] = df.get(w, 0) + 1

    # 第 2 步：套公式把 df 换算成 idf
    idf = {}
    for w, d in df.items():
        idf[w] = math.log((N - d + 0.5) / (d + 0.5) + 1)
    return idf


def bm25_search(question, blocks, top_k):
    """BM25 的简化版：分词 + TF×IDF + 长度归一化。

    和 freq_search 的差别三处：
        1. 用 tokenize() 分词，不再逐字数
        2. 每个词乘 IDF（稀有词权重大）
        3. 长度归一化（长块不能因为词多就白占便宜）
    """
    idf = build_idf(blocks)      # 先算好每个词的权重（只算一次）

    # 把问题切成词，数每个词出现几次（TF）
    q_tf = {}
    for w in tokenize(question):
        q_tf[w] = q_tf.get(w, 0) + 1

    scored = []
    for b in blocks:
        b_words = tokenize(b["title"] + b["text"])
        if not b_words:
            scored.append({"block": b, "score": 0.0})
            continue

        # 块的 TF：每个词在这块里出现几次
        b_tf = {}
        for w in b_words:
            b_tf[w] = b_tf.get(w, 0) + 1

        # 打分：问题里每个词，用「词频 × IDF」累加
        score = 0.0
        for w, qtf in q_tf.items():
            if w in b_tf:
                score += idf.get(w, 0.0) * b_tf[w]

        # 长度归一化：除以这块的词数
        score /= len(b_words)
        scored.append({"block": b, "score": score})

    scored.sort(key=_score_of, reverse=True)
    return scored[:top_k]


# ============================================================
# 检索器 B：向量版
# ============================================================
def embed(text):
    """调阿里云，把一段文字变成 1024 维向量。"""
    from openai import OpenAI
    client = OpenAI(api_key=config.DASHSCOPE_KEY, base_url=config.DASHSCOPE_BASE_URL)
    resp = client.embeddings.create(model=config.EMBEDDING_MODEL, input=text)
    return resp.data[0].embedding


def embed_batch(texts):
    """批量版 embed：一次请求塞进多段文字，返回多个向量。

    阿里云 text-embedding-v3 的 input 支持传列表，但一次最多只收 10 段，
    超过会报 400（"batch size should not be larger than 10"）。所以这里
    每 10 段切一批循环，再把结果按顺序拼回来。
    """
    from openai import OpenAI
    client = OpenAI(api_key=config.DASHSCOPE_KEY, base_url=config.DASHSCOPE_BASE_URL)

    out = []
    batch_size = 10                       # 阿里云上限
    for i in range(0, len(texts), batch_size):
        chunk = texts[i:i + batch_size]
        resp = client.embeddings.create(model=config.EMBEDDING_MODEL, input=chunk)
        out.extend(item.embedding for item in resp.data)
    return out


# 块向量的缓存表：{块的 title+text -> 向量}。
# 块的内容固定、跟问什么问题无关，但若每问一题就把全部块重新 embed 一遍，
# 会重复调用、浪费钱。缓存让每个块只算一次，之后直接复用。
_block_vec_cache = {}


# ------------------------------------------------------------
# 向量索引落盘：把缓存表存成 json 文件，重启也能读回来
# ------------------------------------------------------------
def _index_path():
    """索引文件路径，挂在项目根下。"""
    return os.path.join(config.PROJECT_ROOT, config.INDEX_DIR, config.INDEX_FILE)


def load_index():
    """启动时试着从磁盘读回块向量。

    返回 True = 读到了（缓存表已填好）；False = 没有/读不了（要重建）。

    关键设计：json 里存的 key 是"块的 title+text"，所以知识库没变时下次启动
    直接命中；知识库一旦改，key 对不上自然 miss，会重算，不用手动删索引。
    """
    path = _index_path()
    if not os.path.exists(path):
        return False
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        _block_vec_cache.update(data)
        return True
    except (json.JSONDecodeError, OSError):
        return False   # 文件坏了就当没有，重算


def save_index():
    """把当前缓存表写进磁盘。"""
    path = _index_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(_block_vec_cache, f, ensure_ascii=False)


def _embed_block(b):
    """从一个块拿向量（只查缓存，不在这里发请求）。"""
    key = b["title"] + b["text"]
    return _block_vec_cache[key]


def _warm_up_blocks(blocks):
    """批量预热：把还没缓存的块全部算好向量。

    流程（能省则省）：
        1. 先试磁盘索引，有就整张读回来，一块都不用算
        2. 磁盘没有或缺块，就把缺的块一次性 embed_batch 算完
        3. 算完顺手落盘，下次重启直接读

    省的是网络往返次数：若每块单独 embed，36 块就是 36 次请求；
    批量一次要回来，36 块只发 4 次（每批 10 段）。
    """
    if not _block_vec_cache:          # 缓存还空着，先试磁盘索引
        load_index()

    missing = []                      # 还没算过向量的块
    for b in blocks:
        key = b["title"] + b["text"]
        if key not in _block_vec_cache:
            missing.append((key, b))
    if not missing:                   # 全在缓存或磁盘里，什么都不用做
        return

    keys = [key for key, _ in missing]
    vecs = embed_batch(keys)          # 一次请求拿回所有缺失块的向量
    for (key, _), vec in zip(missing, vecs):
        _block_vec_cache[key] = vec   # 按顺序塞回缓存

    save_index()                      # 算完立刻落盘


def embed_search(question, blocks, top_k):
    """向量检索：把问题和块都变成向量，按余弦相似度排序取 Top-K。

    流程：
        1. 预热：把缺失的块向量一次性算好（走缓存 + 磁盘索引）
        2. 问题向量每次现算（问题每次都不同）
        3. 块向量走缓存（只查不算）
        4. 逐个算余弦相似度，排序取前 top_k
    """
    _warm_up_blocks(blocks)
    q_vec = embed(question)
    scored = []
    for b in blocks:
        b_vec = _embed_block(b)
        scored.append({"block": b, "score": cosine_embed(q_vec, b_vec)})
    scored.sort(key=_score_of, reverse=True)

    return scored[:top_k]


# ============================================================
# 检索器 D：RRF 融合（bm25 + embed 两路投票）
# ============================================================
def rrf_search(question, blocks, top_k):
    """Reciprocal Rank Fusion：把 bm25（字面）和 embed（语义）两路排名融合。

    跨块题的答案分散在多个块，单路检索器各捞一块、捞不全。RRF 让两路各自
    独立排名，再把排名折算成分数相加——两路都排前面的块融合分高，被捞出来。

    步骤：
        1. 两路各自对全部块排序（top_k 传 len(blocks)，等于不截断）
        2. 每个块算 rrf 分 = 1/(bm25排名+K) + 1/(embed排名+K)
        3. 按 rrf 分排序，取前 top_k
    """
    K = 60   # 平滑常数：压平"第1名和第2名之间本不该那么大的分差"

    bm25_ranked = bm25_search(question, blocks, len(blocks))
    embed_ranked = embed_search(question, blocks, len(blocks))

    def _rank_of(block, ranked_list):
        """查一个块在某个排名列表里排第几（用 is 判断是不是同一个对象）。"""
        for i, item in enumerate(ranked_list, 1):
            if item["block"] is block:
                return i
        return len(ranked_list) + 1   # 没排上记"最后一名再往后一位"

    scored = []
    for b in blocks:
        r1 = _rank_of(b, bm25_ranked)
        r2 = _rank_of(b, embed_ranked)
        score = 1.0 / (r1 + K) + 1.0 / (r2 + K)
        scored.append({"block": b, "score": score})

    scored.sort(key=_score_of, reverse=True)
    return scored[:top_k]


# ============================================================
# 对外唯一入口：按配置选一个检索器
# ============================================================
def search(question, blocks, top_k):
    if config.RETRIEVER == "rrf":
        return rrf_search(question, blocks, top_k)
    if config.RETRIEVER == "embed":
        return embed_search(question, blocks, top_k)
    if config.RETRIEVER == "bm25":
        return bm25_search(question, blocks, top_k)
    return freq_search(question, blocks, top_k)
