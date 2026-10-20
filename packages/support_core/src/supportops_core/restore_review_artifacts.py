"""恢复汇总的受保护原回执文件reader；不补造回执、不回写文件或连接业务目标。"""

import asyncio
import hashlib
import json
import os
import stat
import time
from contextlib import ExitStack
from typing import Literal, Protocol
from uuid import UUID

from pydantic import Field, model_validator

from supportops_core.restore_review import MAX_EVIDENCE_BYTES, MAX_INPUT_BYTES, RestoreReviewAuthority, _context, _packet
from supportops_core.restore_review_contracts import (
    CredentialRestoreReviewEvidence, KnowledgeRestoreReviewEvidence, RestoreReviewEvidence,
    RestoreReviewRequest, ReviewCategory, SessionRestoreReviewEvidence,
)
from supportops_core.restore_startup_barrier import _constant, _fingerprint, _pairs
from supportops_core.retention_batch_contracts import Digest, Identifier, RetentionBatchContract, batch_digest
from supportops_core.retention_propagation_contracts import PropagationDeletionFact
from supportops_core.retention_tombstone_snapshot import MAX_FACTS


class RestoreReviewArtifactBinding(RetentionBatchContract):
    fact_id: UUID
    fact_digest: Digest
    artifact_sha256: Digest


class RestoreReviewArtifactScope(RetentionBatchContract):
    schema_version: Literal["restore_review_artifact_scope_v1"] = "restore_review_artifact_scope_v1"
    review_request_digest: Digest
    reader_id: Identifier
    category: ReviewCategory
    directory: str = Field(min_length=2, max_length=4096, repr=False)
    expected_uid: int = Field(ge=1, le=2**32 - 2)
    bindings: tuple[RestoreReviewArtifactBinding, ...] = Field(max_length=MAX_FACTS, repr=False)

    @model_validator(mode="after")
    def fixed_files(self) -> "RestoreReviewArtifactScope":
        parts = self.directory.split("/")
        ids = tuple(str(binding.fact_id) for binding in self.bindings)
        if (not self.directory.startswith("/") or "\\" in self.directory or len(parts) > 129
            or any(part in {"", ".", ".."} for part in parts[1:])
            or any(ord(char) < 32 or ord(char) == 127 for char in self.directory)
            or ids != tuple(sorted(set(ids)))):
            raise ValueError("RESTORE_REVIEW_ARTIFACT_SCOPE_INVALID")
        return self


class RestoreReviewArtifactAuthority(RestoreReviewAuthority, Protocol):
    async def assert_artifact_read_allowed(self, request: RestoreReviewRequest, fact: PropagationDeletionFact,
        scope: RestoreReviewArtifactScope, binding: RestoreReviewArtifactBinding,
    ) -> None:
        """独立确认原回执文件来源、目录/执行身份/字节摘要allowlist及当前读取许可。

        不能只信调用者提供的摘要、文件所有者或reader_id；原审计真实性仍另行查证。
        文件不授予写入或开放许可，批准变化后不得用旧文件或延长观察时间。
        """
        ...


class RestoreReviewArtifactUnavailable(RuntimeError):
    code = "RESTORE_REVIEW_ARTIFACT_UNCONFIRMED"


def _deadline(expires_at: float) -> None:
    if time.monotonic() >= expires_at:
        raise ValueError()


def _read(scope: RestoreReviewArtifactScope, fact_id: UUID, expires_at: float,
) -> tuple[bytes, tuple[tuple[int, ...], ...]]:
    """逐级NOFOLLOW并保留全部目录FD，只读取本事实固定文件；不枚举其他文件。"""
    with ExitStack() as stack:
        flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
        descriptor = os.open("/", flags | os.O_DIRECTORY)
        stack.callback(os.close, descriptor)
        directories: list[tuple[int, int | None, str | None, tuple[int, ...]]] = []
        parts = scope.directory.split("/")[1:]
        for index, part in enumerate([None, *parts]):
            _deadline(expires_at)
            parent = None
            if part is not None:
                parent = descriptor
                descriptor = os.open(part, flags | os.O_DIRECTORY, dir_fd=parent)
                stack.callback(os.close, descriptor)
            info = os.fstat(descriptor)
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in {0, scope.expected_uid}
                or stat.S_IMODE(info.st_mode) & 0o022
                or index == len(parts) and (info.st_uid != scope.expected_uid or stat.S_IMODE(info.st_mode) != 0o500)):
                raise ValueError()
            directories.append((descriptor, parent, part, _fingerprint(info)))
        # 文件名完全由已绑定UUID产生，不接受路径、后备目录、前缀或扩展名参数。
        name = f"{fact_id}.json"
        file_fd = os.open(name, flags, dir_fd=descriptor)
        stack.callback(os.close, file_fd)
        before = os.fstat(file_fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != scope.expected_uid
            or stat.S_IMODE(before.st_mode) != 0o400 or before.st_nlink != 1
            or not 0 < before.st_size <= MAX_EVIDENCE_BYTES):
            raise ValueError()
        chunks: list[bytes] = []
        size = 0
        while True:
            _deadline(expires_at)
            chunk = os.read(file_fd, min(8192, MAX_EVIDENCE_BYTES + 1 - size))
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_EVIDENCE_BYTES:
                raise ValueError()
            chunks.append(chunk)
        after = os.fstat(file_fd)
        current = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        if (_fingerprint(before) != _fingerprint(after) or _fingerprint(after) != _fingerprint(current)
            or size != after.st_size):
            raise ValueError()
        for directory_fd, parent_fd, part, expected in directories:
            _deadline(expires_at)
            if _fingerprint(os.fstat(directory_fd)) != expected:
                raise ValueError()
            if parent_fd is not None and part is not None:
                if _fingerprint(os.stat(part, dir_fd=parent_fd, follow_symlinks=False)) != expected:
                    raise ValueError()
        return b"".join(chunks), tuple(item[3] for item in directories) + (_fingerprint(after),)


def _decode(raw: bytes, category: ReviewCategory, expires_at: float) -> RestoreReviewEvidence:
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant)
    pending = [(value, 0)]
    nodes = 0
    while pending:
        _deadline(expires_at)
        item, depth = pending.pop()
        nodes += 1
        if depth > 16 or nodes > 4096:
            raise ValueError()
        if isinstance(item, dict):
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)
    if not isinstance(value, dict):
        raise ValueError()
    normalized = json.dumps(value, ensure_ascii=False, allow_nan=False)
    if category == "auth_sessions":
        return SessionRestoreReviewEvidence.model_validate_json(normalized)
    if category == "knowledge":
        return KnowledgeRestoreReviewEvidence.model_validate_json(normalized)
    if category == "credentials":
        return CredentialRestoreReviewEvidence.model_validate_json(normalized)
    raise ValueError()


class ProtectedRestoreReviewReader:
    """原回执文件只读适配；源文件需独立批准产生/持久保留，没有导出或默认配置。"""

    def __init__(self, request: RestoreReviewRequest, scope: RestoreReviewArtifactScope, *,
        authority: RestoreReviewArtifactAuthority,
    ) -> None:
        if (not isinstance(request, RestoreReviewRequest) or not isinstance(scope, RestoreReviewArtifactScope)
            or authority is None or authority.authority_id != request.authority_id
            or len(request.model_dump_json().encode("utf-8")) > MAX_INPUT_BYTES
            or scope.review_request_digest != batch_digest(request)
            or not any(reader.category == scope.category and reader.reader_id == scope.reader_id for reader in request.readers)):
            raise RestoreReviewArtifactUnavailable()
        facts = {fact.id: fact for fact in request.facts if fact.category == scope.category}
        if any(binding.fact_id not in facts or binding.fact_digest != facts[binding.fact_id].record_digest
            for binding in scope.bindings):
            raise RestoreReviewArtifactUnavailable()
        self._scope, self._authority = scope, authority
        self._facts = facts
        self._bindings = {binding.fact_id: binding for binding in scope.bindings}

    @property
    def reader_id(self) -> str:
        return self._scope.reader_id

    async def _current(self, request: RestoreReviewRequest, fact: PropagationDeletionFact,
        binding: RestoreReviewArtifactBinding | None,
        evidence: RestoreReviewEvidence | None = None,
    ) -> None:
        _context(request)
        if os.geteuid() != self._scope.expected_uid or self._authority.authority_id != request.authority_id:
            raise ValueError()
        async with asyncio.timeout(0.5):
            await self._authority.assert_current(request)
            if binding is not None:
                await self._authority.assert_artifact_read_allowed(request, fact, self._scope, binding)
            if evidence is not None:
                await self._authority.assert_evidence_current(request, fact, evidence)
        _context(request)
        if os.geteuid() != self._scope.expected_uid or self._authority.authority_id != request.authority_id:
            raise ValueError()

    async def read(self, request: RestoreReviewRequest, fact: PropagationDeletionFact) -> RestoreReviewEvidence | None:
        try:
            expires_at = time.monotonic() + 0.9
            async with asyncio.timeout(0.9):
                if (not isinstance(request, RestoreReviewRequest) or not isinstance(fact, PropagationDeletionFact)
                    or batch_digest(request) != self._scope.review_request_digest
                    or fact.id not in self._facts or fact.record_digest != self._facts[fact.id].record_digest
                    or fact.category != self._scope.category):
                    raise ValueError()
                binding = self._bindings.get(fact.id)
                await self._current(request, fact, binding)
                if binding is None:
                    evidence = None
                else:
                    raw, fingerprint = _read(self._scope, fact.id, expires_at)
                    if hashlib.sha256(raw).hexdigest() != binding.artifact_sha256:
                        raise ValueError()
                    evidence = _decode(raw, self._scope.category, expires_at)
                    _packet(request, fact, self.reader_id, evidence)
                    await self._current(request, fact, binding, evidence)
                    # 覆盖授权等待期间的变化；保留原回执观察时间，不用本次读取时间刷新TTL。
                    repeated, current = _read(self._scope, fact.id, expires_at)
                    if fingerprint != current or raw != repeated:
                        raise ValueError()
                    _packet(request, fact, self.reader_id, evidence)
                _context(request)
                _deadline(expires_at)
            return evidence
        except Exception:
            raise RestoreReviewArtifactUnavailable() from None
