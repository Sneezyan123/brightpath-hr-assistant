"""Metric helpers (pure functions, unit-tested in tests/test_metrics.py)."""
from __future__ import annotations

import re
from statistics import mean


def safe_mean(xs: list[float]) -> float | None:
    xs = [x for x in xs if x is not None]
    return round(mean(xs), 3) if xs else None


def prf(y_true: list[bool], y_pred: list[bool]) -> dict:
    tp = sum(t and p for t, p in zip(y_true, y_pred))
    fp = sum((not t) and p for t, p in zip(y_true, y_pred))
    fn = sum(t and (not p) for t, p in zip(y_true, y_pred))
    tn = sum((not t) and (not p) for t, p in zip(y_true, y_pred))
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = (2 * precision * recall / (precision + recall)) if precision and recall else (0.0 if precision is not None and recall is not None else None)
    n = len(y_true)
    return {"precision": _r(precision), "recall": _r(recall), "f1": _r(f1), "accuracy": _r((tp + tn) / n if n else None),
            "tp": tp, "fp": fp, "fn": fn, "tn": tn, "n": n}


def _r(x: float | None) -> float | None:
    return None if x is None else round(x, 3)


def hit_at_k(ranked: list[str], relevant: list[str], k: int) -> float:
    return 1.0 if set(ranked[:k]) & set(relevant) else 0.0


def recall_at_k(ranked: list[str], relevant: list[str], k: int) -> float:
    return len(set(ranked[:k]) & set(relevant)) / len(relevant) if relevant else 0.0


def reciprocal_rank(ranked: list[str], relevant: list[str]) -> float:
    for i, r in enumerate(ranked, 1):
        if r in relevant:
            return 1.0 / i
    return 0.0


def roc_auc(pos_scores: list[float], neg_scores: list[float]) -> float | None:
    """Probability that a random positive scores higher than a random negative (ties = 0.5)."""
    if not pos_scores or not neg_scores:
        return None
    wins = sum(1.0 if p > n else 0.5 if p == n else 0.0 for p in pos_scores for n in neg_scores)
    return round(wins / (len(pos_scores) * len(neg_scores)), 3)


def _norm(text: str) -> str:
    t = text.lower().replace("–", "-").replace("-", " ")
    t = re.sub(r"(?<=\d),(?=\d{3})", "", t)          # 1,000 -> 1000
    return re.sub(r"\s+", " ", t)


def fact_recall(answer: str, facts: list[str]) -> float | None:
    """Share of required facts that appear in the answer (case/hyphen/thousands-separator insensitive).

    Numbers must match as whole tokens, so '5' does not match inside '25'.
    """
    if not facts:
        return None
    a = _norm(answer)
    found = 0
    for f in facts:
        nf = _norm(f)
        pattern = r"(?<![\d.])" + re.escape(nf) + r"(?![\d])" if re.fullmatch(r"[\d.%]+", nf) else re.escape(nf)
        found += bool(re.search(pattern, a))
    return found / len(facts)


def corrupt_answer(reference: str) -> str:
    """Make a plausible but wrong answer for judge calibration: shift every number, flip yes/no."""
    def shift(m: re.Match) -> str:
        s = m.group(0)
        if "." in s:
            return f"{float(s) + 3.5:.2f}"
        return str(int(s.replace(",", "")) + 7)
    out = re.sub(r"\d[\d,]*(?:\.\d+)?", shift, reference)
    out = re.sub(r"\bYes\b", "No", out) if "Yes" in reference else re.sub(r"\bNo\b", "Yes", out)
    return out
