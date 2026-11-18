
import argparse
import json
from pathlib import Path

from alphalab.collaboration import ControlStore


def inspect_review(fixture_path):
    fixture_path = Path(fixture_path).resolve()
    folder = fixture_path.parent

    fixture = json.loads(
        fixture_path.read_text(encoding="utf-8")
    )

    task_id = fixture["task_id"]

    store = ControlStore(
        folder / "control",
        folder / "evidence"
    )

    state = store.request(task_id, "view")

    print("\n=== TASK STATE ===")
    print("Task:", task_id)
    print("Status:", state["status"])

    print("\n=== ACCEPTED REVIEW ===")

    for child in state.get("children", []):
        review = child.get("review")

        if not review:
            continue

        print("Child:", child["delegation"]["child_task_id"])
        print("Review status:", review["status"])

        for finding in review.get("findings", []):
            print("\nFinding:", finding["finding_id"])
            print("Category:", finding["category"])
            print("Severity:", finding["severity"])
            print("Certainty:", finding["certainty"])
            print("Explanation:", finding["explanation"])
            print("Expected:", finding["expected"])
            print("Observed:", finding["observed"])

    print("\n=== DENIED OPERATIONS ===")

    events = store.request(
    task_id,
    "events",
    after=0,
    limit=200
)

    for event in events:
        if (
            event.get("role") == "reviewer"
            and event.get("kind") == "denied"
        ):
            print({
                "role": event.get("role"),
                "kind": event.get("kind"),
                "error_code": event.get("error_code"),
            })


if __name__ == "__main__":
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--fixture",
        required=True
    )

    args = parser.parse_args()

    inspect_review(args.fixture)
