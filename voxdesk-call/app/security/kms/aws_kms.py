"""Real AWS KMS and HashiCorp Vault Transit envelope-encryption adapters.

Implements:
- ``AwsKmsAdapter``: Calls the AWS KMS JSON 1.1 API (``TrentService.GenerateDataKey``,
  ``TrentService.Encrypt``, ``TrentService.Decrypt``) signed with AWS Signature
  Version 4 (HMAC-SHA256) over HTTPS, validated through ``app.core.ssrf``.
- ``VaultTransitKmsAdapter``: Calls HashiCorp Vault Transit Engine
  (``/v1/transit/datakey/plaintext/{key}``, ``/v1/transit/encrypt/{key}``,
  ``/v1/transit/decrypt/{key}``) over HTTPS, validated through ``app.core.ssrf``.

Both adapters fail closed with ``KmsNotConfiguredError`` (``NOT_CONFIGURED``)
when credentials or key identifiers are missing, and never fabricate wrapped keys.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
import re
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.core.ssrf import OutboundUrlError, validate_outbound_url
from app.security.kms import (
    KmsNotConfiguredError,
    KmsOperationError,
    WrappedDataKey,
)

_REGION_RE = re.compile(r"^[a-z]{2}(-[a-z]+)+-\d+$")


def _sign_sigv4(
    *,
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes,
    access_key: str,
    secret_key: str,
    region: str,
    service: str = "kms",
    now: dt.datetime | None = None,
) -> dict[str, str]:
    """Compute AWS Signature Version 4 headers for a KMS JSON 1.1 request."""
    timestamp = (now or dt.datetime.now(dt.timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    datestamp = timestamp[:8]
    parts = urlsplit(url)
    host = parts.netloc
    canonical_uri = parts.path or "/"
    payload_hash = hashlib.sha256(body).hexdigest()

    to_sign_headers = {k.lower(): v.strip() for k, v in headers.items()}
    to_sign_headers["host"] = host
    to_sign_headers["x-amz-date"] = timestamp
    to_sign_headers["x-amz-content-sha256"] = payload_hash

    sorted_keys = sorted(to_sign_headers.keys())
    canonical_headers = "".join(f"{k}:{to_sign_headers[k]}\n" for k in sorted_keys)
    signed_headers = ";".join(sorted_keys)

    canonical_request = "\n".join(
        [
            method.upper(),
            canonical_uri,
            parts.query,
            canonical_headers,
            signed_headers,
            payload_hash,
        ]
    )
    credential_scope = f"{datestamp}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            timestamp,
            credential_scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )

    def _hmac(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

    k_date = _hmac(("AWS4" + secret_key).encode("utf-8"), datestamp)
    k_region = _hmac(k_date, region)
    k_service = _hmac(k_region, service)
    k_signing = _hmac(k_service, "aws4_request")
    signature = hmac.new(
        k_signing, string_to_sign.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    authorization = (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    out = dict(headers)
    out["Host"] = host
    out["X-Amz-Date"] = timestamp
    out["X-Amz-Content-Sha256"] = payload_hash
    out["Authorization"] = authorization
    return out


class AwsKmsAdapter:
    """AWS KMS adapter using SigV4-authenticated HTTPS requests."""

    provider_name = "aws_kms"

    def __init__(
        self,
        *,
        key_id: str | None = None,
        region_name: str = "us-east-1",
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        session_token: str | None = None,
        endpoint_url: str | None = None,
        http_client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.key_id = (key_id or "").strip()
        self.region_name = (region_name or "us-east-1").strip()
        self.access_key_id = (access_key_id or "").strip()
        self.secret_access_key = (secret_access_key or "").strip()
        self.session_token = (session_token or "").strip() or None
        self.endpoint_url = (
            endpoint_url.strip()
            if endpoint_url
            else f"https://kms.{self.region_name}.amazonaws.com/"
        )
        self._http_client = http_client
        self._timeout_seconds = timeout_seconds

    def is_configured(self) -> bool:
        return bool(
            self.key_id
            and self.access_key_id
            and self.secret_access_key
            and _REGION_RE.fullmatch(self.region_name)
        )

    def _require_configured(self) -> None:
        if not self.is_configured():
            raise KmsNotConfiguredError(
                "AWS KMS adapter requires key_id, valid region_name, access_key_id, and secret_access_key"
            )
        try:
            validate_outbound_url(self.endpoint_url, require_https=True)
        except OutboundUrlError as exc:
            raise KmsOperationError(f"Unsafe KMS endpoint URL rejected: {exc}") from exc

    async def _call_kms(self, target_action: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_configured()
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        base_headers: dict[str, str] = {
            "Content-Type": "application/x-amz-json-1.1",
            "X-Amz-Target": f"TrentService.{target_action}",
        }
        if self.session_token:
            base_headers["X-Amz-Security-Token"] = self.session_token

        signed_headers = _sign_sigv4(
            method="POST",
            url=self.endpoint_url,
            headers=base_headers,
            body=body,
            access_key=self.access_key_id,
            secret_key=self.secret_access_key,
            region=self.region_name,
            service="kms",
        )

        try:
            if self._http_client is not None:
                response = await self._http_client.post(
                    self.endpoint_url, content=body, headers=signed_headers
                )
            else:
                async with httpx.AsyncClient(
                    timeout=self._timeout_seconds, follow_redirects=False
                ) as client:
                    response = await client.post(
                        self.endpoint_url, content=body, headers=signed_headers
                    )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise KmsOperationError(f"AWS KMS transport error on {target_action}") from exc

        if response.status_code != 200:
            raise KmsOperationError(
                f"AWS KMS {target_action} failed with HTTP {response.status_code}"
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise KmsOperationError("AWS KMS returned non-JSON body") from exc
        if not isinstance(data, dict):
            raise KmsOperationError("AWS KMS response is not a JSON object")
        return data

    async def generate_data_key(
        self, *, encryption_context: dict[str, str] | None = None
    ) -> tuple[bytes, WrappedDataKey]:
        ctx = {str(k): str(v) for k, v in (encryption_context or {}).items()}
        req: dict[str, Any] = {
            "KeyId": self.key_id,
            "KeySpec": "AES_256",
        }
        if ctx:
            req["EncryptionContext"] = ctx
        resp = await self._call_kms("GenerateDataKey", req)
        try:
            plaintext = base64.b64decode(resp["Plaintext"])
            ciphertext_blob = base64.b64decode(resp["CiphertextBlob"])
            resolved_key_id = str(resp.get("KeyId") or self.key_id)
        except Exception as exc:
            raise KmsOperationError("AWS KMS GenerateDataKey response missing key fields") from exc
        if len(plaintext) != 32:
            raise KmsOperationError("AWS KMS returned non-256-bit Plaintext key")
        return (
            plaintext,
            WrappedDataKey(
                key_id=resolved_key_id,
                ciphertext_blob=ciphertext_blob,
                provider=self.provider_name,
                encryption_context=ctx,
            ),
        )

    async def wrap_data_key(
        self,
        plaintext_key: bytes,
        *,
        encryption_context: dict[str, str] | None = None,
    ) -> WrappedDataKey:
        if not isinstance(plaintext_key, (bytes, bytearray)) or len(plaintext_key) != 32:
            raise KmsOperationError("Plaintext data key must be 32 bytes")
        ctx = {str(k): str(v) for k, v in (encryption_context or {}).items()}
        req: dict[str, Any] = {
            "KeyId": self.key_id,
            "Plaintext": base64.b64encode(bytes(plaintext_key)).decode("ascii"),
        }
        if ctx:
            req["EncryptionContext"] = ctx
        resp = await self._call_kms("Encrypt", req)
        try:
            ciphertext_blob = base64.b64decode(resp["CiphertextBlob"])
            resolved_key_id = str(resp.get("KeyId") or self.key_id)
        except Exception as exc:
            raise KmsOperationError("AWS KMS Encrypt response missing CiphertextBlob") from exc
        return WrappedDataKey(
            key_id=resolved_key_id,
            ciphertext_blob=ciphertext_blob,
            provider=self.provider_name,
            encryption_context=ctx,
        )

    async def unwrap_data_key(
        self,
        wrapped_key: WrappedDataKey,
        *,
        encryption_context: dict[str, str] | None = None,
    ) -> bytes:
        ctx = (
            {str(k): str(v) for k, v in encryption_context.items()}
            if encryption_context is not None
            else dict(wrapped_key.encryption_context)
        )
        req: dict[str, Any] = {
            "CiphertextBlob": base64.b64encode(wrapped_key.ciphertext_blob).decode("ascii"),
            "KeyId": wrapped_key.key_id or self.key_id,
        }
        if ctx:
            req["EncryptionContext"] = ctx
        resp = await self._call_kms("Decrypt", req)
        try:
            plaintext = base64.b64decode(resp["Plaintext"])
        except Exception as exc:
            raise KmsOperationError("AWS KMS Decrypt response missing Plaintext") from exc
        if len(plaintext) != 32:
            raise KmsOperationError("AWS KMS Decrypt returned non-256-bit key")
        return plaintext


class VaultTransitKmsAdapter:
    """HashiCorp Vault Transit engine adapter over HTTPS."""

    provider_name = "vault_transit"

    def __init__(
        self,
        *,
        vault_addr: str | None = None,
        vault_token: str | None = None,
        key_name: str | None = None,
        mount_point: str = "transit",
        http_client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.vault_addr = (vault_addr or "").strip().rstrip("/")
        self.vault_token = (vault_token or "").strip()
        self.key_name = (key_name or "").strip()
        self.mount_point = (mount_point or "transit").strip().strip("/")
        self._http_client = http_client
        self._timeout_seconds = timeout_seconds

    def is_configured(self) -> bool:
        return bool(self.vault_addr and self.vault_token and self.key_name)

    def _require_configured(self, url: str) -> None:
        if not self.is_configured():
            raise KmsNotConfiguredError(
                "Vault Transit adapter requires vault_addr, vault_token, and key_name"
            )
        try:
            validate_outbound_url(url, require_https=True)
        except OutboundUrlError as exc:
            raise KmsOperationError(f"Unsafe Vault endpoint URL rejected: {exc}") from exc

    @staticmethod
    def _encode_context(encryption_context: dict[str, str] | None) -> str | None:
        if not encryption_context:
            return None
        canonical = json.dumps(encryption_context, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return base64.b64encode(canonical).decode("ascii")

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.vault_addr}/v1/{self.mount_point}/{path.lstrip('/')}"
        self._require_configured(url)
        headers = {
            "X-Vault-Token": self.vault_token,
            "Content-Type": "application/json",
        }
        try:
            if self._http_client is not None:
                resp = await self._http_client.post(url, json=payload, headers=headers)
            else:
                async with httpx.AsyncClient(
                    timeout=self._timeout_seconds, follow_redirects=False
                ) as client:
                    resp = await client.post(url, json=payload, headers=headers)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise KmsOperationError("Vault Transit request transport failure") from exc

        if resp.status_code != 200:
            raise KmsOperationError(f"Vault Transit returned HTTP {resp.status_code}")
        data = resp.json()
        if not isinstance(data, dict) or not isinstance(data.get("data"), dict):
            raise KmsOperationError("Vault Transit response missing data object")
        return data["data"]

    async def generate_data_key(
        self, *, encryption_context: dict[str, str] | None = None
    ) -> tuple[bytes, WrappedDataKey]:
        ctx = {str(k): str(v) for k, v in (encryption_context or {}).items()}
        payload: dict[str, Any] = {"bits": 256}
        ctx_b64 = self._encode_context(ctx)
        if ctx_b64:
            payload["context"] = ctx_b64
        data = await self._post(f"datakey/plaintext/{self.key_name}", payload)
        plaintext = base64.b64decode(data["plaintext"])
        ciphertext = str(data["ciphertext"]).encode("utf-8")
        return (
            plaintext,
            WrappedDataKey(
                key_id=self.key_name,
                ciphertext_blob=ciphertext,
                provider=self.provider_name,
                encryption_context=ctx,
            ),
        )

    async def wrap_data_key(
        self,
        plaintext_key: bytes,
        *,
        encryption_context: dict[str, str] | None = None,
    ) -> WrappedDataKey:
        if len(plaintext_key) != 32:
            raise KmsOperationError("Plaintext data key must be 32 bytes")
        ctx = {str(k): str(v) for k, v in (encryption_context or {}).items()}
        payload: dict[str, Any] = {
            "plaintext": base64.b64encode(bytes(plaintext_key)).decode("ascii")
        }
        ctx_b64 = self._encode_context(ctx)
        if ctx_b64:
            payload["context"] = ctx_b64
        data = await self._post(f"encrypt/{self.key_name}", payload)
        return WrappedDataKey(
            key_id=self.key_name,
            ciphertext_blob=str(data["ciphertext"]).encode("utf-8"),
            provider=self.provider_name,
            encryption_context=ctx,
        )

    async def unwrap_data_key(
        self,
        wrapped_key: WrappedDataKey,
        *,
        encryption_context: dict[str, str] | None = None,
    ) -> bytes:
        ctx = (
            {str(k): str(v) for k, v in encryption_context.items()}
            if encryption_context is not None
            else dict(wrapped_key.encryption_context)
        )
        payload: dict[str, Any] = {
            "ciphertext": wrapped_key.ciphertext_blob.decode("utf-8")
        }
        ctx_b64 = self._encode_context(ctx)
        if ctx_b64:
            payload["context"] = ctx_b64
        data = await self._post(f"decrypt/{wrapped_key.key_name if hasattr(wrapped_key, 'key_name') else (wrapped_key.key_id or self.key_name)}", payload)
        plaintext = base64.b64decode(data["plaintext"])
        if len(plaintext) != 32:
            raise KmsOperationError("Vault Transit returned non-256-bit key")
        return plaintext
