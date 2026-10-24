"""关键词分块的原文定位、确定性与损坏材料拒绝。"""

import hashlib
from uuid import uuid4

import pytest
from supportops_core.knowledge_content import (
    knowledge_content_digest,
    normalize_knowledge_markdown,
    parse_section_anchors,
)
from supportops_core.knowledge_index_services import KnowledgeIndexError, chunk_knowledge
from supportops_core.models import KnowledgeSnapshot, KnowledgeVersion


def materials(content: str) -> tuple[KnowledgeVersion, KnowledgeSnapshot]:
    content = normalize_knowledge_markdown(content)
    digest = knowledge_content_digest(content)
    return (
        KnowledgeVersion(content_markdown=content, content_digest=digest, title="中文分块"),
        KnowledgeSnapshot(
            id=uuid4(), content_digest=digest, section_anchors=parse_section_anchors(content)
        ),
    )


@pytest.mark.parametrize("length", [1599, 1600, 1601, 5000])
def test_chunk_offsets_digests_and_keys_reproduce_original_across_boundaries(length: int):
    version, snapshot = materials("前言\r\n# 诊断\r\n" + "中" * length + "\n## 复核\n结果")
    chunks = chunk_knowledge(version, snapshot)
    assert chunks == chunk_knowledge(version, snapshot)
    assert "".join(chunk.content for chunk in chunks) == version.content_markdown
    assert [chunk.ordinal for chunk in chunks] == list(range(len(chunks)))
    assert len({chunk.chunk_key for chunk in chunks}) == len(chunks)
    assert {chunk.section_id for chunk in chunks} == {"document", "诊断", "复核"}
    for chunk in chunks:
        assert 0 < len(chunk.content) <= 1600
        assert version.content_markdown[chunk.start_offset : chunk.end_offset] == chunk.content
        assert hashlib.sha256(chunk.content.encode()).hexdigest() == chunk.content_digest
    snapshot.id = uuid4()
    assert {chunk.chunk_key for chunk in chunks}.isdisjoint(
        chunk.chunk_key for chunk in chunk_knowledge(version, snapshot)
    )


@pytest.mark.parametrize(
    ("damage", "code"),
    [
        ("body", "KNOWLEDGE_CONTENT_DIGEST_MISMATCH"),
        ("version_digest", "KNOWLEDGE_CONTENT_DIGEST_MISMATCH"),
        ("snapshot_digest", "KNOWLEDGE_CONTENT_DIGEST_MISMATCH"),
        ("missing_anchor", "KNOWLEDGE_SECTION_ANCHOR_MISMATCH"),
        ("extra_anchor", "KNOWLEDGE_SECTION_ANCHOR_MISMATCH"),
        ("long_anchor", "KNOWLEDGE_SECTION_ANCHOR_TOO_LONG"),
    ],
)
def test_corrupt_material_is_rejected_before_producing_chunks(damage: str, code: str):
    version, snapshot = materials("# 标题\n正文")
    if damage == "body":
        version.content_markdown += "被篡改的正文"
    elif damage == "version_digest":
        version.content_digest = "0" * 64
    elif damage == "snapshot_digest":
        snapshot.content_digest = "0" * 64
    elif damage == "missing_anchor":
        snapshot.section_anchors = []
    elif damage == "extra_anchor":
        snapshot.section_anchors = snapshot.section_anchors * 2
    else:
        snapshot.section_anchors = [{"id": "a" * 501, "title": "标题", "level": "1"}]
    with pytest.raises(KnowledgeIndexError) as error:
        chunk_knowledge(version, snapshot)
    assert error.value.code == code
