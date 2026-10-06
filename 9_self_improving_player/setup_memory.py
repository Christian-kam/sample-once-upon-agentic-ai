"""Create the AgentCore Memory resource the player learns with (run once).

The memory gets a single strategy, EPISODIC: AgentCore turns each finished game into an *episode*
(situation, intent, assessment, justification) and, across games, into *reflections* (what tends to
work, what tends to fail). One strategy is enough; each one you add costs an extraction pass per event.
"""

import sys
import time

import boto3

import config_store
from player import EPISODES_NAMESPACE, REFLECTIONS_NAMESPACE

MEMORY_NAME = "OnceUponPlayerMemory"
STRATEGY_NAME = "quest_episodes"


def main() -> None:
    client = boto3.client("bedrock-agentcore-control", region_name=config_store.REGION)

    existing = [m for m in client.list_memories(maxResults=100).get("memories", []) if m.get("id", "").startswith(MEMORY_NAME)]
    if existing:
        memory_id = existing[0]["id"]
        print(f"Memory already exists: {memory_id}")
    else:
        print(f"Creating memory '{MEMORY_NAME}' in {config_store.REGION}...")
        # TODO: Step 1 - Add one episodicMemoryStrategy named STRATEGY_NAME that stores episodes under
        # EPISODES_NAMESPACE and reflections under REFLECTIONS_NAMESPACE
        strategies = [
            {
                "episodicMemoryStrategy": {
                    "name": STRATEGY_NAME,
                    "namespaces": [EPISODES_NAMESPACE],
                    "reflectionConfiguration": {"namespaces": [REFLECTIONS_NAMESPACE]},
                }
            }
        ]
        if not strategies:
            sys.exit("No memory strategy configured: complete TODO Step 1 in setup_memory.py first.")
        response = client.create_memory(
            name=MEMORY_NAME,
            description="Episodes and reflections of the self-improving D&D player",
            eventExpiryDuration=30,  # days of raw conversation to keep
            memoryStrategies=strategies,
        )
        memory_id = response["memory"]["id"]

    # Wait until the memory (and its strategy) is ACTIVE: creation takes a couple of minutes.
    for _ in range(60):
        memory = client.get_memory(memoryId=memory_id)["memory"]
        status = memory["status"]
        strategies = memory.get("strategies", [])
        strategy_statuses = {s["name"]: s.get("status") for s in strategies}
        print(f"  status={status} strategies={strategy_statuses}")
        if status == "ACTIVE" and all(s == "ACTIVE" for s in strategy_statuses.values()):
            break
        if status == "FAILED":
            sys.exit(f"Memory creation failed: {memory.get('failureReason')}")
        time.sleep(10)
    else:
        sys.exit("Timed out waiting for the memory to become ACTIVE. Re-run this script in a minute.")

    strategy = next(s for s in strategies if s["name"] == STRATEGY_NAME)
    config_store.save(memory_id=memory_id, strategy_id=strategy["strategyId"], memory_arn=memory["arn"])
    print(f"\nMemory ready.\n  memory_id   = {memory_id}\n  strategy_id = {strategy['strategyId']}")
    print(f"Saved to {config_store.PATH}. Now play with memory on: python play.py --memory --episodes 3")


if __name__ == "__main__":
    main()
