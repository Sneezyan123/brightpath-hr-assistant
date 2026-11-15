"""Evaluate every component of the HR assistant.

Offline (no LLM):  python -m eval.run_eval --offline
Full:              python -m eval.run_eval [--limit N] [--ids a,b] [--types qa,collection,leave] [--no-judge] [--calibrate-judge]
"""
from __future__ import annotations

import argparse
import json
import tempfile
import time
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

from hr_assistant import db
from hr_assistant.agent import HRAssistant, Session
from hr_assistant.config import ROOT, get_settings
from hr_assistant.llm import LLMClient, LLMError
from hr_assistant.policies import load_chunks
from hr_assistant.prompts import JUDGE_SYSTEM, JUDGE_USER
from hr_assistant.retriever import BM25Retriever
from hr_assistant.tools import LeaveTools

from .metrics import corrupt_answer, fact_recall, hit_at_k, prf, reciprocal_rank, recall_at_k, roc_auc, safe_mean

EVAL_TODAY = date(2026, 11, 16)  # a Monday; all gold dates and balances are computed against it
CASES = ROOT / "eval" / "cases.jsonl"
RESULTS = ROOT / "results"


def load_cases(ids: str | None = None, types: str | None = None, limit: int | None = None) -> list[dict]:
    cases = [json.loads(l) for l in CASES.read_text(encoding="utf-8").splitlines() if l.strip()]
    if ids:
        wanted = set(ids.split(","))
        cases = [c for c in cases if c["id"] in wanted]
    if types:
        cases = [c for c in cases if c["type"] in set(types.split(","))]
    return cases[:limit] if limit else cases


# ============================== OFFLINE ==============================
def offline_eval(cases: list[dict]) -> dict:
    s = get_settings()
    chunks = load_chunks(s.policies_dir)
    retr = BM25Retriever(chunks)
    out: dict = {}

    # --- Retrieval: gold question (all turns joined), with and without the topic boost ---
    ret_cases = [c for c in cases if c["gold"].get("relevant")]
    for variant, use_topic in (("bm25", False), ("bm25+topic_boost", True)):
        rows = []
        for c in ret_cases:
            q = " ".join(c["turns"])
            ranked = [h.chunk.id for h in retr.search(q, k=10, topic=c["gold"]["topic"] if use_topic else None)]
            rows.append((c["id"], ranked, c["gold"]["relevant"]))
        out[f"retrieval_{variant}"] = {
            "n": len(rows),
            "hit@1": safe_mean([hit_at_k(r, g, 1) for _, r, g in rows]),
            "recall@3": safe_mean([recall_at_k(r, g, 3) for _, r, g in rows]),
            "recall@4": safe_mean([recall_at_k(r, g, 4) for _, r, g in rows]),
            "mrr": safe_mean([reciprocal_rank(r, g) for _, r, g in rows]),
            "misses@4": [i for i, r, g in rows if not set(r[:4]) & set(g)],
        }

    # --- Referral gate: does the top BM25 score separate answerable from unanswerable questions? ---
    policy = [c for c in cases if c["gold"]["intent"] == "policy_question"]
    scores = {c["id"]: (retr.search(" ".join(c["turns"]), k=1, topic=c["gold"]["topic"]) or [None])[0] for c in policy}
    top = {i: (h.score if h else 0.0) for i, h in scores.items()}
    pos = [top[c["id"]] for c in policy if not c["gold"]["should_refer"]]
    neg = [top[c["id"]] for c in policy if c["gold"]["should_refer"]]
    sweep = []
    for th in [x / 2 for x in range(0, 21)]:
        m = prf([c["gold"]["should_refer"] for c in policy], [top[c["id"]] < th for c in policy])
        sweep.append({"threshold": th, **{k: m[k] for k in ("precision", "recall", "f1")},
                      "answerable_blocked": sum(1 for c in policy if not c["gold"]["should_refer"] and top[c["id"]] < th)})
    out["referral_gate"] = {
        "auc_answerable_vs_refer": roc_auc(pos, neg),
        "answerable_top_score_min": min(pos) if pos else None, "refer_top_score_max": max(neg) if neg else None,
        "current_threshold": s.referral_min_score,
        "at_current": next((r for r in sweep if r["threshold"] == s.referral_min_score), None),
        "sweep": sweep,
        "note": "The gate only blocks clearly unrelated questions; near-misses are left to the answerer's 'supported' flag.",
    }

    # --- Leave rules: given GOLD arguments, does the rules engine reach the gold status? ---
    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        for c in [c for c in cases if c["gold"].get("tool") == "create_leave_request" and c["employee_id"]]:
            p = Path(tmp) / f"{c['id']}.db"
            db.init_db(p, s.seed_path)
            conn = db.connect(p)
            emp = db.get_employee(conn, c["employee_id"])
            res = LeaveTools(conn, emp, EVAL_TODAY).create_leave_request(**c["gold"]["args"])
            rows.append({"id": c["id"], "expected": c["gold"]["final_status"], "got": res["status"], "reason": res["reason"]})
            conn.close()
    out["leave_rules"] = {"n": len(rows), "accuracy": safe_mean([r["expected"] == r["got"] for r in rows]),
                          "errors": [r for r in rows if r["expected"] != r["got"]], "cases": rows}
    return out


# ============================== ONLINE ==============================
def run_case(case: dict, llm: LLMClient, tmp: Path) -> dict:
    s = get_settings()
    assistant = HRAssistant(llm, s, db_path=tmp / f"{case['id']}.db", today=EVAL_TODAY, save_traces=False)
    sess = Session(employee_id=case["employee_id"])
    responses = []
    t0 = time.perf_counter()
    for turn in case["turns"]:
        responses.append(assistant.handle(sess, turn))
    first, last = responses[0], responses[-1]
    g = case["gold"]

    status = None
    if case["type"] == "leave":
        rid = g.get("check_request_id")
        if rid is None and any(tc["name"] == "create_leave_request" for r in responses for tc in r.tool_calls):
            row = assistant.conn.execute("SELECT MAX(id) FROM leave_requests WHERE employee_id=?",
                                         (case["employee_id"],)).fetchone()
            rid = row[0] if row and row[0] and row[0] > 3 else None
        if rid is not None:
            r = assistant.conn.execute("SELECT status FROM leave_requests WHERE id=?", (rid,)).fetchone()
            status = r[0] if r else None
    assistant.conn.close()

    spans = defaultdict(float)
    for r in responses:
        for sp in r.trace["spans"]:
            if sp["component"] != "llm_call":
                spans[sp["component"].split(":")[0]] += sp["latency_ms"]
    return {
        "id": case["id"], "type": case["type"], "employee_id": case["employee_id"], "turns": case["turns"], "gold": g,
        "first": {"intent": first.intent, "topic": first.topic, "slots": first.slots, "asked_slots": first.asked_slots},
        "last": {"intent": last.intent, "topic": last.topic, "slots": last.slots, "asked_slots": last.asked_slots,
                 "question": last.question, "retrieved": last.retrieved, "citations": last.citations,
                 "supported": last.supported, "referred": last.referred, "referral_reason": last.referral_reason,
                 "reply": last.reply},
        "tool_calls": [{"name": tc["name"], "args": tc["args"], "result": tc["result"]} for r in responses for tc in r.tool_calls],
        "db_status": status,
        "errors": [r.reply for r in responses if r.intent == "error"],
        "latency_ms": round((time.perf_counter() - t0) * 1000),
        "component_ms": dict(spans),
    }


def judge_answer(judge: LLMClient, question: str, reference: str, answer: str, context: str) -> dict:
    msgs = [{"role": "system", "content": JUDGE_SYSTEM},
            {"role": "user", "content": JUDGE_USER.format(question=question, reference=reference, context=context, answer=answer)}]
    try:
        r = judge.chat_json(msgs, component="judge", max_tokens=600)
        return {"correctness": int(r.get("correctness", 0)), "faithfulness": int(r.get("faithfulness", 0)),
                "rationale": r.get("rationale", "")}
    except (LLMError, ValueError, TypeError) as exc:
        return {"error": str(exc)}


def online_metrics(recs: list[dict]) -> dict:
    m: dict = {}
    # ---- Router (first turn) ----
    m["router"] = {
        "n": len(recs),
        "intent_accuracy": safe_mean([r["first"]["intent"] == r["gold"]["intent"] for r in recs]),
        "topic_accuracy": safe_mean([r["first"]["topic"] == r["gold"]["topic"] for r in recs if r["gold"].get("topic")]),
        "intent_confusions": dict(Counter(f"{r['gold']['intent']} -> {r['first']['intent']}" for r in recs
                                          if r["first"]["intent"] != r["gold"]["intent"])),
    }
    # ---- Information collection ----
    ic = [r for r in recs if r["gold"]["intent"] in ("policy_question", "leave_request")]
    y_true = [bool(r["gold"].get("missing_first_turn")) for r in ic]
    y_pred = [bool(r["first"]["asked_slots"]) for r in ic]
    slot_rows = [(r, k, v) for r in recs for k, v in (r["gold"].get("slots_final") or {}).items()]
    multi = [r for r in recs if len(r["turns"]) > 1]
    m["info_collection"] = {
        "follow_up_decision": prf(y_true, y_pred),
        "missing_slots_exact_match": safe_mean([set(r["first"]["asked_slots"]) == set(r["gold"].get("missing_first_turn", []))
                                                for r in ic]),
        "slot_value_accuracy": safe_mean([r["last"]["slots"].get(k) == v for r, k, v in slot_rows]),
        "completed_after_follow_up": safe_mean([not r["last"]["asked_slots"] for r in multi]),
    }
    # ---- Retrieval (on the router's standalone question) ----
    ans = [r for r in recs if r["gold"].get("relevant")]
    ranked = {r["id"]: [x["id"] for x in r["last"]["retrieved"]] for r in ans}
    m["retrieval_online"] = {
        "n": len(ans),
        "hit@1": safe_mean([hit_at_k(ranked[r["id"]], r["gold"]["relevant"], 1) for r in ans]),
        "recall@4": safe_mean([recall_at_k(ranked[r["id"]], r["gold"]["relevant"], 4) for r in ans]),
        "mrr": safe_mean([reciprocal_rank(ranked[r["id"]], r["gold"]["relevant"]) for r in ans]),
        "misses": [r["id"] for r in ans if not set(ranked[r["id"]]) & set(r["gold"]["relevant"])],
    }
    # ---- Answer generation + source support (answerable cases the system actually answered) ----
    answered = [r for r in ans if not r["last"]["referred"] and r["last"]["citations"]]
    judged = [r for r in answered if "correctness" in (r.get("judge") or {})]
    cprec = [len(set(r["last"]["citations"]) & set(r["gold"]["relevant"])) / len(r["last"]["citations"]) for r in answered]
    crec = [len(set(r["last"]["citations"]) & set(r["gold"]["relevant"])) / len(r["gold"]["relevant"]) for r in answered]
    m["answer"] = {
        "n_answerable": len(ans), "n_answered": len(answered),
        "key_fact_recall_answered": safe_mean([fact_recall(r["last"]["reply"], r["gold"]["must_include"]) for r in answered]),
        "end_to_end_correct_rate": safe_mean([(not r["last"]["referred"]) and fact_recall(r["last"]["reply"], r["gold"]["must_include"]) == 1.0
                                              for r in ans]),
        "judge_correctness_mean": safe_mean([r["judge"]["correctness"] for r in judged]),
        "judge_correctness_ge4": safe_mean([r["judge"]["correctness"] >= 4 for r in judged]),
        "low_scored": [{"id": r["id"], "score": r["judge"]["correctness"], "why": r["judge"]["rationale"]}
                       for r in judged if r["judge"]["correctness"] < 4],
    }
    m["source_support"] = {
        "citation_precision": safe_mean(cprec), "citation_recall": safe_mean(crec),
        "judge_faithfulness_mean": safe_mean([r["judge"]["faithfulness"] for r in judged]),
        "judge_faithfulness_eq5": safe_mean([r["judge"]["faithfulness"] == 5 for r in judged]),
    }
    # ---- Referral to HR ----
    ref = [r for r in recs if r["type"] in ("qa", "collection")]
    m["referral"] = {**prf([r["gold"]["should_refer"] for r in ref], [r["last"]["referred"] for r in ref]),
                     "by_reason": dict(Counter(r["last"]["referral_reason"] for r in ref if r["last"]["referred"])),
                     "false_referrals": [r["id"] for r in ref if r["last"]["referred"] and not r["gold"]["should_refer"]],
                     "missed_referrals": [r["id"] for r in ref if not r["last"]["referred"] and r["gold"]["should_refer"]]}
    # ---- Leave agent ----
    lv = [r for r in recs if r["type"] == "leave"]
    def tool_ok(r: dict) -> bool:
        want = r["gold"]["tool"]
        names = [t["name"] for t in r["tool_calls"]]
        return (not names) if want is None else want in names
    def args_ok(r: dict) -> bool:
        want, gargs = r["gold"]["tool"], r["gold"]["args"]
        calls = [t for t in r["tool_calls"] if t["name"] == want]
        return any(all(str(c["args"].get(k)) == str(v) for k, v in gargs.items()) for c in calls) if want else not r["tool_calls"]
    status_rows = [r for r in lv if r["gold"].get("final_status")]
    m["leave_agent"] = {
        "n": len(lv),
        "tool_selection_accuracy": safe_mean([tool_ok(r) for r in lv]),
        "argument_accuracy": safe_mean([args_ok(r) for r in lv]),
        "final_status_accuracy": safe_mean([r["db_status"] == r["gold"]["final_status"] for r in status_rows]),
        "reply_fact_recall": safe_mean([fact_recall(r["last"]["reply"], r["gold"]["must_include"]) for r in lv
                                        if r["gold"].get("must_include")]),
        "failures": [{"id": r["id"], "tools": [(t["name"], t["args"]) for t in r["tool_calls"]], "db_status": r["db_status"],
                      "expected": r["gold"].get("final_status")} for r in lv if not (tool_ok(r) and args_ok(r))
                     or (r["gold"].get("final_status") and r["db_status"] != r["gold"]["final_status"])],
    }
    # ---- Latency ----
    comp = defaultdict(list)
    for r in recs:
        for k, v in r["component_ms"].items():
            comp[k].append(v)
    m["latency_ms"] = {"per_case_mean": safe_mean([r["latency_ms"] for r in recs]),
                       **{f"{k}_mean": safe_mean(v) for k, v in sorted(comp.items())}}
    m["errors"] = [r["id"] for r in recs if r["errors"]]
    return m


def calibrate_judge(judge: LLMClient, cases: list[dict], chunks: dict) -> dict:
    """Check the judge separates a correct answer (the reference) from a corrupted one (numbers shifted)."""
    rows = []
    for c in cases:
        g = c["gold"]
        if not g.get("reference") or not g.get("relevant"):
            continue
        bad = corrupt_answer(g["reference"])
        if bad == g["reference"]:
            continue
        ctx = "\n\n".join(f"[{i}] {chunks[i].text}" for i in g["relevant"])
        good_j = judge_answer(judge, " ".join(c["turns"]), g["reference"], g["reference"], ctx)
        bad_j = judge_answer(judge, " ".join(c["turns"]), g["reference"], bad, ctx)
        if "correctness" in good_j and "correctness" in bad_j:
            rows.append({"id": c["id"], "good": good_j["correctness"], "bad": bad_j["correctness"],
                         "good_faith": good_j["faithfulness"], "bad_faith": bad_j["faithfulness"]})
    return {"n": len(rows), "mean_correct_answer": safe_mean([r["good"] for r in rows]),
            "mean_corrupted_answer": safe_mean([r["bad"] for r in rows]),
            "pairwise_accuracy": safe_mean([r["good"] > r["bad"] for r in rows]),
            "faithfulness_pairwise_accuracy": safe_mean([r["good_faith"] > r["bad_faith"] for r in rows]), "rows": rows}


# ============================== REPORT ==============================
def _fmt(v) -> str:
    if v is None:
        return "–"
    if isinstance(v, float):
        return f"{v:.3f}"
    return str(v)


def _table(title: str, d: dict, keys: list[str]) -> str:
    rows = "\n".join(f"| {k} | {_fmt(d.get(k))} |" for k in keys if k in d)
    return f"### {title}\n\n| Metric | Value |\n|---|---|\n{rows}\n"


def write_report(offline: dict, online: dict | None, meta: dict) -> str:
    parts = [f"# Evaluation report\n\nGenerated {time.strftime('%Y-%m-%d %H:%M')} · eval date {EVAL_TODAY} · "
             f"{json.dumps(meta)}\n", "## Offline components (no LLM)\n"]
    for v in ("bm25", "bm25+topic_boost"):
        parts.append(_table(f"Retrieval: {v} (gold question)", offline[f"retrieval_{v}"], ["n", "hit@1", "recall@3", "recall@4", "mrr", "misses@4"]))
    rg = offline["referral_gate"]
    parts.append(_table("Referral gate (top BM25 score)", rg, ["auc_answerable_vs_refer", "answerable_top_score_min",
                                                                "refer_top_score_max", "current_threshold", "at_current"]))
    parts.append(_table("Leave rules engine (gold arguments)", offline["leave_rules"], ["n", "accuracy", "errors"]))
    if online:
        parts.append("## Online components (LLM)\n")
        parts.append(_table("Router / intake (first turn)", online["router"], ["n", "intent_accuracy", "topic_accuracy", "intent_confusions"]))
        ic = online["info_collection"]
        fu = ic["follow_up_decision"]
        parts.append(_table("Information collection", {**ic, "follow_up_precision": fu["precision"], "follow_up_recall": fu["recall"],
                                                        "follow_up_f1": fu["f1"]},
                            ["follow_up_precision", "follow_up_recall", "follow_up_f1", "missing_slots_exact_match",
                             "slot_value_accuracy", "completed_after_follow_up"]))
        parts.append(_table("Retrieval (router query, end-to-end)", online["retrieval_online"], ["n", "hit@1", "recall@4", "mrr", "misses"]))
        parts.append(_table("Answer correctness", online["answer"], ["n_answerable", "n_answered", "key_fact_recall_answered",
                                                                       "end_to_end_correct_rate", "judge_correctness_mean",
                                                                       "judge_correctness_ge4", "low_scored"]))
        parts.append(_table("Source support", online["source_support"], ["citation_precision", "citation_recall",
                                                                           "judge_faithfulness_mean", "judge_faithfulness_eq5"]))
        parts.append(_table("Referral to HR", online["referral"], ["precision", "recall", "f1", "accuracy", "by_reason",
                                                                    "false_referrals", "missed_referrals"]))
        parts.append(_table("Leave agent (tool use + DB outcome)", online["leave_agent"], ["n", "tool_selection_accuracy",
                                                                                            "argument_accuracy", "final_status_accuracy",
                                                                                            "reply_fact_recall", "failures"]))
        if "judge_calibration" in online:
            parts.append(_table("Judge calibration (correct vs corrupted answers)", online["judge_calibration"],
                                ["n", "mean_correct_answer", "mean_corrupted_answer", "pairwise_accuracy", "faithfulness_pairwise_accuracy"]))
        parts.append(_table("Latency", online["latency_ms"], list(online["latency_ms"].keys())))
        if online["errors"]:
            parts.append(f"**Cases with LLM errors:** {online['errors']}\n")
    return "\n".join(parts)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="only components that need no LLM")
    ap.add_argument("--ids")
    ap.add_argument("--types")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--no-judge", action="store_true")
    ap.add_argument("--calibrate-judge", action="store_true")
    args = ap.parse_args()

    RESULTS.mkdir(exist_ok=True)
    all_cases = load_cases()
    offline = offline_eval(all_cases)
    s = get_settings()
    meta = {"model": s.model, "judge": s.judge_model}
    online = None

    if not args.offline:
        cases = load_cases(args.ids, args.types, args.limit)
        llm = LLMClient(s)
        judge = LLMClient(s, model=s.judge_model, fallbacks=[m for m in s.fallback_models if m != s.judge_model])
        chunks = {c.id: c for c in load_chunks(s.policies_dir)}
        recs = []
        with tempfile.TemporaryDirectory() as tmp:
            for i, c in enumerate(cases, 1):
                print(f"[{i}/{len(cases)}] {c['id']}", flush=True)
                try:
                    rec = run_case(c, llm, Path(tmp))
                except LLMError as exc:
                    print(f"  stopped: {exc}")
                    break
                if (not args.no_judge and rec["gold"].get("reference") and rec["gold"].get("relevant")
                        and not rec["last"]["referred"] and rec["last"]["citations"]):
                    ctx = "\n\n".join(f"[{cid}] {chunks[cid].text}" for cid in rec["last"]["citations"] if cid in chunks)
                    rec["judge"] = judge_answer(judge, rec["last"]["question"] or " ".join(c["turns"]),
                                                rec["gold"]["reference"], rec["last"]["reply"], ctx)
                recs.append(rec)
        with (RESULTS / "per_case.jsonl").open("w", encoding="utf-8") as f:
            for r in recs:
                f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
        online = online_metrics(recs) if recs else None
        if online is not None and args.calibrate_judge:
            online["judge_calibration"] = calibrate_judge(judge, cases, chunks)
        meta.update({"cases": len(recs), "llm_network_calls": llm.network_calls + judge.network_calls,
                     "llm_cache_hits": llm.cache_hits + judge.cache_hits})

    (RESULTS / "metrics.json").write_text(json.dumps({"meta": meta, "offline": offline, "online": online}, indent=2,
                                                     ensure_ascii=False, default=str), encoding="utf-8")
    report = write_report(offline, online, meta)
    (RESULTS / "eval_report.md").write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
