import pytest

from supportops_core.knowledge_content import (
    KnowledgeContentError,
    knowledge_content_digest,
    normalize_knowledge_markdown,
    parse_section_anchors,
)


def test_normalize_markdown_and_digest_are_stable() -> None:
    first = normalize_knowledge_markdown("# 标题  \r\n\r\n正文  \n")
    second = normalize_knowledge_markdown("# 标题\n\n正文")
    assert first == second
    assert knowledge_content_digest(first) == knowledge_content_digest(second)


def test_parse_section_anchors_is_unique_and_deterministic() -> None:
    content = "# 429 诊断\n## 根因\n## 根因\n### 参数/预算"
    assert parse_section_anchors(content) == [
        {"id": "429-诊断", "title": "429 诊断", "level": "1"},
        {"id": "根因", "title": "根因", "level": "2"},
        {"id": "根因-2", "title": "根因", "level": "2"},
        {"id": "参数预算", "title": "参数/预算", "level": "3"},
    ]


def test_empty_content_is_rejected() -> None:
    with pytest.raises(KnowledgeContentError):
        normalize_knowledge_markdown(" \r\n ")
