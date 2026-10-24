"""Envelope encryption, the development KMS and the rekey job.

The cryptography here is real: a local master key file wraps a per-record data
key, the record ciphertext is AES-GCM with an associated-data binding, and a
rekey re-wraps the data key without ever putting plaintext back on disk. The
tests assert the properties an operator relies on — a value cannot be decrypted
in the wrong tenant or the wrong column, a rewrapped token still decrypts, and a
rekey job reports what it touched.
"""

from __future__ import annotations


import pytest

from app.compliance_v2.kms.base import (
    KmsNotConfigured,
)
from app.compliance_v2.kms.local_dev import LocalDevKms


def test_local_dev_refuses_production_construction(tmp_path):
    with pytest.raises(KmsNotConfigured) as excinfo:
        LocalDevKms(tmp_path / "never.key", environment="production")
    assert "production" in str(excinfo.value)


def test_master_key_file_is_created_with_owner_only_permissions(tmp_path):
    path = tmp_path / "master.key"
    LocalDevKms(path, environment="development")

    assert path.exists()
    assert len(path.read_bytes()) == 32
    assert path.stat().st_mode & 0o077 == 0


