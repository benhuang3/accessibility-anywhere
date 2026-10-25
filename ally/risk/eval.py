"""Evaluation runner: precision and recall of the R1 text and R3 domain rules on a labelled case set.

Run from the repo root: python -m ally.risk.eval [--json] [--strict]
"""

import argparse
import json
from pathlib import Path
import sys

from ally.risk.domains import check_domain
from ally.risk.text import score_text

CASES = Path(__file__).with_name("eval_cases.json")
_RANK = {"none": 0, "medium": 1, "high": 2}


def _sev(value) -> str:
    return value or "none"


def _ratio(num: int, den: int):
    return round(num / den, 3) if den else None


def _fmt(value) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def load_cases(path: Path = CASES) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _text(case: dict):
    return score_text(case["text"], url=case.get("url", ""), overlay_text=case.get("overlay_text", ""),
                      fullscreen=case.get("fullscreen", False))


def run(cases: dict) -> dict:
    """Run every case; return metrics plus a list of misjudged cases."""
    failures = []

    def fail(kind, case, got, score, want):
        failures.append({"id": case["id"], "kind": kind, "note": case["note"], "severity": got,
                         "score": score, "expected": want})

    scam = [(c, _text(c)) for c in cases["scam_text"]]
    benign = [(c, _text(c)) for c in cases["benign_text"]]
    flagged = sum(1 for _, r in scam if r.severity)
    need_high = [(c, r) for c, r in scam if c["expect_min"] == "high"]
    high_ok = sum(1 for _, r in need_high if r.severity == "high")
    strict_benign = [(c, r) for c, r in benign if c["expect_max"] == "none"]
    false_pos = sum(1 for _, r in strict_benign if r.severity)
    benign_high = sum(1 for _, r in benign if r.severity == "high")
    for c, r in scam:
        if _RANK[_sev(r.severity)] < _RANK[c["expect_min"]]:
            fail("scam_text under-flagged", c, _sev(r.severity), r.score, "at least " + c["expect_min"])
    for c, r in benign:
        if _RANK[_sev(r.severity)] > _RANK[c["expect_max"]]:
            fail("benign_text over-flagged", c, _sev(r.severity), r.score, "at most " + c["expect_max"])

    look = [(c, check_domain(c["url"])) for c in cases["lookalike"]]
    real = [(c, check_domain(c["url"])) for c in cases["real_domain"]]
    tp = sum(1 for _, r in look if r.severity)
    fp = sum(1 for _, r in real if r.severity)
    for c, r in look:
        if not r.severity:
            fail("lookalike missed", c, "none", 0, "flagged")
    for c, r in real:
        if r.severity:
            fail("real_domain flagged", c, r.severity, 0, "clean")

    text_tp = flagged
    text_fp = false_pos
    return {
        "counts": {k: len(v) for k, v in cases.items()},
        "text": {
            "recall_scam": _ratio(flagged, len(scam)),
            "recall_high": _ratio(high_ok, len(need_high)),
            "precision": _ratio(text_tp, text_tp + text_fp),
            "flagged_scams": flagged, "expect_high": len(need_high), "high_found": high_ok,
            "benign_false_positives": text_fp, "benign_strict": len(strict_benign),
            "benign_wrongly_high": benign_high,
            "confusion": {"scam": _tally(r for _, r in scam), "benign": _tally(r for _, r in benign)},
        },
        "domain": {
            "precision": _ratio(tp, tp + fp), "recall": _ratio(tp, len(look)),
            "true_positive": tp, "false_negative": len(look) - tp,
            "false_positive": fp, "true_negative": len(real) - fp,
        },
        "failures": failures,
    }


def _tally(results) -> dict:
    out = {"none": 0, "medium": 0, "high": 0}
    for r in results:
        out[_sev(r.severity)] += 1
    return out


def report(m: dict) -> str:
    t, d, c = m["text"], m["domain"], m["counts"]
    lines = ["Ally risk rules: evaluation", "",
             "Cases: " + ", ".join(f"{k}={v}" for k, v in c.items()), "", "Text rules (R1)",
             f"  Recall on scam_text (medium or high): {_fmt(t['recall_scam'])} ({t['flagged_scams']}/{c['scam_text']})",
             f"  Recall of high where high expected:   {_fmt(t['recall_high'])} ({t['high_found']}/{t['expect_high']})",
             f"  Precision (benign expect none counted as false positives): {_fmt(t['precision'])}"
             f" ({t['benign_false_positives']} false positives of {t['benign_strict']} strict benign)",
             f"  Benign cases wrongly high: {t['benign_wrongly_high']}", "",
             "  Confusion (rows = true class, columns = returned severity)",
             f"  {'':8}{'none':>6}{'medium':>8}{'high':>6}"]
    for row in ("scam", "benign"):
        v = t["confusion"][row]
        lines.append(f"  {row:8}{v['none']:>6}{v['medium']:>8}{v['high']:>6}")
    lines += ["", "Domain rules (R3), lookalike = positive",
              f"  Precision: {_fmt(d['precision'])}   Recall: {_fmt(d['recall'])}",
              f"  TP={d['true_positive']} FN={d['false_negative']} FP={d['false_positive']} TN={d['true_negative']}",
              "", f"Failures ({len(m['failures'])})"]
    for f in m["failures"]:
        lines.append(f"  {f['id']} [{f['kind']}] expected {f['expected']}, got {f['severity']} (score {f['score']}): {f['note']}")
    if not m["failures"]:
        lines.append("  none")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate the Ally risk rules on eval_cases.json.")
    parser.add_argument("--json", action="store_true", help="print machine-readable results")
    parser.add_argument("--strict", action="store_true",
                        help="exit 1 if a benign_text is high or a real_domain is flagged")
    args = parser.parse_args(argv)
    metrics = run(load_cases())
    print(json.dumps(metrics, indent=2) if args.json else report(metrics))
    bad = metrics["text"]["benign_wrongly_high"] or metrics["domain"]["false_positive"]
    return 1 if args.strict and bad else 0


if __name__ == "__main__":
    sys.exit(main())
