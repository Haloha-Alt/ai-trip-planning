# @author: 乙
# @reviewer: 甲
# @since: 2026-11-07
"""知识检索（A3 RAG 的检索侧）：给"问行程"的问题找**证据**，不靠模型硬编。

为什么是 BM25 而不是向量库
--------------------------
路线图原本写的是 bge-m3 向量检索，但本项目有一条铁律：**引擎与数据层零第三方依赖**
（pandas 的 DLL 在本机被应用控制策略拦截的教训还在）。向量检索要 numpy/faiss，
BM25 只要标准库——几十个 POI 的语料规模下，BM25 的召回质量与向量检索差距很小，
先把链路跑通，语料涨到千条以上再换 bge-m3（接口不变，只换 `_rank` 的实现）。

语料
----
按城市取 POI 的全部知识字段：name / category / tags / tips / address。
一格 CSV 就是一篇"文档"——它们本来就是给人和模型看的事实文案，
拿来做检索语料不引入任何新数据负担。

分词
----
中文没有空格，粗但够用的做法：**滑窗二元组（bigram）+ ASCII 词**。
"星海广场的音乐喷泉" → 星海/海广/广场/…/音乐/喷泉 + 英文数字原样。
二元组对中文检索的查全率足够（这是搜索引擎时代的经典做法），
同义词（"自习"↔"图书馆"）靠 tags 与 tips 里的事实文案覆盖。

用法
----
    from llm.rag import search
    hits = search("晚上想听音乐会", city="dalian")   # top5，带分数

    # 给 LLM 的提示词注入证据（RAG 的"R"）：
    context = "\\n".join(h["text"] for h in hits)
"""

from __future__ import annotations

import math
import re
from typing import Any

from engine.config import load_scenario

_K1 = 1.5        # BM25 词频饱和度
_B = 0.75        # BM25 长度归一强度
_TOP_DEFAULT = 5

# ------------------------------------------------------------------ 分词

_ASCII = re.compile(r"[a-zA-Z0-9]+")


def tokenize(text: str) -> list[str]:
    """中文滑窗二元组 + ASCII 词。空串返回空表。"""
    s = (text or "").strip()
    if not s:
        return []
    out: list[str] = []
    # ASCII 词原样保留（模型名、门牌号、"WiFi"这类）
    for m in _ASCII.findall(s):
        out.append(m.lower())
        s = s.replace(m, " ")
    s = re.sub(r"\s+", " ", s)
    for ch in re.sub(r"[^\u4e00-\u9fff]", "", s):
        out.append(ch)
    for i in range(len(s) - 1):
        a, b = s[i], s[i + 1]
        if "\u4e00" <= a <= "\u9fff" and "\u4e00" <= b <= "\u9fff":
            out.append(a + b)
    return out


# ------------------------------------------------------------------ 语料与索引

_INDEX_CACHE: dict[tuple, dict[str, Any]] = {}


def _doc_text(p) -> str:                        # noqa: ANN001 - POI 契约对象
    parts = [p.name, p.category, " ".join(p.tags or [])]
    for attr in ("tips", "address"):
        v = str(getattr(p, attr, "") or "").strip()
        if v:
            parts.append(v)
    return "｜".join(x for x in parts if x)


def build_index(city: str | None = None, scenario: str | None = None
                ) -> dict[str, Any]:
    """按 (scenario, city) 建 BM25 索引。POI 缓存由 data.loader 兜底，代价很小。"""
    key = (scenario or "", city or "")
    hit = _INDEX_CACHE.get(key)
    if hit is not None:
        return hit

    from data.loader import load_pois

    pois = load_pois(load_scenario(scenario or None), city or None)
    docs: list[dict[str, Any]] = []
    tf: list[dict[str, int]] = []
    for p in pois:
        text = _doc_text(p)
        toks = tokenize(text)
        docs.append({
            "poi_id": p.poi_id, "name": p.name, "category": p.category,
            "text": text, "lat": p.lat, "lon": p.lon,
        })
        bag: dict[str, int] = {}
        for t in toks:
            bag[t] = bag.get(t, 0) + 1
        tf.append(bag)

    n = len(docs)
    avgdl = (sum(sum(b.values()) for b in tf) / n) if n else 0.0
    df: dict[str, int] = {}
    for bag in tf:
        for term in bag:
            df[term] = df.get(term, 0) + 1
    idx = {"docs": docs, "tf": tf, "df": df, "n": n, "avgdl": avgdl}
    _INDEX_CACHE[key] = idx
    return idx


def clear_cache() -> int:
    """清索引缓存（改了 POI 数据后调；/api/reload 顺带清它）。"""
    n = len(_INDEX_CACHE)
    _INDEX_CACHE.clear()
    return n


# ------------------------------------------------------------------ BM25 打分

def _score(idx: dict[str, Any], q_tokens: list[str]) -> list[tuple[int, float]]:
    n, avgdl, df = idx["n"], idx["avgdl"], idx["df"]
    tf_list = idx["tf"]
    out: list[tuple[int, float]] = []
    if not n or not q_tokens:
        return out
    for i, bag in enumerate(tf_list):
        dl = sum(bag.values())
        s = 0.0
        for term in q_tokens:
            f = bag.get(term)
            if not f or term not in df:
                continue
            idf = math.log(1.0 + (n - df[term] + 0.5) / (df[term] + 0.5))
            s += idf * (f * (_K1 + 1.0)) / (f + _K1 * (1.0 - _B + _B * dl / (avgdl or 1.0)))
        if s > 0:
            out.append((i, s))
    out.sort(key=lambda x: (-x[1], x[0]))
    return out


def search(q: str, city: str | None = None, scenario: str | None = None,
           top: int = _TOP_DEFAULT) -> list[dict[str, Any]]:
    """按问题检索 POI 知识，返回 top 命中（分数降序）。空问题/空语料返回 []。"""
    idx = build_index(city=city, scenario=scenario)
    hits = _score(idx, tokenize(q))
    out: list[dict[str, Any]] = []
    for i, s in hits[:max(0, int(top or _TOP_DEFAULT))]:
        d = dict(idx["docs"][i])
        d["score"] = round(s, 4)
        out.append(d)
    return out
