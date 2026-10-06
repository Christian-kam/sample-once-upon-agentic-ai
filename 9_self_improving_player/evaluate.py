"""Score one game with AgentCore Evaluations, on demand, straight from the recorded spans."""

import boto3

import config_store

# Session-level built-in judge: did the player reach the goal the Game Master set? One result per game.
GOAL_EVALUATOR = "Builtin.GoalSuccessRate"


def score_session(spans: list[dict], evaluator_ids: list[str], trace_ids: dict[str, list[str]] | None = None) -> list[dict]:
    """Run each evaluator on the session's spans and return one summary per evaluator.

    `trace_ids` optionally restricts a trace-level evaluator to given traces (evaluator id -> trace ids),
    for example to score the player's actions but not its closing remark.

    A trace-level evaluator (like QuestMastery) returns one result per player turn; we average them
    into the game's score and keep the per-turn details. A session-level evaluator returns one result.
    Each summary has `evaluator`, `value` (0..1), `label`, `explanation`, `n` (results averaged) and
    `per_turn`, or `error`. Evaluate is synchronous: an LLM judge reads the transcript, so expect
    10-40 seconds per evaluator.
    """
    client = boto3.client("bedrock-agentcore", region_name=config_store.REGION)
    summaries = []
    for evaluator_id in evaluator_ids:
        target = {"evaluationTarget": {"traceIds": trace_ids[evaluator_id]}} if trace_ids and trace_ids.get(evaluator_id) else {}
        try:
            # TODO: Step 1 - Call the Evaluate API with the evaluator id, the session spans and the optional target
            response = None
            if response is None:
                raise RuntimeError("Complete TODO Step 1 in evaluate.py first.")
            items = response["evaluationResults"]
            scored = [i for i in items if i.get("value") is not None]
            errors = "; ".join(i["errorMessage"] for i in items if i.get("errorMessage")) or None
            if not scored:
                summaries.append({"evaluator": evaluator_id, "value": None, "label": None, "explanation": None, "n": 0, "per_turn": [], "error": errors or "no result"})
                continue
            mean = sum(i["value"] for i in scored) / len(scored)
            typical = min(scored, key=lambda i: abs(i["value"] - mean))  # the result closest to the average
            summaries.append(
                {
                    "evaluator": scored[0].get("evaluatorName", evaluator_id),
                    "value": round(mean, 3),
                    "label": typical.get("label"),
                    "explanation": typical.get("explanation"),
                    "n": len(scored),
                    "per_turn": [{"value": i["value"], "label": i.get("label"), "explanation": i.get("explanation")} for i in scored],
                    "error": errors,
                }
            )
        except Exception as error:  # keep playing even if one judge fails
            summaries.append({"evaluator": evaluator_id, "value": None, "label": None, "explanation": None, "n": 0, "per_turn": [], "error": str(error)})
    return summaries
