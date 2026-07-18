"""纯文本多样性/开放度代理(无 API、无 DB、无外部依赖)。

供 `analysis/v3.py`(条件×题目多样性)与 `analysis/norming.py`(主题开放度)共用。
指标依据 paper/14 §5(多样性:压缩比 CR + distinct-n + self-repetition)。
字符级 n-gram(不分词),适配日文;跨样本比较前请先 strip_format 去掉分镜模板。
"""
from __future__ import annotations

import gzip
from collections import Counter


def gzip_cr(texts: list[str]) -> float:
    """压缩比 CR = 原始字节 / gzip 后字节。越高 = 越冗余/越同质。<2 条 → nan。"""
    texts = [t for t in texts if t]
    if len(texts) < 2:
        return float("nan")
    blob = "\n".join(texts).encode("utf-8")
    comp = gzip.compress(blob, 9)
    return len(blob) / len(comp) if comp else float("nan")


def _char_ngrams(text: str, n: int) -> list[str]:
    t = "".join(text.split())  # 去空白,字符级
    return [t[i:i + n] for i in range(len(t) - n + 1)] if len(t) >= n else []


def distinct_n(texts: list[str], n: int = 2) -> float:
    """distinct-n = 去重 n-gram / 总 n-gram(字符级)。越高 = 越发散。"""
    all_ng: list[str] = []
    for t in texts:
        all_ng.extend(_char_ngrams(t, n))
    return len(set(all_ng)) / len(all_ng) if all_ng else float("nan")


def self_repetition(texts: list[str], n: int = 4) -> float:
    """跨样本重复率 = 出现在 ≥2 个样本中的 n-gram 占比。越高 = 越同质。"""
    texts = [t for t in texts if t]
    if len(texts) < 2:
        return float("nan")
    doc_count: Counter[str] = Counter()
    for t in texts:
        for ng in set(_char_ngrams(t, n)):
            doc_count[ng] += 1
    if not doc_count:
        return float("nan")
    shared = sum(1 for c in doc_count.values() if c >= 2)
    return shared / len(doc_count)
