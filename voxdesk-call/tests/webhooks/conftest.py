"""Real Redis and test-only random AES keys for webhook API contracts."""
import base64
import os
import shutil
import socket
import subprocess
import time

import pytest

from app.core.config import settings


@pytest.fixture(scope="session")
def webhook_redis_url():
    executable = shutil.which("redis-server")
    if executable is None:
        pytest.fail("Webhook API contracts require redis-server; install Redis first")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    process = subprocess.Popen([executable, "--bind", "127.0.0.1", "--port", str(port),
                                "--save", "", "--appendonly", "no"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            if process.poll() is not None:
                pytest.fail("Isolated Redis exited before accepting connections")
            with socket.socket() as probe:
                if probe.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.02)
        else:
            pytest.fail("Isolated Redis did not become ready")
        yield f"redis://127.0.0.1:{port}/0"
    finally:
        process.terminate()
        process.wait(timeout=5)


@pytest.fixture
def webhook_configuration(monkeypatch, webhook_redis_url):
    monkeypatch.setattr(settings, "redis_url", webhook_redis_url)
    monkeypatch.setattr(settings, "identity_encryption_keys",
                        "test:" + base64.urlsafe_b64encode(os.urandom(32)).decode())


@pytest.fixture(autouse=True)
def webhook_crypto(monkeypatch):
    """An explicit test-only key, never a production encryption fallback."""
    monkeypatch.setattr(settings, "identity_encryption_keys",
                        "test:" + base64.urlsafe_b64encode(os.urandom(32)).decode())
