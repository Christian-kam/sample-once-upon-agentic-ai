"""The quest the player must complete, and the HTTP client that talks to your chapter 5 Game Master.

The Game Master from chapter 5 (or the Strands harness version from chapter 7) listens on port 8009
and answers one structured turn per request: narration, three suggested actions, and dice rolls.
Nothing in this chapter changes the Game Master. From the player's point of view the Game Master
plays the role of the "user": it describes a situation, and the player answers with an action.
"""

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass

GAMEMASTER_URL = os.environ.get("GAMEMASTER_URL", "http://localhost:8009")

COMPLETE_MARKER = "QUEST COMPLETE"
FAILED_MARKER = "QUEST FAILED"


@dataclass(frozen=True)
class Quest:
    name: str
    character: str
    goal: str

    def kickoff(self, max_turns: int) -> str:
        """The message that makes the Game Master open a fresh one-shot with a clear win condition."""
        return (
            "=== NEW ADVENTURE ===\n"
            "Forget any previous adventure: this is a fresh one-shot for a single player.\n"
            f"Player character: {self.character}.\n"
            f"Quest goal, which you must state plainly to the player: {self.goal}\n"
            f"Rules of this one-shot: the player gets at most {max_turns} actions. On each turn, "
            "describe the result of the player's last action and the current situation in 2 to 4 "
            "sentences, roll dice through your tools whenever an action is risky, and offer three "
            "possible actions. Never take decisions for the player. "
            f"When the goal is achieved, include the exact words '{COMPLETE_MARKER}' in your narration. "
            f"If the character dies or nightfall arrives first, include the exact words '{FAILED_MARKER}'.\n"
            "Start now: set the scene and state the goal."
        )

    def player_turn_prompt(self, turn: dict, turn_number: int, max_turns: int) -> str:
        """What the player sees each turn: the Game Master's narration and suggestions."""
        suggestions = "\n".join(f"- {action}" for action in turn.get("actions_suggestions", []))
        dice = ", ".join(
            f"{roll.get('dice_type', '?')}={roll.get('result', '?')} ({roll.get('reason', '')})"
            for roll in turn.get("dice_rolls", [])
        )
        text = f"Game Master: {turn['response']}\n"
        if dice:
            text += f"Dice rolled: {dice}\n"
        if suggestions:
            text += f"Suggested actions:\n{suggestions}\n"
        text += f"\n(Turn {turn_number} of {max_turns}.) What does your character do? Answer with your action."
        return text

    def closing_prompt(self, turn: dict, outcome: str) -> str:
        """Delivered to the player once the game is over, so the trace records how the quest ended."""
        verdict = {"complete": "The quest is complete.", "failed": "The quest has failed."}.get(outcome, "The turn budget is spent and the quest is not complete.")
        return (
            f"Game Master: {turn['response']}\n\n{verdict} The game is over: no more actions. "
            "Reply with one short closing sentence in character."
        )

    @staticmethod
    def outcome_from(turn: dict) -> str | None:
        narration = turn.get("response", "").upper()
        if FAILED_MARKER in narration:
            return "failed"
        if COMPLETE_MARKER in narration:
            return "complete"
        return None


# The one-shot every episode replays. Same quest, same character, so scores are comparable.
QUEST = Quest(
    name="The Toll Bridge of Grimfang",
    character=(
        "Thorin Ironbeard, a male Dwarf Fighter (strength 16, dexterity 12, constitution 15, "
        "intelligence 10, wisdom 13, charisma 8), carrying a battleaxe, a shield and a purse of 25 gold"
    ),
    goal=(
        "Cross the goblin-held Grimfang bridge and reach the village of Harrowmere before nightfall, "
        "alive and still holding his purse of gold"
    ),
)


class GameMasterClient:
    """Minimal client for the Game Master API built in chapter 5 (`POST /inquire`)."""

    def __init__(self, base_url: str = GAMEMASTER_URL, timeout: int = 180):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def check(self) -> None:
        try:
            with urllib.request.urlopen(f"{self.base_url}/health", timeout=10) as response:
                response.read()
        except (urllib.error.URLError, OSError) as error:
            raise SystemExit(
                f"Cannot reach the Game Master at {self.base_url} ({error}). Start the chapter 5 "
                "services first (dice server, Rules Agent, Character Agent, Game Master orchestrator)."
            ) from error

    def ask(self, question: str) -> dict:
        """Send one message and return the structured turn: response, actions_suggestions, dice_rolls."""
        payload = json.dumps({"question": question}).encode()
        request = urllib.request.Request(
            f"{self.base_url}/inquire", data=payload, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            body = json.loads(response.read())
        if "response" not in body:
            raise RuntimeError(f"Unexpected Game Master answer: {body}")
        turn = body["response"]
        if isinstance(turn, str):  # a Game Master without structured output: wrap the plain text
            turn = {"response": turn, "actions_suggestions": [], "dice_rolls": []}
        return turn

    def verdict(self, quest: Quest) -> str:
        """Ask the Game Master, out of character, whether the goal was reached."""
        turn = self.ask(
            "Out-of-character question for the Game Master. Answer with exactly one word in your "
            f"narration, COMPLETE or INCOMPLETE: has the player achieved the quest goal ({quest.goal})?"
        )
        answer = turn.get("response", "").upper()
        return "complete" if "COMPLETE" in answer and "INCOMPLETE" not in answer else "incomplete"
