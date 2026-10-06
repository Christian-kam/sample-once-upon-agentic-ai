"""The outer loop: let AgentCore Optimization propose a better prompt, then test and promote it.

    python optimize.py init                       # store prompt v1 as version 1 of a configuration bundle
    python optimize.py recommend --tag <tag>      # analyse the games of <tag> and write version 2
    python play.py --bundle-version <v2> --tag candidate --episodes 3
    python optimize.py report --tags <tag> candidate
    python optimize.py promote --version <v2> --by "<your name>"
    python optimize.py status

The bundle is the player's configuration as data: immutable, versioned, with a commit message per
version. The recommendation reads the current prompt from the bundle, learns from the recorded games
and the QuestMastery scores, and writes its proposal back as a new version. Nothing is applied until a
human promotes it.
"""

import argparse
import glob
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone

import boto3

import config_store
from player import PLAYER_PROMPT_V1
from spans import load_spans

HERE = os.path.dirname(os.path.abspath(__file__))
TRACES_DIR = os.path.join(HERE, "traces")
RESULTS_DIR = os.path.join(HERE, "results")
PROMOTIONS_LOG = os.path.join(HERE, "promotions.log")

BUNDLE_NAME = "OnceUponPlayerConfig"
# Components are keyed by the agent they configure. Our player runs locally, so we key it by name.
COMPONENT_KEY = "player"


def control_client():
    return boto3.client("bedrock-agentcore-control", region_name=config_store.REGION)


def data_client():
    return boto3.client("bedrock-agentcore", region_name=config_store.REGION)


def read_bundle_version(config: dict, version_id: str) -> dict:
    """Return the player's configuration dict (system_prompt, ...) stored in one bundle version."""
    response = control_client().get_configuration_bundle_version(bundleId=config["bundle_id"], versionId=version_id)
    return response["components"][COMPONENT_KEY]["configuration"]


def cmd_init(args) -> None:
    config = config_store.load()
    if config.get("bundle_id"):
        print(f"Bundle already exists: {config['bundle_id']} (baseline version {config['baseline_version']})")
        return
    print(f"Creating configuration bundle '{BUNDLE_NAME}' with prompt v1...")
    # TODO: Step 1 - Create the bundle: one component COMPONENT_KEY whose configuration holds
    # the system_prompt PLAYER_PROMPT_V1, with the commit message "Prompt v1, written by hand"
    response = None
    if response is None:
        sys.exit("Complete TODO Step 1 in optimize.py first.")
    config_store.save(
        bundle_id=response["bundleId"], bundle_arn=response["bundleArn"],
        baseline_version=response["versionId"], active_version=response["versionId"],
    )
    print(f"  bundle_id = {response['bundleId']}\n  version 1 = {response['versionId']} (now the active version)")


def sessions_for_tag(tag: str) -> list[str]:
    path = os.path.join(RESULTS_DIR, f"{tag}.jsonl")
    if not os.path.exists(path):
        sys.exit(f"No results for tag '{tag}' ({path}). Play some games first: python play.py --tag {tag} --episodes 3")
    with open(path) as f:
        return [json.loads(line)["session_id"] for line in f if line.strip()]


def cmd_recommend(args) -> None:
    config = config_store.load()
    for key in ("bundle_id", "evaluator_arn"):
        config_store.require(key, "Run optimize.py init and setup_evaluator.py first.")
    source_version = args.version or config["active_version"]
    sessions = sessions_for_tag(args.tag)
    spans = []
    for session_id in sessions:
        spans.extend(load_spans(os.path.join(TRACES_DIR, f"{session_id}.json")))
    print(f"Analysing {len(sessions)} game(s) / {len(spans)} spans from tag '{args.tag}' against {config['evaluator_arn'].split('/')[-1]}...")

    name = f"player_rec_{datetime.now(timezone.utc).strftime('%m%d_%H%M%S')}"
    # TODO: Step 2 - Start a SYSTEM_PROMPT_RECOMMENDATION: read the prompt from the bundle version
    # (JSON path "$.player.configuration.system_prompt"), analyse the recorded spans, target the QuestMastery evaluator
    response = None
    if response is None:
        sys.exit("Complete TODO Step 2 in optimize.py first.")
    recommendation_id = response["recommendationId"]
    print(f"Recommendation {recommendation_id} started ({response['status']}). This takes a few minutes.", flush=True)

    while True:
        time.sleep(20)
        recommendation = data_client().get_recommendation(recommendationId=recommendation_id)
        status = recommendation["status"]
        print(f"  {status}", flush=True)
        if status in ("COMPLETED", "FAILED"):
            break
    result = recommendation.get("recommendationResult", {}).get("systemPromptRecommendationResult", {})
    if status == "FAILED" or result.get("errorCode"):
        sys.exit(f"Recommendation failed: {result.get('errorCode')} {result.get('errorMessage')}")

    new_version = result["configurationBundle"]["versionId"]
    config_store.save(candidate_version=new_version, last_recommendation_id=recommendation_id)
    print(f"\n{'=' * 70}\nWhy the optimizer changed the prompt:\n{result.get('explanation')}\n")
    print(f"{'=' * 70}\nRecommended prompt (saved as bundle version {new_version}):\n{result['recommendedSystemPrompt']}\n")
    print(f"Next: play with it and compare.\n  python play.py --bundle-version {new_version} --tag candidate --episodes 3\n  python optimize.py report --tags {args.tag} candidate")


def summarise(tag: str) -> dict:
    path = os.path.join(RESULTS_DIR, f"{tag}.jsonl")
    if not os.path.exists(path):
        return {"tag": tag, "games": 0}
    with open(path) as f:
        records = [json.loads(line) for line in f if line.strip()]
    def mean_of(evaluator):
        values = [s["value"] for r in records for s in r["scores"] if s["evaluator"] == evaluator and s["value"] is not None]
        return sum(values) / len(values) if values else None
    return {
        "tag": tag, "games": len(records),
        "completed": sum(1 for r in records if r["outcome"] == "complete"),
        "avg_turns": sum(r["turns"] for r in records) / len(records) if records else 0,
        "quest_mastery": mean_of("QuestMastery"),
        "goal_success": mean_of("Builtin.GoalSuccessRate"),
        "versions": sorted({r["prompt"] for r in records}),
    }


def cmd_report(args) -> None:
    fmt = lambda v: f"{v:.2f}" if v is not None else "n/a"
    print(f"{'tag':<16}{'games':>6}{'completed':>11}{'avg turns':>11}{'QuestMastery':>14}{'GoalSuccess':>13}  prompt")
    for tag in args.tags:
        s = summarise(tag)
        if not s["games"]:
            print(f"{tag:<16}{0:>6}  (no results)")
            continue
        print(f"{tag:<16}{s['games']:>6}{s['completed']:>11}{s['avg_turns']:>11.1f}{fmt(s['quest_mastery']):>14}{fmt(s['goal_success']):>13}  {', '.join(s['versions'])}")


def cmd_promote(args) -> None:
    config = config_store.load()
    configuration = read_bundle_version(config, args.version)  # fails loudly if the version does not exist
    previous = config.get("active_version")
    config_store.save(active_version=args.version)
    entry = {
        "at": datetime.now(timezone.utc).isoformat(), "by": args.by, "promoted": args.version,
        "previous": previous, "reason": args.reason, "evidence": [summarise(t) for t in args.evidence],
    }
    with open(PROMOTIONS_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")
    print(f"Version {args.version} is now the active player configuration (was {previous}).")
    print(f"Recorded in {PROMOTIONS_LOG}. Play with it: python play.py --active --episodes 3")
    print(f"\nActive prompt:\n{configuration['system_prompt']}")


def cmd_status(args) -> None:
    config = config_store.load()
    config_store.require("bundle_id", "Run optimize.py init first.")
    response = control_client().list_configuration_bundle_versions(bundleId=config["bundle_id"], maxResults=50)
    versions = sorted(response.get("versions", response.get("bundleVersions", [])), key=lambda v: str(v.get("versionCreatedAt", v.get("createdAt", ""))))
    print(f"Bundle {config['bundle_id']} ({len(versions)} version(s)); active = {config.get('active_version')}\n")
    for v in versions:
        marker = "  <== active" if v.get("versionId") == config.get("active_version") else ""
        lineage = v.get("lineageMetadata", {})
        who = lineage.get("createdBy", {}).get("name", "?")
        print(f"  {v.get('versionId')}  {str(v.get('versionCreatedAt', ''))[:19]}  by {who:<20} {lineage.get('commitMessage', '')}{marker}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("init", help="create the bundle with prompt v1")
    p.add_argument("--by", default=os.environ.get("USER", "participant"))
    p.set_defaults(func=cmd_init)
    p = sub.add_parser("recommend", help="generate an improved prompt from recorded games")
    p.add_argument("--tag", required=True, help="results tag whose games are analysed")
    p.add_argument("--version", help="bundle version holding the prompt to improve (default: active)")
    p.set_defaults(func=cmd_recommend)
    p = sub.add_parser("report", help="compare the scores of several tags")
    p.add_argument("--tags", nargs="+", required=True)
    p.set_defaults(func=cmd_report)
    p = sub.add_parser("promote", help="make a bundle version the active configuration")
    p.add_argument("--version", required=True)
    p.add_argument("--by", default=os.environ.get("USER", "participant"))
    p.add_argument("--reason", default="")
    p.add_argument("--evidence", nargs="*", default=[], help="result tags that justify the decision")
    p.set_defaults(func=cmd_promote)
    p = sub.add_parser("status", help="list bundle versions")
    p.set_defaults(func=cmd_status)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
