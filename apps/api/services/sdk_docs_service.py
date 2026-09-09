"""Read the canonical AlphaLab SDK guide for the web workbenches."""

from __future__ import annotations

import re
from dataclasses import dataclass

from alphalab.utils.paths import REPO_ROOT

SDK_GUIDE_PATH = REPO_ROOT / "docs" / "06_ALPHALAB_SDK_GUIDE.md"
_VERSION_PATTERN = re.compile(r"<!--\s*alphalab-sdk-version:(\d+)\s*-->")
_TOPIC_PATTERN = re.compile(
    r"^<!--\s*alphalab-sdk-topic:([a-z][a-z0-9_-]*)\s*-->\s*$",
    re.MULTILINE,
)
_TITLE_PATTERN = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class SdkDocumentTopic:
    id: str
    title: str
    markdown: str

    def summary(self) -> dict[str, str]:
        return {"id": self.id, "title": self.title}


def _parse_guide() -> tuple[int, tuple[SdkDocumentTopic, ...]]:
    source = SDK_GUIDE_PATH.read_text(encoding="utf-8")
    version_match = _VERSION_PATTERN.search(source)
    if version_match is None:
        raise RuntimeError(f"SDK guide has no version marker: {SDK_GUIDE_PATH}")

    matches = list(_TOPIC_PATTERN.finditer(source))
    if not matches:
        raise RuntimeError(f"SDK guide has no topic markers: {SDK_GUIDE_PATH}")

    topics: list[SdkDocumentTopic] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(source)
        markdown = source[match.end() : end].strip()
        title_match = _TITLE_PATTERN.search(markdown)
        if title_match is None:
            raise RuntimeError(f"SDK guide topic {match.group(1)!r} has no level-two title")
        topics.append(
            SdkDocumentTopic(
                id=match.group(1),
                title=title_match.group(1).strip(),
                markdown=markdown,
            )
        )
    return int(version_match.group(1)), tuple(topics)


def sdk_document_catalog() -> dict:
    version, topics = _parse_guide()
    return {
        "sdk_version": version,
        "document": SDK_GUIDE_PATH.relative_to(REPO_ROOT).as_posix(),
        "topics": [topic.summary() for topic in topics],
    }


def sdk_document(topic_id: str) -> dict | None:
    version, topics = _parse_guide()
    selected = next((topic for topic in topics if topic.id == topic_id), None)
    if selected is None:
        return None
    return {
        "sdk_version": version,
        "document": SDK_GUIDE_PATH.relative_to(REPO_ROOT).as_posix(),
        "topic": selected.id,
        "title": selected.title,
        "markdown": selected.markdown,
        "topics": [topic.summary() for topic in topics],
    }


__all__ = ["SDK_GUIDE_PATH", "sdk_document", "sdk_document_catalog"]
