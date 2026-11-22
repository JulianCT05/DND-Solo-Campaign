"""Scores the parser on the team's sample sentences and writes eval/report.md.

    python eval/eval_parser.py                 # the real model (needs a key in .env)
    python eval/eval_parser.py --keyword       # offline keyword parser (baseline)
    python eval/eval_parser.py --only b02 c04  # a few cases

A case passes when:
  * expect.clarify is true and the parser asked a question, or
  * the parser's actions match expect.actions in order, where each expected
    action only has to match the keys it lists.
Every produced request must also pass the action_request schema.
"""
import argparse
import json
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from llm import KeywordParser, LLMConfig, Parser, make_client, schema_errors  # noqa: E402
from llm.dm import LocalEngine  # noqa: E402

CASES = Path(__file__).resolve().parent / "sample_sentences.json"


def matches(expected, actual):
    for k, v in expected.items():
        a = actual.get(k)
        if isinstance(v, str) and isinstance(a, str):
            if v.lower() != a.lower():
                return False
        elif v != a:
            return False
    return True


def score(case, req):
    exp = case["expect"]
    asked = bool(req.get("clarification_needed"))
    if exp.get("clarify"):
        return asked, "asked a question" if asked else "acted instead of asking"
    if asked:
        return False, f"asked: {req['clarification_needed']}"
    want, got = exp["actions"], req["actions"]
    if len(want) != len(got):
        return False, f"expected {len(want)} action(s), got {len(got)}"
    for i, (w, g) in enumerate(zip(want, got)):
        if not matches(w, g):
            return False, f"action {i}: expected {json.dumps(w)}"
    return True, "ok"


def build_states(cases, engine):
    cache = {}
    for c in cases:
        key = c.get("state_file") or (c["room"], c["character"])
        if key in cache:
            continue
        if c.get("state_file"):
            cache[key] = json.loads((ROOT / "data" / c["state_file"]).read_text(encoding="utf-8"))
        else:
            cache[key] = engine.new_game(c["character"], c["room"], seed=7)
    return cache


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--keyword", action="store_true", help="use the offline keyword parser")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--report", default=str(Path(__file__).resolve().parent / "report.md"))
    args = ap.parse_args(argv)

    cases = json.loads(CASES.read_text(encoding="utf-8"))["cases"]
    if args.only:
        cases = [c for c in cases if c["id"] in args.only]
    engine = LocalEngine(saves_dir=tempfile.mkdtemp())
    states = build_states(cases, engine)

    config = LLMConfig.from_env()
    if args.keyword or not config.online:
        parser, label = KeywordParser(), "keyword parser (offline)"
        if not args.keyword:
            print("No API key found: scoring the offline keyword parser instead.")
    else:
        parser, label = Parser(make_client(config), config.parser_model), f"{config.provider} / {config.parser_model}"

    rows, passed, by_type, times = [], 0, Counter(), []
    fails_by_type = Counter()
    for c in cases:
        state = states[c.get("state_file") or (c["room"], c["character"])]
        actor = next(p["id"] for p in state["party"] if p["controller"] == "player")
        t0 = time.time()
        req = parser.parse(c["text"], state, actor)
        times.append(time.time() - t0)
        ok, why = score(c, req)
        errs = schema_errors(req)
        if errs:
            ok, why = False, f"schema: {errs[0]}"
        kind = "clarify" if c["expect"].get("clarify") else c["expect"]["actions"][-1]["type"]
        by_type[kind] += 1
        if ok:
            passed += 1
        else:
            fails_by_type[kind] += 1
        got = req.get("clarification_needed") or json.dumps(req["actions"])
        rows.append((c["id"], c["text"], ok, why, got))
        print(f"{'PASS' if ok else 'FAIL'} {c['id']:4} {c['text'][:50]:50} {'' if ok else why}")

    pct = 100 * passed / max(len(cases), 1)
    avg_ms = int(1000 * sum(times) / max(len(times), 1))
    print(f"\n{passed}/{len(cases)} correct ({pct:.0f}%), average {avg_ms} ms per sentence, parser: {label}")

    lines = [f"# Parser evaluation", "",
             f"- Parser: {label}", f"- Cases: {len(cases)} (eval/sample_sentences.json)",
             f"- Correct: **{passed}/{len(cases)} ({pct:.0f}%)**", f"- Average time per sentence: {avg_ms} ms",
             f"- Run: {time.strftime('%Y-%m-%d %H:%M')}", "", "## By expected action", "",
             "| Kind | Cases | Correct |", "| --- | --- | --- |"]
    for k, n in sorted(by_type.items()):
        lines.append(f"| {k} | {n} | {n - fails_by_type[k]} |")
    lines += ["", "## Every case", "", "| Id | Text | Result | Why | Parser output |", "| --- | --- | --- | --- | --- |"]
    for cid, text, ok, why, got in rows:
        esc = lambda s: str(s).replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {cid} | {esc(text)} | {'pass' if ok else '**fail**'} | {esc(why)} | `{esc(got)[:160]}` |")
    Path(args.report).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Report written to {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
