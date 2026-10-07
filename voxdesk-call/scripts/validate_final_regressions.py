"""Run changed-contract and neighboring regression files, retaining phase evidence."""
from __future__ import annotations

import collections
import gc
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import validate_full_population as runner  # noqa: E402


def main():
    runner.EVIDENCE = ROOT / ".prompt8b" / "regression"
    runner.EVIDENCE.mkdir(exist_ok=True)
    manifests = []
    for n in (1, 2):
        result, events = runner.run(f"collection-{n}", ["--collect-only", "-q"], 300)
        if result["exit_code"] != 0:
            raise RuntimeError(result)
        manifests.append(next(e["nodeids"] for e in events if e["kind"] == "collection"))
    if manifests[0] != manifests[1] or len(set(manifests[0])) != len(manifests[0]):
        raise RuntimeError("Collection drift")
    (ROOT / "tests_manifest.txt").write_text("\n".join(manifests[0]) + "\n")
    names = {
        "tests/test_external_success_honesty.py", "tests/test_placeholder_security_regressions.py",
        "tests/test_value_types.py", "tests/test_agent_config.py", "tests/test_agent_catalog_routes.py",
        "tests/test_stt_lazy_import.py", "tests/test_stt_endpoint_security.py", "tests/test_tts.py",
        "tests/test_retell_parity.py", "tests/test_enterprise_batch01.py",
        "tests/test_telephony_runtime_e2e.py", "tests/test_telephony_security_idempotency.py",
        "tests/test_calendar_api.py", "tests/operations/test_notification_webhook_email.py",
    }
    groups = collections.defaultdict(list)
    for node in manifests[0]:
        file = node.split("::")[0]
        if file in names or file.startswith(("tests/e2e/", "tests/integration/", "tests/telephony/")):
            groups[file].append(node)
    os.environ.update(runner.ENV)
    import app.main  # noqa: F401
    gc.collect()
    gc.freeze()
    summaries = []
    for file, nodes in groups.items():
        for offset in range(0, len(nodes), 50):
            label = f"regression-{len(summaries) + 1:04d}"
            result, events = runner.run(label, ["-q", "-ra", *nodes[offset:offset + 50]], 300)
            summaries.append(result)
            (runner.EVIDENCE / "summary.json").write_text(json.dumps(summaries, indent=2))
            print(label, file, result["exit_code"], flush=True)
    return int(any(s["exit_code"] != 0 for s in summaries))


if __name__ == "__main__":
    raise SystemExit(main())
