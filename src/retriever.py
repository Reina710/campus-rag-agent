# -*- coding: utf-8 -*-
"""
检索器模块：给一个问题，从知识库里挑出最相关的几块。

四个检索器干的是同一件事（打分 + 排序 + 取 Top-K），只是"怎么算相似度"不同：
    freq_search   字频版：逐字匹配，不花钱不联网
    bm25_search   分词 + IDF：治"字面匹配不准"，不花钱不联网
    embed_search  向量版：按语义匹配，要联网
    rrf_search    融合版：bm25 + embed 两路排名融合

第五个不是"又一个检索器"，而是一条流水线（见文件下半部分）：
    hybrid_search 宽召回 -> RRF 合并 -> rerank 精排
    它调用的还是上面几个，只是把"召回"和"排序"拆成两个阶段分别优化。

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
    """BM25：TF 饱和 + IDF + 按平均块长归一。

    注意这里和"TF×IDF÷块长"的区别，之前写成后者时踩过坑：
        1. 词频要饱和——同一个词出现 10 次不该比出现 1 次重要 10 倍
           （分母 f + k1*... 让增长变缓，不会无限累加）
        2. 长度惩罚要看"这块比平均块长多少"，而不是"这块有多长"
           （直接除块长会让 40 字的块无条件压过 200 字的块）
    这两条合起来才是 BM25；少了任何一个，短块就会霸榜。
    """
    idf = build_idf(blocks)      # 先算好每个词的权重（只算一次）

    # 先切好每块的词和词频，顺便算平均块长（avgdl 是 BM25 的基准线）
    docs = []
    for b in blocks:
        words = tokenize(b["title"] + b["text"])
        tf = {}
        for w in words:
            tf[w] = tf.get(w, 0) + 1
        docs.append((b, len(words), tf))
    avgdl = sum(dl for _, dl, _ in docs) / float(max(len(docs), 1)) or 1.0

    k1 = config.BM25_K1
    b_param = config.BM25_B
    q_terms = set(tokenize(question))     # 去重：同一个词问两遍不该算两份

    scored = []
    for block, dl, tf in docs:
        norm = k1 * (1 - b_param + b_param * dl / avgdl)
        score = 0.0
        for w in q_terms:
            f = tf.get(w, 0)
            if f:
                # 饱和项：f 从 1 涨到 2 收益明显，从 10 涨到 11 几乎没用
                score += idf.get(w, 0.0) * f * (k1 + 1) / (f + norm)
        scored.append({"block": block, "score": score})

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


def prune_index(blocks):
    """丢掉不属于当前知识库的向量。

    索引的 key 是 title+text，改了知识库后旧 key 不会被命中，但会一直留在
    json 里——文件只涨不降，自检每次启动都会报"数量不一致"。
    这里按当前块集合裁一遍，返回裁掉多少条。
    """
    keep = {b["title"] + b["text"] for b in blocks}
    stale = [k for k in _block_vec_cache if k not in keep]
    for k in stale:
        del _block_vec_cache[k]
    return len(stale)


def warm_up(blocks):
    """启动预热：把块向量算好并落盘。

    不预热的话，第一个提问的用户要替所有人等冷启动（读索引 + 缺块要 embed）。
    容器刚起来时最容易被第一个请求打到，这个代价应该由启动阶段承担。
    """
    _warm_up_blocks(blocks)
    return len(_block_vec_cache)


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
        # 但历史版本残留的向量要清掉，否则索引文件只涨不降
        if prune_index(blocks):
            save_index()
        return

    keys = [key for key, _ in missing]
    vecs = embed_batch(keys)          # 一次请求拿回所有缺失块的向量
    for (key, _), vec in zip(missing, vecs):
        _block_vec_cache[key] = vec   # 按顺序塞回缓存

    prune_index(blocks)               # 顺手清掉旧版本块的向量
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
# 混合检索：宽召回 -> RRF 合并 -> rerank 精排
# ============================================================
def _rrf_merge(bm25_ranked, embed_ranked, candidates, k=60):
    """在给定候选集上算 RRF 分并排序。

    和 rrf_search 的区别：rrf_search 对"全部块"算分，这里只算候选池里的块。
    不在某一路排名里的块（宽召回时可能发生）按"该路最后一名再往后一位"
    记名次，贡献极小但不为零 —— 免得漏掉只在单路出现的正确答案。

    ranked 列表用 id 建索引，避免 rrf_search 里那种逐块线性查找（O(n²)）。
    """
    r_bm = {h["block"]["id"]: i for i, h in enumerate(bm25_ranked, 1)}
    r_em = {h["block"]["id"]: i for i, h in enumerate(embed_ranked, 1)}
    far = max(len(bm25_ranked), len(embed_ranked)) + 1

    scored = []
    for b in candidates:
        s = (1.0 / (r_bm.get(b["id"], far) + k)
             + 1.0 / (r_em.get(b["id"], far) + k))
        scored.append({"block": b, "score": s})
    scored.sort(key=_score_of, reverse=True)
    return scored


def retrieve_pool(question, blocks, recall_k=None, merge_n=None):
    """宽召回：两路各捞 recall_k 条，并集去重，再用 RRF 合并。

    为什么先宽后窄：
        如果一开始就只取 3 条，正确答案排第 4 就永远救不回来；
        先捞 20 条（recall@20 通常接近 100%），把"能不能找到"和
        "排得准不准"拆成两个问题，各自用合适的手段解决。

    返回 RRF 分降序的候选列表（长度 <= merge_n），还没做精排。
    """
    if recall_k is None:
        recall_k = config.RECALL_K
    if merge_n is None:
        merge_n = config.RERANK_N

    # 两路都排全量：既然要算向量，不如一次排完，后面切片随便取
    bm25_ranked = bm25_search(question, blocks, len(blocks))
    embed_ranked = embed_search(question, blocks, len(blocks))

    # 并集：任一路进前 recall_k 就留下
    pool_ids = set()
    for h in bm25_ranked[:recall_k]:
        pool_ids.add(h["block"]["id"])
    for h in embed_ranked[:recall_k]:
        pool_ids.add(h["block"]["id"])
    pool = [b for b in blocks if b["id"] in pool_ids]

    return _rrf_merge(bm25_ranked, embed_ranked, pool)[:merge_n]


def hybrid_search(question, blocks, top_k):
    """三段流水线：宽召回 -> RRF 合并 -> rerank 精排 -> 取 Top-K。

    两层降级（重要）：
        第一层：rerank 失败（没装、超时、限流）-> 用 RRF 顺序，排序差一点但能答
        第二层：连向量都不可用（key 失效、断网）-> 退回纯 BM25 字面检索

    为什么兜底要放在这里而不是各路内部：
        这是整条链路的出口。用户不关心里面哪一环挂了，只关心"还能不能答"。
        组件内部各自兜底会让失败信号被吞掉，出口兜底至少能保证行为可预期。
    """
    try:
        cands = retrieve_pool(question, blocks)
    except Exception:
        # 向量服务不可用的兜底。这里故意不细分异常类型：
        # 到了出口这一层，任何原因都只对应一个动作——退到不依赖网络的检索。
        return bm25_search(question, blocks, top_k)

    if not cands:
        return []

    if config.RERANK_ENABLED:
        import rerank as rerank_mod          # 延迟导入，避免循环依赖
        reranked = rerank_mod.rerank(question, cands)
        if reranked:                          # None / [] 都当作失败，降级
            return reranked[:top_k]

    return cands[:top_k]


# ============================================================
# 对外唯一入口：按配置选一个检索器
# ============================================================
def search(question, blocks, top_k):
    if config.RETRIEVER == "hybrid":
        return hybrid_search(question, blocks, top_k)
    if config.RETRIEVER == "rrf":
        return rrf_search(question, blocks, top_k)
    if config.RETRIEVER == "embed":
        return embed_search(question, blocks, top_k)
    if config.RETRIEVER == "bm25":
        return bm25_search(question, blocks, top_k)
    return freq_search(question, blocks, top_k)
