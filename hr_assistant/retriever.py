"""BM25 retrieval over policy sections, with an optional boost for documents of the routed topic.

Why BM25 and not embeddings: the corpus is small (~45 sections) with precise vocabulary
("sick days", "carry over"), BM25 is transparent (scores are explainable in the demo), needs no
model download, and its top score doubles as a cheap "is there any relevant policy?" signal.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from .policies import Chunk
from .topics import TOPICS

STOPWORDS = set("""a an and are as at be been by can could do does did for from get got have has how i if in
into is it its me my of on or our so that the their them then there these they this to us was we were what
when where which who will with would you your am any about do much many should need want""".split())

# Light synonym expansion: employees say "vacation", policies say "annual leave".
SYNONYMS = {
    "vacation": ["annual", "leave"], "holiday": ["annual", "leave"], "holidays": ["annual", "public"],
    "pto": ["annual", "leave"], "ill": ["sick"], "sickness": ["sick"], "doctor": ["medical", "certificate"],
    "note": ["certificate"], "maternity": ["parental"], "paternity": ["parental"], "baby": ["parental", "birth"],
    "kid": ["child"], "pay": ["salary"], "paid": ["salary"], "salary": ["salary"], "wfh": ["home", "remote"],
    "abroad": ["abroad", "country"], "gym": ["wellness"], "therapy": ["counselling"], "psychologist": ["counselling"],
    "died": ["death", "bereavement"], "passed": ["death", "bereavement"], "funeral": ["bereavement"],
    "laptop": ["laptop", "equipment"], "stolen": ["stolen", "lost"], "hotel": ["hotel", "travel"],
    "insurance": ["health", "insurance"], "dentist": ["dental"], "retirement": ["pension"],
}


def _stem(tok: str) -> str:
    for suf in ("ing", "ies", "ed", "es", "s"):
        if len(tok) > 4 and tok.endswith(suf):
            return tok[: -len(suf)] + ("y" if suf == "ies" else "")
    return tok


def tokenize(text: str, expand: bool = False) -> list[str]:
    toks = [t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in STOPWORDS]
    if expand:
        toks = toks + [s for t in toks for s in SYNONYMS.get(t, [])]
    return [_stem(t) for t in toks]


@dataclass
class Hit:
    chunk: Chunk
    score: float


class BM25Retriever:
    def __init__(self, chunks: list[Chunk], k1: float = 1.4, b: float = 0.75) -> None:
        self.chunks = chunks
        self.k1, self.b = k1, b
        # Section title counts twice: it is the most informative text in a chunk.
        self.docs = [tokenize(f"{c.doc_title} {c.section_title} {c.section_title} {c.text}") for c in chunks]
        self.tf = [Counter(d) for d in self.docs]
        self.avgdl = sum(len(d) for d in self.docs) / max(len(self.docs), 1)
        df = Counter(t for d in self.docs for t in set(d))
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def _score(self, q: list[str], i: int) -> float:
        tf, dl = self.tf[i], len(self.docs[i])
        s = 0.0
        for t in q:
            f = tf.get(t)
            if f:
                s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / self.avgdl))
        return s

    def search(self, query: str, k: int = 4, topic: str | None = None, topic_boost: float = 1.3) -> list[Hit]:
        q = tokenize(query, expand=True)
        boost_docs = set(TOPICS.get(topic or "", {}).get("docs", []))
        hits = []
        for i, c in enumerate(self.chunks):
            s = self._score(q, i)
            if s > 0 and c.doc_id in boost_docs:
                s *= topic_boost
            if s > 0:
                hits.append(Hit(c, round(s, 3)))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:k]
