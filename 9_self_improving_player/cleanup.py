"""Delete the AWS resources this chapter created: the memory, the evaluator and the configuration bundle.

    python cleanup.py            # asks for confirmation
    python cleanup.py --yes

Recorded games (traces/, results/) and the promotions log are local files: delete them by hand if
you want a clean slate. The Game Master services from chapter 5 are stopped with Ctrl+C.
"""

import argparse
import os

import boto3

import config_store


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    args = parser.parse_args()

    config = config_store.load()
    targets = [(k, config[k]) for k in ("memory_id", "evaluator_id", "bundle_id") if config.get(k)]
    if not targets:
        print(f"Nothing to delete: no resource ids in {config_store.PATH}.")
        return
    print(f"About to delete in {config_store.REGION}:")
    for key, value in targets:
        print(f"  {key:<13} {value}")
    if not args.yes and input("Type 'delete' to confirm: ").strip().lower() != "delete":
        print("Aborted.")
        return

    control = boto3.client("bedrock-agentcore-control", region_name=config_store.REGION)
    if config.get("memory_id"):
        control.delete_memory(memoryId=config["memory_id"])
        print("  memory deletion started (takes a minute)")
    if config.get("evaluator_id"):
        control.delete_evaluator(evaluatorId=config["evaluator_id"])
        print("  evaluator deleted")
    if config.get("bundle_id"):
        control.delete_configuration_bundle(bundleId=config["bundle_id"])
        print("  configuration bundle deleted")

    os.remove(config_store.PATH)
    print(f"Removed {config_store.PATH}. Done.")


if __name__ == "__main__":
    main()
