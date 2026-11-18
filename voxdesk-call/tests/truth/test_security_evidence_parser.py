"""Truth test: the security-evidence collector can actually read what CI writes.

`reports/SECURITY_EVIDENCE_SUMMARY.json` is buyer-facing, so every scanner column
in it has to be able to report a non-zero number. Three of them could not, and PART
10 found it only by running the scanners for real instead of reading the summary:

* ``parse_hadolint`` split the file on newlines and kept lines starting with
  ``{`` — but ``hadolint --format json`` emits ONE JSON ARRAY on a single line,
  so any Dockerfile tree, warnings or not, counted as 0.
* ``parse_checkov`` assumed one ``{"results": {...}}`` object — but ``checkov
  --output json --output-file-path <dir>`` writes a LIST of per-framework report
  objects, so the real CI artifact raised ``AttributeError`` in the summariser.
* ``KNOWN_SCANNERS`` calls semgrep's kind ``sarif``, so ``semgrep-results.json``
  — the report CI writes beside the SARIF — went to the SARIF reader, found no
  ``runs`` array, and counted 0 while 26 findings sat in the file.

Both shapes are asserted here from the exact bytes the tools produce, plus the
corrupt-input case: an artifact that cannot be parsed must be reported as
unparseable, never as a clean zero.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts.security_evidence import classify, parse_artifact, parse_checkov, parse_hadolint

_HADOLINT = [
    {"code": "DL3008", "column": 1, "file": "Dockerfile", "level": "warning", "line": 73,
     "message": "Pin versions in apt get install."},
    {"code": "DL4006", "column": 5, "file": "Dockerfile", "level": "warning", "line": 78,
     "message": "Set the SHELL option -o pipefail before RUN with a pipe."},
]

_CHECKOV = [
    {
        "check_type": "terraform",
        "results": {
            "failed_checks": [
                {
                    "check_id": "CKV_AWS_23",
                    "check_name": "Ensure no Security Group allows open ingress",
                    "file_path": "/modules/alb/main.tf",
                    "severity": "HIGH",
                }
            ]
        },
    },
    {
        "check_type": "secrets",
        "results": {
            "failed_checks": [
                {
                    "check_id": "CKV_SECRET_6",
                    "check_name": "Base64 High Entropy String",
                    "file_path": "/voxdesk/values.yaml",
                }
            ]
        },
    },
]


def _write(tmp_path: Path, name: str, payload: Any) -> Path:
    path = tmp_path / name
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_hadolint_classifies_by_filename() -> None:
    assert classify(Path("artifacts/security/hadolint-results.json")) == ("hadolint", "jsonl")
    assert classify(Path("artifacts/security/gitleaks-results.json")) == ("gitleaks", "json")


def test_hadolint_counts_the_single_line_json_array(tmp_path: Path) -> None:
    """The shape ``hadolint -f json`` actually produces: one array, one line."""

    raw = json.dumps(_HADOLINT)
    assert "\n" not in raw and raw.startswith("[")
    parsed = parse_hadolint(_write(tmp_path, "hadolint-results.json", raw))
    assert parsed["total"] == 2
    assert parsed["counts"]["medium"] == 2
    assert [f["rule_id"] for f in parsed["findings"]] == ["DL3008", "DL4006"]


def test_hadolint_still_counts_json_lines(tmp_path: Path) -> None:
    raw = "\n".join(json.dumps(entry) for entry in _HADOLINT) + "\n"
    parsed = parse_hadolint(_write(tmp_path, "hadolint-results.jsonl", raw))
    assert parsed["total"] == 2
    assert parsed["counts"]["medium"] == 2


def test_checkov_counts_every_framework_in_the_list(tmp_path: Path) -> None:
    parsed = parse_checkov(_write(tmp_path, "checkov-results.json", _CHECKOV))
    assert parsed["total"] == 2
    assert parsed["counts"]["high"] == 2
    # The severity default matters: CKV entries without a severity field are
    # treated as high, matching how the pipeline is configured to fail.
    assert {f["path"] for f in parsed["findings"]} == {"/modules/alb/main.tf", "/voxdesk/values.yaml"}


def test_checkov_still_counts_a_single_report_object(tmp_path: Path) -> None:
    parsed = parse_checkov(_write(tmp_path, "checkov-results.json", _CHECKOV[0]))
    assert parsed["total"] == 1
    assert parsed["findings"][0]["rule_id"] == "CKV_AWS_23"


def test_semgrep_json_report_is_not_read_as_an_empty_sarif(tmp_path: Path) -> None:
    """CI ships `semgrep-results.json` beside the SARIF; the filename declares
    "sarif", so without the content sniff it went to the SARIF reader, found no
    `runs`, and reported a clean column next to 26 real findings."""

    payload = {
        "results": [
            {
                "check_id": "python.lang.security.use-defused-xml.use-defused-xml",
                "path": "app/api/multichannel_routes.py",
                "start": {"line": 15},
                "extra": {"severity": "ERROR", "message": "Use defusedxml instead of xml.etree."},
            },
            {
                "check_id": "python.django.security.injection.raw-html-format.raw-html-format",
                "path": "app/api/public_site_routes.py",
                "start": {"line": 54},
                "extra": {"severity": "WARNING", "message": "Unescaped HTML may be injected."},
            },
        ],
        "errors": [],
    }
    path = _write(tmp_path, "semgrep-results.json", payload)
    assert classify(path) == ("semgrep", "sarif")  # the trap: filename says SARIF
    row = parse_artifact(path)
    assert row["total"] == 2
    assert row["counts"]["high"] == 1 and row["counts"]["medium"] == 1
    assert row["status"] in ("ran", "failed", "clean")


def test_semgrep_sarif_is_still_parsed_as_sarif(tmp_path: Path) -> None:
    sarif = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {"driver": {"name": "semgrep", "rules": [{"id": "r1", "shortDescription": {"text": "r1"}}]}},
                "results": [{"ruleId": "r1", "level": "error", "message": {"text": "bad"}, "locations": []}],
            }
        ],
    }
    parsed = parse_artifact(_write(tmp_path, "semgrep-results.sarif", sarif))
    assert parsed["total"] == 1
    assert parsed["counts"]["high"] == 1


def test_unparseable_artifact_is_not_reported_as_clean(tmp_path: Path) -> None:
    path = _write(tmp_path, "checkov-results.json", '{"results": {"failed_checks": [')
    row = parse_artifact(path)
    assert row["status"] == "unparseable"
    assert row["total"] == 0
