from __future__ import annotations

import copy

from alphalab.collaboration import (
    EvidenceArtifact,
    encode_json,
    sha256,
)

def prepare_corrected_candidate(store, fixture):
    task_id = fixture["task_id"]
    version = fixture["versions"][0]

    evidence_store = store.evidence

    previous_context = evidence_store.candidate_context(
        task_id,
        version["candidate"],
    )

    old_manifest = previous_context["manifest"]

    old_publication = copy.deepcopy(
    previous_context["evidence"]["publication"]["content"]
)

    artifacts = []

    # The corrected report must be consistent with the
    # publication payload stored in the frozen manifest.
    corrected_report = (
        "# Corrected Research Report\n\n"
        "The geometrically compounded total return is -1%.\n"
        "The previous 0% claim was incorrect.\n"
        "This corrected draft requires independent re-review.\n"
    ).encode("utf-8")

    for descriptor in old_manifest["artifacts"]:
        artifact_id = descriptor["artifact_id"]
        media_type = descriptor["media_type"]
        kind = descriptor["kind"]
        source = descriptor["source"]
        locators = tuple(descriptor["locators"])

        content = previous_context["evidence"][artifact_id]["content"]

        if artifact_id == "report":
            raw = corrected_report

        elif artifact_id == "publication":
            publication = copy.deepcopy(old_publication)

            # The publication artifact must reference the
            # exact corrected report bytes.
            publication["report"] = {
                "artifact_id": "report",
                "locator": "",
                "sha256": sha256(corrected_report),
            }

            raw = encode_json(publication)

        elif media_type == "application/json":
            raw = encode_json(content)

        else:
            raw = content.encode("utf-8")

        artifacts.append(
            EvidenceArtifact(
                artifact_id,
                raw,
                media_type,
                kind,
                source,
                locators,
            )
        )

    new_manifest = evidence_store.freeze(
        task_id,
        expected_revision=1,
        delivery_key="correction-round-1",
        artifacts=artifacts,
        coverage=old_manifest["coverage"],
        omissions=old_manifest["omissions"],
    )

    # freeze() returns a manifest reference, not the full manifest.
    new_manifest_context = evidence_store.reader(
        task_id,
        [new_manifest],
    ).context(new_manifest["manifest_id"])

    full_new_manifest = new_manifest_context["manifest"]

    package = copy.deepcopy(
        evidence_store.candidate(
            task_id,
            version["candidate"],
        )
    )

    package["revision"] = 2
    package["manifest"] = new_manifest
    package["assumptions"].append(
        "Formula finding addressed in corrected report."
    )

    # Update citations to the changed report artifact.
    new_report = next(
        artifact
        for artifact in full_new_manifest["artifacts"]
        if artifact["artifact_id"] == "report"
    )

    old_report = next(
        artifact
        for artifact in old_manifest["artifacts"]
        if artifact["artifact_id"] == "report"
    )

    def update_report_citations(value):
        if isinstance(value, dict):
            if (
                value.get("artifact_id") == "report"
                and value.get("sha256") == old_report["sha256"]
            ):
                value["sha256"] = new_report["sha256"]

            for child in value.values():
                update_report_citations(child)

        elif isinstance(value, list):
            for child in value:
                update_report_citations(child)

    update_report_citations(package)

    new_publication = next(
        artifact
        for artifact in full_new_manifest["artifacts"]
        if artifact["artifact_id"] == "publication"
    )

    package["publication"] = {
        "artifact_id": "publication",
        "locator": "",
        "sha256": new_publication["sha256"],
    }

    package["claims"][0]["text"] = (
        "The geometrically compounded total return is -1%."
    )

    new_candidate = evidence_store.save_candidate(
        package,
        expected_revision=1,
    )

    return new_candidate, new_manifest

def make_correction(fixture, new_candidate, new_manifest, store):
    version = fixture["versions"][0]
    review = version["review"]
    finding = review["findings"][0]

    # Read the trusted context of the corrected candidate.
    new_context = store.evidence.candidate_context(
        fixture["task_id"],
        new_candidate,
    )

    report_descriptor = next(
        artifact
        for artifact in new_context["manifest"]["artifacts"]
        if artifact["artifact_id"] == "report"
    )

    returns_descriptor = next(
        artifact
        for artifact in new_context["manifest"]["artifacts"]
        if artifact["artifact_id"] == "returns"
    )

    return {
        "schema_version": 1,
        "task_id": fixture["task_id"],
        "response_id": "correction-response-1",
        "review_submission_id": review["submission_id"],
        "reviewed_candidate": version["candidate"],
        "new_candidate": new_candidate,
        "dispositions": [
            {
                "finding_id": finding["finding_id"],
                "status": "corrected",
                "reason": (
                    "The formula error was corrected using "
                    "geometric compounding."
                ),
                "evidence": [
                    {
                        "artifact_id": "report",
                        "sha256": report_descriptor["sha256"],
                        "locator": "",
                    },
                    {
                        "artifact_id": "returns",
                        "sha256": returns_descriptor["sha256"],
                        "locator": "",
                    },
                ],
                "changed_artifact_ids": ["report"],
            }
        ],
        "remaining_disagreements": [],
    }