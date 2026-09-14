"""知识正文规范化、摘要和章节定位工具。

这些函数不依赖数据库或模型，供知识版本写入和后续检索快照共同复用。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

MAX_KNOWLEDGE_CONTENT_BYTES = 1_048_576
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_NON_WORD_RE = re.compile(r"[^\w\- ]+", re.UNICODE)


class KnowledgeContentError(ValueError):
    """正文不满足知识版本契约。"""


@dataclass(frozen=True, slots=True)
class KnowledgeSectionAnchor:
    """可在引用中稳定定位的 Markdown 标题。"""

    id: str
    title: str
    level: int

    def as_dict(self) -> dict[str, str]:
        return {"id": self.id, "title": self.title, "level": str(self.level)}


def normalize_knowledge_markdown(content: str) -> str:
    """统一换行和行尾空白，并拒绝空正文及超限内容。"""

    if not isinstance(content, str):
        raise KnowledgeContentError("知识正文必须是字符串")
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    normalized = "\n".join(line.rstrip() for line in normalized.split("\n")).strip()
    if not normalized:
        raise KnowledgeContentError("知识正文不能为空")
    if len(normalized.encode("utf-8")) > MAX_KNOWLEDGE_CONTENT_BYTES:
        raise KnowledgeContentError("知识正文超过 1 MiB 限制")
    return normalized


def knowledge_content_digest(content: str) -> str:
    """返回规范化 Markdown 的 SHA-256 十六进制摘要。"""

    normalized = normalize_knowledge_markdown(content)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _anchor_slug(title: str) -> str:
    slug = _NON_WORD_RE.sub("", title.strip().lower().replace("_", " "))
    slug = re.sub(r"\s+", "-", slug).strip("-")
    return slug or "section"


def parse_section_anchors(content: str) -> list[dict[str, str]]:
    """从规范化 Markdown 标题生成唯一、确定性的章节锚点。"""

    normalized = normalize_knowledge_markdown(content)
    counts: dict[str, int] = {}
    anchors: list[dict[str, str]] = []
    for line in normalized.splitlines():
        match = _HEADING_RE.match(line)
        if match is None:
            continue
        level = len(match.group(1))
        title = match.group(2).strip().rstrip("#").rstrip()
        base = _anchor_slug(title)
        counts[base] = counts.get(base, 0) + 1
        anchor_id = base if counts[base] == 1 else f"{base}-{counts[base]}"
        anchors.append(KnowledgeSectionAnchor(anchor_id, title, level).as_dict())
    return anchors
