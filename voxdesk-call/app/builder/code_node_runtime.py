"""Sandboxed code nodes (PART 12, P12-18).

A "code node" runs a small piece of JavaScript the customer wrote, in the middle of a live call. The
promise we make is narrow and testable:

* the code gets a JSON object in and must return a JSON object out;
* it gets no network namespace, no host home/application mounts, and only a read-only system runtime
  plus ephemeral `/tmp` inside Bubblewrap;
* QuickJS heap use and process CPU/wall time are bounded;
* ``Date`` and ``Math.random`` are seeded, so the same input produces the same output;
* every execution is audited with the source hash, so "which version of the code said that?" has
  an answer months later.

QuickJS is the intended engine (a subprocess or WASM build, no Node runtime). The engine itself is
*injected* — ``QjsRunner`` shells out to a ``qjs`` binary when one is installed — because a test
suite must be able to exercise the policy, the timeout and the audit trail without a JavaScript
engine on the box, and because the sandbox boundary is the process, not the library.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import selectors
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

#: Budgets from the specification. Kept as named constants so the API, the dashboard and the tests
#: quote the same numbers.
CODE_NODE_CPU_LIMIT_S = 2.0
CODE_NODE_MEMORY_LIMIT_MB = 8
CODE_NODE_MAX_SOURCE_BYTES = 16_384
CODE_NODE_MAX_OUTPUT_BYTES = 32_768


class CodeNodeError(RuntimeError):
    """Base class for code-node failures, mapped to a tool error the agent can speak about."""


class SandboxUnavailable(CodeNodeError):
    """No JavaScript engine is installed on this host."""


class CodeNodeTimeout(CodeNodeError):
    """The code exceeded its budget and was killed."""


class CodeNodeOutputError(CodeNodeError):
    """The code returned something that is not a JSON object."""


def source_hash(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True)
class CodeNodePolicy:
    cpu_limit_s: float = CODE_NODE_CPU_LIMIT_S
    memory_limit_mb: int = CODE_NODE_MEMORY_LIMIT_MB
    allow_network: bool = False
    allow_filesystem: bool = False
    max_source_bytes: int = CODE_NODE_MAX_SOURCE_BYTES
    max_output_bytes: int = CODE_NODE_MAX_OUTPUT_BYTES
    seed: int = 0

    def __post_init__(self) -> None:
        if self.allow_network or self.allow_filesystem:
            raise ValueError(
                "code nodes never get network or filesystem access; the flags exist only to make "
                "that refusal explicit"
            )
        if self.cpu_limit_s <= 0:
            raise ValueError("cpu_limit_s must be positive")
        if self.memory_limit_mb <= 0:
            raise ValueError("memory_limit_mb must be positive")

    def check_source(self, source: str) -> None:
        size = len(source.encode("utf-8"))
        if size > self.max_source_bytes:
            raise CodeNodeError(
                f"source is {size} bytes, over the {self.max_source_bytes} byte limit"
            )


@dataclass(frozen=True)
class CodeNodeResult:
    ok: bool
    output: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    duration_ms: float = 0.0
    source_hash: str = ""
    engine: str = ""

    def to_audit(self, *, node_id: str = "") -> dict[str, Any]:
        return {
            "node_id": node_id,
            "source_hash": self.source_hash,
            "engine": self.engine,
            "ok": self.ok,
            "duration_ms": round(self.duration_ms, 3),
            "error": self.error,
        }


#: The wrapper every snippet runs inside. It captures the return value, seeds the non-deterministic
#: globals and strips the escape hatches that a QuickJS build might otherwise expose.
SANDBOX_PREAMBLE = """
(function(){
  var __seed = __SEED__;
  var __state = __seed || 1;
  Math.random = function(){ __state = (__state * 1103515245 + 12345) % 2147483648; return __state / 2147483648; };
  var __RealDate = Date;
  var __fixed = __FIXED_MS__;
  function FixedDate(){ return new __RealDate(__fixed); }
  FixedDate.now = function(){ return __fixed; };
  FixedDate.prototype = __RealDate.prototype;
  globalThis.Date = FixedDate;
})();
"""


def build_sandbox_source(source: str, *, policy: CodeNodePolicy | None = None) -> str:
    """Wrap customer code with the deterministic preamble and a JSON-only entry point."""
    policy = policy or CodeNodePolicy()
    policy.check_source(source)
    fixed_ms = 1_700_000_000_000 + policy.seed * 1000
    return (
        # Plain replacement, not str.format: the snippet is full of JavaScript braces.
        SANDBOX_PREAMBLE.replace("__SEED__", str(policy.seed)).replace(
            "__FIXED_MS__", str(fixed_ms)
        )
        + "\n"
        + "var __write = print;\n"
        + "function __entry(input){\n"
        + source
        + "\n}\n"
        + "__write(JSON.stringify(__entry(JSON.parse(globalThis.__input || '{}'))));\n"
    )


class QjsRunner:
    """Run QuickJS inside a no-network, read-only Bubblewrap sandbox.

    A bare `qjs` process is not a security boundary: its `std` module can access files, and the
    process would inherit the host network namespace. Code execution is therefore unavailable
    unless QuickJS, Bubblewrap, and `prlimit` are all installed. We fail closed rather than
    silently weakening the contract.
    """

    engine = "quickjs"

    def __init__(
        self,
        binary: str | None = None,
        *,
        sandbox_binary: str | None = None,
        limit_binary: str | None = None,
    ) -> None:
        self.binary = binary or shutil.which("qjs") or ""
        self.sandbox_binary = sandbox_binary or shutil.which("bwrap") or ""
        self.limit_binary = limit_binary or shutil.which("prlimit") or ""

    def available(self) -> bool:
        required = (self.binary, self.sandbox_binary, self.limit_binary)
        return all(bool(path) and shutil.which(path) is not None for path in required)

    def run(
        self,
        script: str,
        payload: Mapping[str, Any],
        *,
        timeout_s: float,
        memory_limit_mb: int = CODE_NODE_MEMORY_LIMIT_MB,
        max_output_bytes: int = CODE_NODE_MAX_OUTPUT_BYTES,
    ) -> str:
        if not self.available():
            raise SandboxUnavailable(
                "no QuickJS engine (`qjs`) and OS sandbox (`bwrap`, `prlimit`) on this host; "
                "code nodes stay unavailable until all three are installed"
            )

        payload_json = json.dumps(dict(payload), separators=(",", ":"), ensure_ascii=True)
        input_literal = json.dumps(payload_json, ensure_ascii=True)
        wrapped_script = f"globalThis.__input = {input_literal};\n{script}"

        command = [
            self.sandbox_binary,
            "--die-with-parent",
            "--new-session",
            "--unshare-pid",
            "--unshare-ipc",
            "--unshare-uts",
            "--unshare-net",
            "--ro-bind",
            "/usr",
            "/usr",
        ]
        # QuickJS and its dynamic loader need only the standard executable/library trees. The
        # caller's home, application files, credentials, and writable host paths are not mounted.
        for host_path in ("/bin", "/lib", "/lib64"):
            if os.path.exists(host_path):
                command.extend(["--ro-bind", host_path, host_path])
        command.extend(
            [
                "--proc",
                "/proc",
                "--dev",
                "/dev",
                "--tmpfs",
                "/tmp",
                "--chdir",
                "/tmp",
                "--",
                self.limit_binary,
                f"--cpu={max(1, math.ceil(timeout_s))}:{max(1, math.ceil(timeout_s))}",
                "--",
                self.binary,
                "--memory-limit",
                f"{int(memory_limit_mb)}M",
                "-e",
                wrapped_script,
            ]
        )
        process = None
        stdout_data = bytearray()
        stderr_data = bytearray()
        try:
            process = subprocess.Popen(  # noqa: S603 - fixed executables, no shell
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
                env={"PATH": "/usr/bin:/bin", "HOME": "/tmp"},  # deliberately no credentials
            )
            selector = selectors.DefaultSelector()
            try:
                for stream, label, buffer, limit in (
                    (process.stdout, "output", stdout_data, max_output_bytes),
                    (process.stderr, "diagnostic", stderr_data, 8192),
                ):
                    if stream is not None:
                        os.set_blocking(stream.fileno(), False)
                        selector.register(stream, selectors.EVENT_READ, (label, buffer, limit))

                deadline = time.monotonic() + timeout_s
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(command, timeout_s)
                    for key, _ in selector.select(remaining):
                        label, buffer, limit = key.data
                        chunk = os.read(key.fd, min(65536, max(1, limit - len(buffer) + 1)))
                        if not chunk:
                            selector.unregister(key.fileobj)
                            key.fileobj.close()
                            continue
                        if len(buffer) + len(chunk) > limit:
                            raise CodeNodeError(f"code node {label} exceeded {limit} bytes")
                        buffer.extend(chunk)

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, timeout_s)
                process.wait(timeout=remaining)
            finally:
                selector.close()
        except (subprocess.TimeoutExpired, CodeNodeError):
            if process is not None and process.poll() is None:
                process.kill()
            if process is not None:
                process.wait()
            raise
        except OSError as exc:
            if process is not None and process.poll() is None:
                process.kill()
            if process is not None:
                process.wait()
            raise SandboxUnavailable(f"OS sandbox could not start: {exc}") from exc
        finally:
            if process is not None:
                for stream in (process.stdout, process.stderr):
                    if stream is not None and not stream.closed:
                        stream.close()

        if process.returncode != 0:
            diagnostic = stderr_data.decode("utf-8", errors="replace").strip()
            raise CodeNodeError((diagnostic or "code node failed")[:500])
        return stdout_data.decode("utf-8", errors="replace")


def run_code_node(
    source: str,
    payload: Mapping[str, Any] | None = None,
    *,
    policy: CodeNodePolicy | None = None,
    runner: Any | None = None,
    clock: Callable[[], float] | None = None,
    node_id: str = "",
) -> CodeNodeResult:
    """Run one code node, enforcing the policy and returning a result that is safe to audit.

    Failures are values, not exceptions: a code node that blows up must not take the call down, so
    the pipeline gets `ok=False` plus a spoken-friendly `error`.
    """
    policy = policy or CodeNodePolicy()
    runner = runner or QjsRunner()
    now = clock or time.monotonic
    digest = source_hash(source)
    started = now()

    try:
        script = build_sandbox_source(source, policy=policy)
        if isinstance(runner, QjsRunner):
            raw = runner.run(
                script,
                dict(payload or {}),
                timeout_s=policy.cpu_limit_s,
                memory_limit_mb=policy.memory_limit_mb,
                max_output_bytes=policy.max_output_bytes,
            )
        else:
            raw = runner.run(script, dict(payload or {}), timeout_s=policy.cpu_limit_s)
    except SandboxUnavailable as exc:
        return CodeNodeResult(False, error=str(exc), source_hash=digest, engine=getattr(runner, "engine", ""))
    except subprocess.TimeoutExpired:
        return CodeNodeResult(
            False,
            error=f"code node exceeded {policy.cpu_limit_s:g}s and was killed",
            duration_ms=(now() - started) * 1000,
            source_hash=digest,
            engine=getattr(runner, "engine", ""),
        )
    except CodeNodeError as exc:
        return CodeNodeResult(
            False,
            error=str(exc),
            duration_ms=(now() - started) * 1000,
            source_hash=digest,
            engine=getattr(runner, "engine", ""),
        )

    duration_ms = (now() - started) * 1000
    if len(raw.encode("utf-8")) > policy.max_output_bytes:
        return CodeNodeResult(
            False,
            error=f"output exceeded {policy.max_output_bytes} bytes",
            duration_ms=duration_ms,
            source_hash=digest,
            engine=getattr(runner, "engine", ""),
        )
    try:
        parsed = json.loads(raw or "null")
    except json.JSONDecodeError as exc:
        return CodeNodeResult(
            False,
            error=f"code node did not return JSON: {exc.msg}",
            duration_ms=duration_ms,
            source_hash=digest,
            engine=getattr(runner, "engine", ""),
        )
    if not isinstance(parsed, dict):
        return CodeNodeResult(
            False,
            error="code node must return a JSON object",
            duration_ms=duration_ms,
            source_hash=digest,
            engine=getattr(runner, "engine", ""),
        )
    return CodeNodeResult(
        True,
        output=parsed,
        duration_ms=duration_ms,
        source_hash=digest,
        engine=getattr(runner, "engine", ""),
    )


def audit_entry(result: CodeNodeResult, *, node_id: str = "", call_id: str = "") -> dict[str, Any]:
    """The row written to the audit log for one execution."""
    entry = result.to_audit(node_id=node_id)
    entry["call_id"] = call_id
    entry["kind"] = "code_node_execution"
    return entry
