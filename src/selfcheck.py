# -*- coding: utf-8 -*-
"""
启动自检：服务起来之前，先把"一定会出事"的配置查一遍。

为什么要有这一步：
    容器起来后如果知识库是空的、密钥没配，服务照样会监听端口、/health
    也照样返回 200，然后每个用户都问不出东西——排障时最难查的就是
    "服务活着但什么都不对"。宁可启动时吼一声，也别让它在半死不活的状态下跑。

两级结论：
    FATAL   必然不可用，直接拒绝启动（知识库 0 块、检索器名字写错）
    WARN    能跑但会降级，只告警（缺某个 key、cookie 密钥随机、索引过期）

为什么缺 key 不 FATAL：
    很多平台先起容器再注入密钥；而且缺 DASHSCOPE_KEY 时检索会降级成 BM25，
    服务仍有价值。这类问题该让人看见，但不该让容器反复重启。
"""

import json
import os

import config
import knowledge

VALID_RETRIEVERS = ("freq", "bm25", "embed", "rrf", "hybrid")
VALID_CHUNK_MODES = ("heading", "fixed")


def _writable(path):
    """目录能不能写：真正写一个临时文件，别只信 os.access。"""
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".write_probe")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
        return True
    except OSError:
        return False


def run(strict=True):
    """跑一遍自检，返回 {"ok", "errors", "warnings", "info"}。

    strict=True 时只要有 FATAL 就抛异常，让容器启动失败——
    启动失败会被人看见，半死不活不会。
    """
    errors, warnings, info = [], [], {}

    # ---------- 知识库 ----------
    try:
        blocks = knowledge.get_blocks()
        info["blocks"] = len(blocks)
        if not blocks:
            errors.append("知识库切出 0 块：检查 data/knowledge.md 是否存在且含 '### ' 标题")
        else:
            empty_titles = [b["id"] for b in blocks if not b.get("title")]
            if empty_titles:
                warnings.append("有 %d 个块没有标题（检索时无法按标题命中）：%s"
                                % (len(empty_titles), empty_titles[:5]))
            short = [b["title"] for b in blocks if len(b.get("text", "")) < 60]
            if short:
                warnings.append("%d 个块正文不足 60 字，很容易答不出细节：%s"
                                % (len(short), short[:3]))
    except FileNotFoundError:
        errors.append("找不到 data/knowledge.md")
        info["blocks"] = 0
    except Exception as e:
        errors.append("知识库加载失败：%s: %s" % (type(e).__name__, e))
        info["blocks"] = 0

    # ---------- 检索配置 ----------
    info["retriever"] = config.RETRIEVER
    if config.RETRIEVER not in VALID_RETRIEVERS:
        errors.append("RETRIEVER=%r 不是可用值，可选：%s"
                      % (config.RETRIEVER, " / ".join(VALID_RETRIEVERS)))
    if config.CHUNK_MODE not in VALID_CHUNK_MODES:
        errors.append("CHUNK_MODE=%r 不是可用值，可选：heading / fixed" % config.CHUNK_MODE)
    if config.TOP_K < 1:
        errors.append("TOP_K 必须 >= 1")

    # ---------- 密钥 ----------
    info["llm_configured"] = bool(config.DEEPSEEK_KEY)
    info["embedding_configured"] = bool(config.DASHSCOPE_KEY)
    if not config.DEEPSEEK_KEY:
        warnings.append("未配置 DEEPSEEK_KEY：所有问答都会返回兜底提示")
    if not config.DASHSCOPE_KEY:
        warnings.append("未配置 DASHSCOPE_KEY：向量与 rerank 不可用，检索降级为 BM25")

    # ---------- cookie 密钥 ----------
    if not config.APP_SECRET:
        warnings.append("未配置 APP_SECRET：cookie 签名密钥每次重启随机生成，"
                        "重启后所有会话丢失；多实例部署还会互相验不过签名")

    # ---------- 磁盘可写 ----------
    mem_dir = os.path.join(config.PROJECT_ROOT, "data", "memory")
    if not _writable(mem_dir):
        errors.append("目录不可写：%s（每轮对话都要落盘，不可写会导致历史丢失）" % mem_dir)
    info["memory_dir"] = mem_dir

    # ---------- 向量索引新鲜度 ----------
    index_path = os.path.join(config.PROJECT_ROOT, config.INDEX_DIR, config.INDEX_FILE)
    if not os.path.exists(index_path):
        warnings.append("向量索引不存在（首次启动会重建，冷启动会慢一些，属正常）")
    else:
        try:
            with open(index_path, "r", encoding="utf-8") as f:
                idx = json.load(f)
            if isinstance(idx, dict) and info.get("blocks") and len(idx) != info["blocks"]:
                warnings.append("向量索引 %d 条、知识库 %d 块，数量不一致："
                                "改过 knowledge.md 后需要重建索引"
                                % (len(idx), info["blocks"]))
        except Exception:
            warnings.append("向量索引文件读取失败，建议删除后重建")

    # ---------- 首页展示的评测数字 ----------
    summary_path = os.path.join(config.PROJECT_ROOT, "data", "eval_summary.json")
    if not os.path.exists(summary_path):
        warnings.append("缺少 data/eval_summary.json：首页统计卡会显示占位符")

    ok = not errors
    report = {"ok": ok, "errors": errors, "warnings": warnings, "info": info}

    if strict and not ok:
        raise RuntimeError("启动自检未通过：\n  - " + "\n  - ".join(errors))
    return report
