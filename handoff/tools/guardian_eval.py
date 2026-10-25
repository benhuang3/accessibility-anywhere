"""Compare local rules with vision review on invented, locally rendered pages.

Usage: python handoff/tools/guardian_eval.py <repo> <outdir> [--only s] [--rules-only]
The default mode calls Claude using ANTHROPIC_API_KEY from the environment.
Rules-only mode renders the same pages offline and never constructs a Guardian.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from eval_cases import CASES, CASE_TAGS, HARD_GROUPS


def run(cases, out, rules_only=False):
    from ally.contracts import EventBus
    from ally.platforms.base import png_to_jpeg_if_possible
    from ally.risk.guardian import Budget, Guardian
    from ally.risk.wire import RiskWire
    from playwright.sync_api import Error, sync_playwright

    budget = Budget(daily_calls=200, max_per_hour=200, page_cooldown_s=0)
    rows = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            context = browser.new_context(viewport={"width": 1280, "height": 800},
                                          offline=True, service_workers="block")
            context.route("**/*", lambda route: route.abort())
            pg = context.new_page()
            for cid, kind, url, fullscreen, html in cases:
                case_id = cid.split()[0]
                row = {"id": cid, "kind": kind, "tags": list(CASE_TAGS.get(case_id, ())),
                       "rules": None, "agent": None, "rules_ok": None, "agent_ok": None,
                       "verdict": "(not run)" if rules_only else "(not asked)",
                       "confidence": None, "explanation": "", "render_error": None}
                rows.append(row)
                try:
                    pg.set_content(html)
                    pg.wait_for_timeout(200)
                    png = pg.screenshot(type="png")
                    text = pg.inner_text("body")
                    # Include accessible text so attribute injections reach the reviewer.
                    labels = pg.eval_on_selector_all(
                        "[alt], [aria-label]",
                        "els=>els.flatMap(e=>[e.getAttribute('alt'),e.getAttribute('aria-label')]).filter(Boolean)")
                    if labels:
                        text += "\n" + "\n".join(labels)
                    fields = pg.eval_on_selector_all(
                        "input", "els=>els.map(e=>({name:e.name,type:e.type}))")
                except Error as exc:
                    row["render_error"] = str(exc)
                    continue
                (out / (case_id + ".png")).write_bytes(png)
                msg = {"url": url, "text": text, "fullscreen": fullscreen, "fields": fields}

                def published(guardian):
                    bus, got = EventBus(), []
                    bus.subscribe("risk", got.append)
                    RiskWire(bus, snapshot_fn=lambda: [], guardian=guardian).on_page(msg)
                    return got

                row["rules"] = bool(published(None))
                row["rules_ok"] = row["rules"] == (kind == "scam")
                if rules_only:
                    continue
                image = png_to_jpeg_if_possible(png)
                guardian = Guardian(screenshot=lambda image=image: image, budget=budget,
                                    consented=lambda: True)
                seen = []
                real_review = guardian.review

                def spy(ev, review=real_review, results=seen):
                    verdict = None
                    try:
                        verdict = review(ev)
                        return verdict
                    finally:
                        results.append(verdict)

                guardian.review = spy
                agent = published(guardian)
                verdict = seen[0] if seen else None
                row.update(agent=bool(agent), agent_ok=bool(agent) == (kind == "scam"),
                           verdict=getattr(verdict, "verdict", "(model error)" if seen else "(not asked)"),
                           confidence=getattr(verdict, "confidence", None),
                           explanation=next((r.evidence for r in agent
                                             if r.source == "risk.guardian"), ""))
        finally:
            browser.close()
    return rows, budget.snapshot()


def metrics(rows, mode):
    evaluated = [r for r in rows if r[mode] is not None]
    tp = sum(r["kind"] == "scam" and r[mode] for r in evaluated)
    fp = sum(r["kind"] == "safe" and r[mode] for r in evaluated)
    tn = sum(r["kind"] == "safe" and not r[mode] for r in evaluated)
    fn = sum(r["kind"] == "scam" and not r[mode] for r in evaluated)
    return {"count": len(evaluated), "correct": tp + tn,
            "tp": tp, "fp": fp, "tn": tn, "fn": fn,
            "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None,
            "false_alarm_rate": fp / (fp + tn) if fp + tn else None}


def summarize(rows, rules_only):
    def group(items):
        return {"case_counts": {kind: sum(r["kind"] == kind for r in items)
                                for kind in ("scam", "safe")},
                "rendered": sum(r["render_error"] is None for r in items),
                "model_errors": sum(r["verdict"] == "(model error)" for r in items),
                "rules": metrics(items, "rules"),
                "agent": None if rules_only else metrics(items, "agent")}

    summary = group(rows)
    summary["breakdown"] = {name: group([r for r in rows if name in r["tags"]])
                            for name in HARD_GROUPS}
    return summary


def percentage(value):
    return "n/a" if value is None else f"{value:.1%}"


def warning(value):
    return "not run" if value is None else "warn" if value else "-"


def outcome(value):
    return "" if value is None else "ok" if value else "WRONG"


def model_text(row):
    confidence = f" {row['confidence']:.2f}" if row["confidence"] is not None else ""
    return row["verdict"] + confidence


def summary_rows(summary):
    for group, data in [("all cases", summary), *summary["breakdown"].items()]:
        for mode, label in (("rules", "rules alone"), ("agent", "rules + AI")):
            yield group, label, data[mode]


def print_report(rows, summary, spend):
    print(f"{'case':44} {'truth':6} {'rules':12} {'rules+AI':14} model says")
    for row in rows:
        rules = warning(row["rules"]) + " " + outcome(row["rules_ok"])
        agent = warning(row["agent"]) + " " + outcome(row["agent_ok"])
        status = "RENDER FAILED" if row["render_error"] else model_text(row)
        print(f"{row['id']:44} {row['kind']:6} {rules:12} {agent:14} {status}")
    counts = summary["case_counts"]
    print(f"\nCases: {len(rows)} ({counts['scam']} scam, {counts['safe']} safe); "
          f"rendered: {summary['rendered']}/{len(rows)}; model errors: {summary['model_errors']}")
    print("Precision = TP/(TP+FP); recall = TP/(TP+FN); false-alarm rate = FP/(FP+TN).")
    print("n/a means the denominator is zero. Render failures are excluded from metrics.")
    for group, mode, result in summary_rows(summary):
        if result is None:
            print(f"{group} / {mode}: not run")
            continue
        print(f"{group} / {mode}: {result['correct']}/{result['count']} correct; "
              f"precision {percentage(result['precision'])}; recall {percentage(result['recall'])}; "
              f"false-alarm rate {percentage(result['false_alarm_rate'])} "
              f"(TP {result['tp']}, FP {result['fp']}, TN {result['tn']}, FN {result['fn']})")
    print("model calls:", spend["calls_today"], " tokens in/out:",
          spend["input_tokens_today"], "/", spend["output_tokens_today"])
    for row in rows:
        if row["explanation"] or row["render_error"]:
            print(f"- {row['id']}: {row['render_error'] or row['explanation']}")


def markdown_report(rows, summary, spend, rules_only, only):
    def cell(value):
        return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ")

    counts = summary["case_counts"]
    lines = ["# Screen-risk evaluation", "",
             f"Mode: {'rules only' if rules_only else 'rules + AI'}. Prefix: {only or '(all)'}. "
             f"Cases: {len(rows)} ({counts['scam']} scam, {counts['safe']} safe). "
             f"Rendered: {summary['rendered']}/{len(rows)}. Model errors: {summary['model_errors']}.", "",
             "All pages are invented and rendered offline; these results are not a real-world accuracy estimate.", "",
             "| Case | Truth | Rules | Rules + AI | Model verdict | Tags |",
             "| --- | --- | --- | --- | --- | --- |"]
    for row in rows:
        values = [row["id"], row["kind"],
                  warning(row["rules"]) + " " + outcome(row["rules_ok"]),
                  warning(row["agent"]) + " " + outcome(row["agent_ok"]),
                  "RENDER FAILED" if row["render_error"] else model_text(row),
                  ", ".join(row["tags"])]
        lines.append("| " + " | ".join(cell(v).strip() for v in values) + " |")
    lines += ["", "## Summary", "",
              "Precision = TP/(TP+FP); recall = TP/(TP+FN); false-alarm rate = FP/(FP+TN). "
              "n/a means the denominator is zero. Render failures are excluded from metrics. "
              "Hard-case groups use fixed case tags and can overlap.", "",
              "| Group | Mode | Correct | TP | FP | TN | FN | Precision | Recall | False-alarm rate |",
              "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for group, mode, result in summary_rows(summary):
        if result is None:
            values = [group, mode, "not run", "-", "-", "-", "-", "-", "-", "-"]
        else:
            values = [group, mode, f"{result['correct']}/{result['count']}",
                      result["tp"], result["fp"], result["tn"], result["fn"],
                      percentage(result["precision"]), percentage(result["recall"]),
                      percentage(result["false_alarm_rate"])]
        lines.append("| " + " | ".join(cell(v) for v in values) + " |")
    lines += ["", f"Model calls: {spend['calls_today']}. "
              f"Tokens in/out: {spend['input_tokens_today']}/{spend['output_tokens_today']}.", ""]
    details = [r for r in rows if r["explanation"] or r["render_error"]]
    if details:
        lines += ["## Details", ""]
        lines += [f"- {cell(r['id'])}: {cell(r['render_error'] or r['explanation'])}" for r in details]
        lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo", type=Path)
    parser.add_argument("outdir", type=Path)
    parser.add_argument("--only", default="", metavar="PREFIX", help="run case IDs starting with PREFIX")
    parser.add_argument("--rules-only", action="store_true", help="render and score without model calls")
    args = parser.parse_args()
    cases = [c for c in CASES if c[0].split()[0].startswith(args.only)]
    if not cases:
        parser.error(f"no cases match prefix {args.only!r}")
    sys.path.insert(0, str(args.repo.resolve()))
    args.outdir.mkdir(parents=True, exist_ok=True)
    if not args.rules_only:
        from ally.paths import Paths, load_env_file

        load_env_file(Paths().env_file)
    rows, spend = run(cases, args.outdir, args.rules_only)
    summary = summarize(rows, args.rules_only)
    print_report(rows, summary, spend)
    result = {"rows": rows, "spend": spend, "summary": summary,
              "rules_only": args.rules_only, "only": args.only}
    (args.outdir / "results.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    (args.outdir / "results.md").write_text(
        markdown_report(rows, summary, spend, args.rules_only, args.only), encoding="utf-8")
    return int(any(r["render_error"] for r in rows))


if __name__ == "__main__":
    sys.exit(main())
