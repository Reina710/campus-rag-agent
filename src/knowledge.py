# -*- coding: utf-8 -*-
"""
知识库模块：读入 knowledge.md 并切成块（RAG 里的 chunking）。

切块方式影响检索能否命中正确内容。每块是一个字典：
    {
        "id": 0,                          # 编号，报错和评测时用来定位
        "title": "选课 · 选课时间",         # 标题（大类 · 子块）
        "text": "……"                      # 正文
    }
"""

import os
import config


# 知识库文件的位置（挂在项目根下，不受代码在 src/ 影响）
DATA_FILE = os.path.join(config.PROJECT_ROOT, "data", "knowledge.md")


def load_raw():
    """把整个文件读成一个长字符串。"""
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return f.read()


def split_by_heading(raw):
    """
    按标题切块 —— 默认的做法。

    支持两级标题：
      ## 是大类（比如"选课"），### 是子块（比如"选课时间"）。
      每个 ### 下面那段正文，单独切成一块；块标题带上"大类 · 子块"，
      这样检索时大类名也能命中（不会因为只看子块名而漏掉）。

    逻辑：一行一行读，碰到 "## " 或 "### " 开头就切一块。
    """
    blocks = []
    big = ""             # 当前大类名（## 那层）
    title = None         # None 表示"还没进正文"
    lines = []

    for line in raw.splitlines():
        if line.startswith("### "):
            # 三级标题：先收上一块，再开新块
            text = "\n".join(lines).strip()
            if text and title is not None:
                blocks.append({"title": title, "text": text})
            sub = line[4:].strip()
            title = ("%s · %s" % (big, sub)) if big else sub
            lines = []
        elif line.startswith("## "):
            # 二级标题：只是记下大类名，本身不单独成块
            text = "\n".join(lines).strip()
            if text and title is not None:
                blocks.append({"title": title, "text": text})
            big = line[3:].strip()
            title = None
            lines = []
        else:
            lines.append(line)

    # 收尾：最后一块还在 lines 里
    text = "\n".join(lines).strip()
    if text and title is not None:
        blocks.append({"title": title, "text": text})

    for i, b in enumerate(blocks):
        b["id"] = i
    return blocks


def split_by_fixed(raw, size, overlap):
    """
    按固定字数硬切 —— 用来做"切块效果对比"的对照组。

    overlap 是重叠字数：相邻两块之间故意留一段重复的内容。
    这样万一一个句子正好断在切口上，两边都还留着一半，不至于两边都捞不着。
    """
    # 先把 markdown 的标题符号去掉，只留正文
    lines = [l for l in raw.splitlines() if not l.startswith("#")]
    text = "\n".join(lines).strip()

    blocks = []
    step = size - overlap          # 每块往前挪多少字
    if step <= 0:
        step = size                # 防呆：overlap 比 size 还大就没法切了
    pos = 0
    while pos < len(text):
        chunk = text[pos:pos + size].strip()
        if chunk:
            blocks.append({"title": "第 %d 块" % (len(blocks) + 1), "text": chunk})
        pos = pos + step

    for i, b in enumerate(blocks):
        b["id"] = i
    return blocks


# 上传文档目录：data/uploads/ 下的 .md / .txt 会被合并进知识库。
UPLOAD_DIR = os.path.join(config.PROJECT_ROOT, "data", "uploads")

ALLOWED_EXT = (".md", ".txt")


def split_by_paragraph(raw, stem):
    """纯文本（或没有标题结构的 markdown）：按空行分段，每段一块。"""
    blocks = []
    for i, para in enumerate(raw.split("\n\n"), 1):
        text = para.strip()
        if text:
            blocks.append({"title": "%s · 第 %d 段" % (stem, i), "text": text})
    return blocks


def load_uploads():
    """读 data/uploads/ 下所有 .md/.txt，切成块并标上来源文件名。

    为什么单独标 source：
        Web 端的文档管理需要按文件名统计块数、删除时找到对应块；
        评测里也能看出"这块是内置的还是用户上传的"。
    """
    blocks = []
    if not os.path.isdir(UPLOAD_DIR):
        return blocks

    for name in sorted(os.listdir(UPLOAD_DIR)):
        path = os.path.join(UPLOAD_DIR, name)
        low = name.lower()
        if not os.path.isfile(path) or not low.endswith(ALLOWED_EXT):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = f.read()
        except OSError:
            continue

        stem = os.path.splitext(name)[0]
        parts = split_by_heading(raw) if low.endswith(".md") else []
        if not parts:      # .txt，或没有 ##/### 结构的 .md，按空行兜底
            parts = split_by_paragraph(raw, stem)
        for b in parts:
            b["source"] = name
            blocks.append(b)
    return blocks


def load_blocks():
    """对外的唯一入口：内置 knowledge.md + data/uploads/ 下的文档，一起切块。"""
    raw = load_raw()
    if config.CHUNK_MODE == "fixed":
        blocks = split_by_fixed(raw, config.CHUNK_SIZE, config.CHUNK_OVERLAP)
    else:
        blocks = split_by_heading(raw)

    for b in blocks:
        b["source"] = "内置"
    blocks += load_uploads()

    # 合并后重新编号：上传文档的 id 会和内置块撞
    for i, b in enumerate(blocks):
        b["id"] = i
    return blocks


_CACHE = None


def get_blocks():
    """带缓存的版本：知识块是只读的，所有会话共用一份。

    上线后每个访客都 load 一次是纯浪费。进程内缓存，重启即失效——
    改了 knowledge.md 要重启才生效，这个代价换启动速度和内存是值的。
    """
    global _CACHE
    if _CACHE is None:
        _CACHE = load_blocks()
    return _CACHE


if __name__ == "__main__":
    # 直接运行本文件可查看切块结果
    bs = load_blocks()
    print("切块方式：%s" % config.CHUNK_MODE)
    print("一共切出 %d 块\n" % len(bs))
    for b in bs:
        print("[%d] %s  （%d 字）" % (b["id"], b["title"], len(b["text"])))
