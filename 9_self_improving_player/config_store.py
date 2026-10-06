"""Tiny JSON store for the AWS resource IDs this chapter creates (memory, evaluator, bundle).

The file lives next to the scripts as `.player_config.json` and is gitignored: every participant
creates their own resources, so the IDs must never be committed.
"""

import json
import os
import sys

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".player_config.json")
# The workshop runs in us-west-2. Export AWS_REGION to use another region for every script.
REGION = os.environ.get("AWS_REGION", "us-west-2")


def load() -> dict:
    """Return the saved configuration, or an empty dict before anything was created."""
    if os.path.exists(PATH):
        with open(PATH) as f:
            return json.load(f)
    return {}


def save(**updates) -> dict:
    """Merge `updates` into the saved configuration and return the result."""
    config = load()
    config.update(updates)
    with open(PATH, "w") as f:
        json.dump(config, f, indent=2)
    return config


def require(key: str, hint: str):
    """Return config[key] or exit with a message telling the participant which step they skipped."""
    config = load()
    if key not in config:
        sys.exit(f"'{key}' is not in {PATH}. {hint}")
    return config[key]
