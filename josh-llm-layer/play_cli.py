"""Play in the terminal: type what you do, read the story and the dice log.

    python play_cli.py                                   # engine in-process
    python play_cli.py --http http://127.0.0.1:8000      # against the running API
    python play_cli.py --character cleric_dwarf_life --seed 11

With no API key in .env it still runs (keyword parser + template narration),
so you can try the flow before setting up a key. Commands: state, quit.
"""
import argparse
import sys

from llm import HttpEngine, LLMConfig, LocalEngine, build_dm


def status_line(state):
    me = next(p for p in state["party"] if p["controller"] == "player")
    line = f"[{state['mode']}] {me['name']} {me['hp']}/{me['max_hp']} HP"
    cb = state.get("combat")
    if cb:
        active = cb["initiative_order"][cb["active_index"]]["id"]
        t = me.get("turn", {})
        line += (f" | round {cb['round']}, turn: {active} | action {'Y' if t.get('action') else 'N'}, "
                 f"bonus {'Y' if t.get('bonus_action') else 'N'}, move {t.get('movement_left_ft', 0)} ft")
    foes = [f"{e['id']} {e['hp']}/{e['max_hp']}" for e in state["enemies"] if e["alive"]]
    return line + ("\n  enemies: " + ", ".join(foes) if foes else "")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--http", help="engine API base URL (default: run the engine in-process)")
    ap.add_argument("--character", default="barbarian_orc_berserker",
                    choices=["barbarian_orc_berserker", "cleric_dwarf_life", "ranger_wood_elf_hunter"])
    ap.add_argument("--room", default="demo_cellar")
    ap.add_argument("--seed", type=int)
    args = ap.parse_args(argv)

    config = LLMConfig.from_env()
    engine = HttpEngine(args.http) if args.http else LocalEngine()
    dm = build_dm(engine=engine, config=config)
    print(f"LLM: {config.provider}" + ("" if config.online else " (no API key found: keyword parser + template narration)"))

    state = dm.start(args.character, args.room, args.seed)
    print(f"\nGame {state['session_id']} (seed {state.get('rng_seed')})\n{state['summary']['text']}\n")
    print(status_line(state))

    while True:
        try:
            text = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if text.lower() in ("quit", "exit"):
            return 0
        if text.lower() == "state":
            print(status_line(dm.state()))
            continue
        out = dm.handle(text)
        if out["question"]:
            print(f"? {out['question']}")
            continue
        for n in out["narrations"]:
            for d in n["dice"]:
                print(f"   🎲 {d}")
            if n["text"]:
                print(n["text"])
        for line in out["npc_lines"]:
            print(f"{line['line']}" if line["line"].startswith(line["speaker"]) else f"{line['speaker']}: {line['line']}")
        if out["rules_answer"]:
            print(f"Rules: {out['rules_answer']['answer']}")
        print(status_line(out["state"]))
        if out["state"]["mode"] in ("victory", "game_over"):
            print("\n*** " + ("Victory!" if out["state"]["mode"] == "victory" else "Game over.") + " ***")
            return 0


if __name__ == "__main__":
    sys.exit(main())
