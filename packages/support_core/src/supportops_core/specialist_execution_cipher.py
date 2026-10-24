"""复用既有Fernet依赖的专用响应信封；与凭据用途分离，不自动加载主密钥。"""

import base64
import hashlib
import json
from uuid import UUID

from cryptography.fernet import Fernet

from supportops_core.specialist_request_contracts import canonical_request_json
from supportops_core.specialist_response_contracts import SPECIALIST_RESPONSE_BYTES_MAX


class SpecialistResponseCipher:
    def __init__(self, *, key_id: UUID, master_key: str) -> None:
        if not isinstance(key_id, UUID) or not 32 <= len(master_key) <= 20000:
            raise ValueError("SPECIALIST_RESPONSE_KEY_UNAVAILABLE")
        self.key_id, self._master_key = key_id, master_key

    def _fernet(self, tenant_id: UUID) -> Fernet:
        key = hashlib.sha256(b"supportops.specialist.response.v1\0" + tenant_id.bytes + self._master_key.encode("utf-8")).digest()
        return Fernet(base64.urlsafe_b64encode(key))

    def seal(self, *, tenant_id: UUID, scope_digest: str, answer_json: str) -> str:
        try:
            if len(answer_json) > SPECIALIST_RESPONSE_BYTES_MAX or len(answer_json.encode("utf-8")) > SPECIALIST_RESPONSE_BYTES_MAX:
                raise ValueError()
            packet = canonical_request_json({"schema_version": "specialist_response_envelope_v1",
                "scope_digest": scope_digest, "answer_json": answer_json})
            sealed = self._fernet(tenant_id).encrypt(packet.encode("utf-8")).decode("ascii")
            if len(sealed) > 524288:
                raise ValueError()
            return sealed
        except Exception:
            raise ValueError("SPECIALIST_RESPONSE_SEAL_FAILED") from None

    def open(self, *, tenant_id: UUID, scope_digest: str, sealed: str) -> str:
        try:
            if len(sealed) > 524288:
                raise ValueError()
            packet = json.loads(self._fernet(tenant_id).decrypt(sealed.encode("ascii")))
            if (set(packet) != {"schema_version", "scope_digest", "answer_json"}
                or packet["schema_version"] != "specialist_response_envelope_v1" or packet["scope_digest"] != scope_digest
                or not isinstance(packet["answer_json"], str)
                or len(packet["answer_json"].encode("utf-8")) > SPECIALIST_RESPONSE_BYTES_MAX):
                raise ValueError()
            return packet["answer_json"]
        except Exception:
            raise ValueError("SPECIALIST_RESPONSE_OPEN_FAILED") from None
