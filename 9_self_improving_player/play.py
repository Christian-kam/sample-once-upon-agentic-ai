"""Play the quest: the player agent versus your Game Master, for a few episodes.

    python play.py --episodes 3                      # baseline: prompt v1, no memory
    python play.py --episodes 3 --memory             # inner loop: learn across games
    python play.py --episodes 3 --bundle-version ID  # outer loop: play with a bundle version
    python play.py --episodes 3 --active             # play with the promoted version

Each episode is one session: its spans are saved under traces/, scored by the QuestMastery evaluator
if you created it (setup_evaluator.py), and summarised in results/<tag>.jsonl.
"""

import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone

import boto3

import config_store
import player as player_module
from evaluate import GOAL_EVALUATOR, score_session
from quest import QUEST, GameMasterClient
from spans import SpanRecorder, save_spans

HERE = os.path.dirname(os.path.abspath(__file__))
TRACES_DIR = os.path.join(HERE, "traces")
RESULTS_DIR = os.path.join(HERE, "results")


def resolve_prompt(args, config: dict) -> tuple[str, str]:
    """Pick the system prompt to play with and a label describing where it came from."""
    if args.prompt_file:
        with open(args.prompt_file) as f:
            return f.read(), f"file:{os.path.basename(args.prompt_file)}"
    version = args.bundle_version or (config.get("active_version") if args.active else None)
    if version:
        from optimize import read_bundle_version  # local import: optimize.py is Part 4 of the chapter

        return read_bundle_version(config, version)["system_prompt"], f"bundle:{version[:8]}"
    return player_module.PLAYER_PROMPT_V1, "inline:v1"


def wait_for_new_lessons(memory: dict, before: int, timeout: int) -> int:
    """Episodes are extracted asynchronously once AgentCore detects the game is over."""
    print(f"🧠 Waiting up to {timeout}s for AgentCore Memory to extract this game's episode...", flush=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        after = player_module.count_lessons(memory)
        if after > before:
            print(f"   {after - before} new memory record(s) extracted.")
            return after
        time.sleep(15)
    print("   No new record yet. Extraction runs about 15 minutes after a game ends; the lessons will be there for later games.")
    return before


def print_lessons(memory: dict) -> None:
    records = player_module.recall_lessons(memory, query=QUEST.goal)
    if not records:
        print("🧠 No lessons from past games yet.\n")
        return
    print(f"🧠 Lessons from past games ({len(records)} most relevant records):")
    for record in records:
        text = record.get("content", {}).get("text", "").strip().replace("\n", " ")
        print(f"   • {text[:300]}{'…' if len(text) > 300 else ''}")
    print()


def run_episode(agent, gm: GameMasterClient, max_turns: int) -> tuple[str, list[dict]]:
    """Alternate Game Master narration and player action until the quest ends or turns run out."""
    transcript = []
    turn = gm.ask(QUEST.kickoff(max_turns))
    transcript.append({"speaker": "gm", **turn})
    print(f"🧙 Game Master: {turn['response']}\n")
    outcome = None
    for number in range(1, max_turns + 1):
        outcome = QUEST.outcome_from(turn)
        if outcome:
            break
        action = str(agent(QUEST.player_turn_prompt(turn, number, max_turns))).strip()
        transcript.append({"speaker": "player", "response": action})
        print(f"⚔️  Thorin (turn {number}): {action}\n")
        turn = gm.ask(f"Thorin: {action}")
        transcript.append({"speaker": "gm", **turn})
        print(f"🧙 Game Master: {turn['response']}\n")
    outcome = outcome or QUEST.outcome_from(turn) or gm.verdict(QUEST)
    # Deliver the ending to the player: the trace (and the memory) then record how the quest ended.
    closing = str(agent(QUEST.closing_prompt(turn, outcome))).strip()
    transcript.append({"speaker": "player", "response": closing, "closing": True})
    print(f"⚔️  Thorin (closing): {closing}\n")
    return outcome, transcript


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--episodes", type=int, default=1)
    parser.add_argument("--max-turns", type=int, default=8)
    parser.add_argument("--memory", action="store_true", help="store games in AgentCore Memory and recall past lessons")
    parser.add_argument("--wait-memory", type=int, default=0, help="seconds to wait between games for AgentCore to extract the episode (it takes ~15 min; default: do not wait)")
    parser.add_argument("--bundle-version", help="play with this configuration bundle version")
    parser.add_argument("--active", action="store_true", help="play with the promoted bundle version")
    parser.add_argument("--prompt-file", help="play with a system prompt read from a file")
    parser.add_argument("--tag", help="name for this batch (default: derived from the options)")
    parser.add_argument("--builtin", action="append", default=[], help="also score with another built-in evaluator, e.g. Builtin.Helpfulness")
    parser.add_argument("--no-eval", action="store_true", help="skip scoring (no evaluator calls)")
    args = parser.parse_args()

    config = config_store.load()
    system_prompt, prompt_label = resolve_prompt(args, config)
    memory = {"memory_id": config["memory_id"], "strategy_id": config["strategy_id"]} if args.memory else None
    if args.memory and not memory["memory_id"]:
        sys.exit("Run setup_memory.py first to create the memory.")
    tag = args.tag or (prompt_label.replace(":", "-") + ("-memory" if memory else ""))
    # QuestMastery (if created) scores every player action; Builtin.GoalSuccessRate says whether the quest was reached.
    evaluators = [] if args.no_eval else ([config["evaluator_id"]] if "evaluator_id" in config else []) + [GOAL_EVALUATOR] + args.builtin

    gm = GameMasterClient()
    gm.check()
    recorder = SpanRecorder()
    os.makedirs(TRACES_DIR, exist_ok=True)
    os.makedirs(RESULTS_DIR, exist_ok=True)

    print(f"🎲 Quest: {QUEST.name} | prompt: {prompt_label} | memory: {'on' if memory else 'off'} | tag: {tag}\n")
    batch = []
    for episode in range(1, args.episodes + 1):
        session_id = f"{tag}-ep{episode}-{uuid.uuid4().hex[:8]}"
        print(f"{'=' * 70}\nEpisode {episode}/{args.episodes}  session {session_id}\n{'=' * 70}")
        lessons_before = 0
        if memory:
            print_lessons(memory)
            lessons_before = player_module.count_lessons(memory)

        agent, memory_session = player_module.build_player(system_prompt, session_id, memory)
        started = time.time()
        outcome, transcript = run_episode(agent, gm, args.max_turns)
        if memory_session:
            memory_session.close()  # flush the last turns to AgentCore Memory
        duration = time.time() - started
        turns = sum(1 for t in transcript if t["speaker"] == "player" and not t.get("closing"))
        print(f"🏁 Outcome: {outcome.upper()} in {turns} turn(s), {duration:.0f}s")

        spans = recorder.session_spans(session_id)
        save_spans(os.path.join(TRACES_DIR, f"{session_id}.json"), spans)
        # QuestMastery judges actions: score every player turn except the closing remark.
        action_traces = [s["traceId"] for s in spans if s["attributes"].get("gen_ai.operation.name") == "invoke_agent"][:-1]
        targets = {config["evaluator_id"]: action_traces} if "evaluator_id" in config else None
        scores = score_session(spans, evaluators, targets) if evaluators else []
        for score in scores:
            if score["value"] is None:
                print(f"📊 {score['evaluator']}: error {score['error']}")
                continue
            detail = f" over {score['n']} turn(s): " + " ".join(f"{t['value']:.2f}" for t in score["per_turn"]) if score["n"] > 1 else ""
            print(f"📊 {score['evaluator']}: {score['value']} ({score['label']}){detail}\n   {score['explanation']}")

        record = {
            "session_id": session_id, "tag": tag, "prompt": prompt_label, "memory": bool(memory),
            "outcome": outcome, "turns": turns, "seconds": round(duration), "scores": scores,
            "transcript": transcript, "at": datetime.now(timezone.utc).isoformat(),
        }
        with open(os.path.join(RESULTS_DIR, f"{tag}.jsonl"), "a") as f:
            f.write(json.dumps(record) + "\n")
        batch.append(record)

        if memory and episode < args.episodes and args.wait_memory:
            wait_for_new_lessons(memory, lessons_before, args.wait_memory)
        print()

    print(f"{'=' * 70}\nSummary for tag '{tag}' ({len(batch)} episode(s))")
    completed = sum(1 for r in batch if r["outcome"] == "complete")
    print(f"  quest completed: {completed}/{len(batch)}   average turns: {sum(r['turns'] for r in batch) / len(batch):.1f}")
    for evaluator in {s["evaluator"] for r in batch for s in r["scores"] if s["value"] is not None}:
        values = [s["value"] for r in batch for s in r["scores"] if s["evaluator"] == evaluator and s["value"] is not None]
        print(f"  {evaluator}: mean {sum(values) / len(values):.2f} over {len(values)} game(s)")
    print(f"  traces in {TRACES_DIR}, results in {os.path.join(RESULTS_DIR, tag + '.jsonl')}")
    if memory:
        print("  🧠 AgentCore Memory extracts each game's episode and updates the reflections about 15 minutes after the game ends.")


if __name__ == "__main__":
    main()
