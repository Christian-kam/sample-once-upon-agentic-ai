"""Create the custom evaluator that defines what a *good player action* is (run once).

This rubric is the steering wheel of the whole loop: the optimizer will push the player's prompt
toward whatever this judge scores highly. It is a trace-level LLM-as-a-judge evaluator: the judge
reads the game so far (Game Master turns = user, player turns = assistant) and scores the player's
latest action from 0 to 1. A game's QuestMastery score is the average over its turns.

Why trace level and not session level? Recommendations (Part 4) accept custom evaluators only at
trace level today; whether the quest was completed is measured separately with the built-in,
session-level Builtin.GoalSuccessRate evaluator.
"""

import boto3

import config_store

EVALUATOR_NAME = "QuestMastery"
# The judge model. Any Anthropic model enabled in your account works; Sonnet reads long games well.
JUDGE_MODEL_ID = "us.anthropic.claude-sonnet-4-6"

# `{context}` is replaced by the conversation so far, `{assistant_turn}` by the action being judged.
INSTRUCTIONS = """You are judging one action taken by an AI player in a short Dungeons & Dragons quest.
In the conversation, the user turns are the Game Master's narration and the assistant turns are the
player's actions. The Game Master stated the quest goal in the first turn. Judge ONLY the player's
latest action, in the light of the conversation so far. Never judge the Game Master.

Rate the action on these five criteria:
1. Goal pursuit: the action moves the character toward the stated quest goal; no wandering.
2. Declares intent, not outcomes: the player says what the character attempts and lets the Game
   Master resolve it with dice. Narrating one's own success, inventing items, allies, spells or rules,
   or speaking for non-player characters are serious faults.
3. Plays the character: the action fits a dwarf fighter's abilities and equipment, and uses the
   character's strengths (strength, constitution) rather than its weaknesses (charisma).
4. Uses the situation: the action engages with details and options the Game Master just offered,
   or proposes a sensible alternative, instead of a generic action.
5. One clear action: a single, briefly stated action, not several options or a monologue.

Conversation so far:
{context}

Player's action to judge:
{assistant_turn}"""

RATING_SCALE = [
    {"value": 1.0, "label": "Legendary", "definition": "All five criteria met: a clear, in-character attempt that advances the goal and engages with the situation."},
    {"value": 0.75, "label": "Heroic", "definition": "A good action with one minor lapse, such as a little flourish beyond the attempt or a slightly generic choice."},
    {"value": 0.5, "label": "Competent", "definition": "A reasonable action with a notable lapse: narrating part of the outcome, hedging between options, or ignoring an offered detail."},
    {"value": 0.25, "label": "Clumsy", "definition": "Several lapses: invents facts or allies, narrates success, or barely relates to the goal."},
    {"value": 0.0, "label": "Disastrous", "definition": "Ignores the goal or the situation, or breaks the rules of play throughout the action."},
]


def main() -> None:
    client = boto3.client("bedrock-agentcore-control", region_name=config_store.REGION)

    existing = [e for e in client.list_evaluators(maxResults=100).get("evaluators", []) if e.get("evaluatorName") == EVALUATOR_NAME]
    if existing:
        evaluator = existing[0]
        print(f"Evaluator already exists: {evaluator['evaluatorId']}")
    else:
        print(f"Creating evaluator '{EVALUATOR_NAME}' in {config_store.REGION}...")
        evaluator = client.create_evaluator(
            evaluatorName=EVALUATOR_NAME,
            description="Scores each action of the player agent in the Grimfang bridge quest",
            # TODO: Step 1 - Set the evaluation level to "TRACE" so the judge scores every player action
            level=None,
            # TODO: Step 2 - Configure an llmAsAJudge evaluator with INSTRUCTIONS, the numerical RATING_SCALE and JUDGE_MODEL_ID
            evaluatorConfig=None,
        )

    config_store.save(evaluator_id=evaluator["evaluatorId"], evaluator_arn=evaluator["evaluatorArn"])
    print(f"\nEvaluator ready.\n  evaluator_id  = {evaluator['evaluatorId']}\n  evaluator_arn = {evaluator['evaluatorArn']}")
    print(f"Saved to {config_store.PATH}. Scores now appear after every game you play with play.py.")


if __name__ == "__main__":
    main()
