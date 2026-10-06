"""The player agent: a Strands agent that plays the quest against your Game Master.

Two things make this player able to improve:
- its system prompt is data, not code: it comes from an AgentCore configuration bundle version
  once the outer loop is set up (see optimize.py), and from PLAYER_PROMPT_V1 before that;
- its conversations are stored in AgentCore Memory with the episodic strategy, so reflections on past
  games are retrieved and injected before each turn (the inner loop).
"""

import boto3
from strands import Agent

import config_store

# The same actor across every episode: that is what lets reflections span games.
ACTOR_ID = "thorin"
# Episodes are stored per session under the actor; reflections are stored at the actor level.
EPISODES_NAMESPACE = "/strategies/{memoryStrategyId}/actors/{actorId}/sessions/{sessionId}/"
REFLECTIONS_NAMESPACE = "/strategies/{memoryStrategyId}/actors/{actorId}/"

# Version 1 of the player's instructions. Deliberately short: the loop will improve it.
PLAYER_PROMPT_V1 = """You are Thorin Ironbeard, a dwarf fighter, playing Dungeons & Dragons.
The Game Master describes the scene. Say what you do."""


def actor_namespace(strategy_id: str) -> str:
    """The resolved namespace prefix holding this actor's episodes and reflections."""
    return REFLECTIONS_NAMESPACE.format(memoryStrategyId=strategy_id, actorId=ACTOR_ID)


def build_player(system_prompt: str, session_id: str, memory: dict | None = None) -> tuple[Agent, object | None]:
    """Create the player for one episode. Returns the agent and its memory session manager (or None).

    Args:
        system_prompt: the instructions to play with (v1 inline text or a bundle version).
        session_id: one id per episode; it is stamped on every span as `session.id` so the
            evaluation service can group the spans of this game.
        memory: `{"memory_id": ..., "strategy_id": ...}` to enable AgentCore Memory, or None.
    """
    kwargs = dict(
        name="Player",
        system_prompt=system_prompt,
        callback_handler=None,  # the runner prints the transcript itself
        trace_attributes={"session.id": session_id},
    )
    if memory:
        # TODO: Step 1 - Import AgentCoreMemoryConfig, RetrievalConfig and AgentCoreMemorySessionManager
        from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig, RetrievalConfig
        from bedrock_agentcore.memory.integrations.strands.session_manager import AgentCoreMemorySessionManager

        # TODO: Step 2 - Create an AgentCoreMemoryConfig for this memory, session and ACTOR_ID that retrieves
        # the 5 most relevant records under REFLECTIONS_NAMESPACE (relevance_score 0.3)
        memory_config = AgentCoreMemoryConfig(
            memory_id=memory["memory_id"],
            session_id=session_id,
            actor_id=ACTOR_ID,
            retrieval_config={
                REFLECTIONS_NAMESPACE: RetrievalConfig(top_k=5, relevance_score=0.3, strategy_id=memory["strategy_id"])
            },
            context_tag="lessons_from_past_games",
        )
        # TODO: Step 3 - Give the agent an AgentCoreMemorySessionManager built from memory_config
        kwargs["session_manager"] = AgentCoreMemorySessionManager(memory_config, region_name=config_store.REGION)

        if "session_manager" not in kwargs:
            raise SystemExit("Memory requested but not wired up: complete the TODOs in player.py first.")
    return Agent(**kwargs), kwargs.get("session_manager")


def recall_lessons(memory: dict, query: str, top_k: int = 5) -> list[dict]:
    """Return the long-term memory records (episodes and reflections) most relevant to `query`."""
    client = boto3.client("bedrock-agentcore", region_name=config_store.REGION)
    response = client.retrieve_memory_records(
        memoryId=memory["memory_id"],
        namespacePath=actor_namespace(memory["strategy_id"]),  # prefix: reflections + every session's episodes
        searchCriteria={"searchQuery": query, "topK": top_k},
    )
    return response.get("memoryRecordSummaries", [])


def count_lessons(memory: dict) -> int:
    """How many long-term records exist for the actor (used to notice when extraction finished)."""
    client = boto3.client("bedrock-agentcore", region_name=config_store.REGION)
    total = 0
    kwargs = dict(memoryId=memory["memory_id"], namespacePath=actor_namespace(memory["strategy_id"]), maxResults=100)
    while True:
        response = client.list_memory_records(**kwargs)
        total += len(response.get("memoryRecordSummaries", []))
        if not response.get("nextToken"):
            return total
        kwargs["nextToken"] = response["nextToken"]
