"""Regression tests for the two `.env.check` / smoke-script defects found by
standing the check stack up for real.

Both bugs were invisible to the unit suite and only surfaced when the stack was
actually started:

1. ``scripts/make_check_env.py`` seeded every key found in ``.env.example`` with
   an *empty* string instead of the documented value, so
   ``VOICE_DEMO_RATE_LIMIT_PER_MINUTE=`` / ``VOICE_DEMO_MAX_TURNS=`` reached
   ``Settings()`` as ``""`` and the API refused to boot with
   ``Input should be a valid integer, unable to parse string as an integer``.
2. ``scripts/stack_smoke.py`` posted ``work_email`` / ``company_name`` /
   ``plan_tier`` to ``/auth/signup``, but the published contract
   (``PublicSignupRequest``) requires ``organization_name`` / ``email`` /
   ``password``, so owner login could never be created on a fresh stack (422).

These tests pin the behaviour without needing a running stack.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from scripts import make_check_env, stack_smoke

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_parse_env_defaults_keeps_documented_values(tmp_path: Path) -> None:
    template = tmp_path / ".env.example"
    template.write_text(
        "# comment\n"
        "VOICE_DEMO_RATE_LIMIT_PER_MINUTE=10\n"
        "VOICE_DEMO_MAX_TURNS=12\n"
        "DEEPGRAM_API_KEY=\n"
        "QUOTED='keep me'\n",
        encoding="utf-8",
    )

    parsed = make_check_env._parse_env_defaults(template)

    assert parsed["VOICE_DEMO_RATE_LIMIT_PER_MINUTE"] == "10"
    assert parsed["VOICE_DEMO_MAX_TURNS"] == "12"
    # Genuinely blank provider credentials must stay blank, not become a value.
    assert parsed["DEEPGRAM_API_KEY"] == ""
    # Keys are reported in file order and de-duplicated.
    assert list(parsed) == [
        "VOICE_DEMO_RATE_LIMIT_PER_MINUTE",
        "VOICE_DEMO_MAX_TURNS",
        "DEEPGRAM_API_KEY",
        "QUOTED",
    ]


def test_validate_keys_alias_matches_full_parse(tmp_path: Path) -> None:
    template = tmp_path / ".env.example"
    template.write_text("A=1\nB=two\n", encoding="utf-8")
    assert make_check_env._parse_env_keys(template) == ["A", "B"]


def test_generated_check_env_has_no_empty_int_settings() -> None:
    """A generated `.env.check` must never ship a typed setting as ``""``."""
    values = make_check_env.generate_check_env(
        REPO_ROOT / ".env.check.test-only",
        seed_companion_env_files=False,
    )
    try:
        for key in (
            "VOICE_DEMO_RATE_LIMIT_PER_MINUTE",
            "VOICE_DEMO_MAX_TURNS",
            "VOICE_DEMO_TTL_SECONDS",
        ):
            assert values[key].strip(), f"{key} was seeded empty"
            assert values[key].strip().isdigit(), f"{key} is not an integer: {values[key]!r}"
        # Secrets stay populated so the stack is actually bootable.
        assert len(values["JWT_SECRET"]) >= 16
        assert len(values["REALTIME_GATEWAY_INGEST_SECRET"]) >= 16
    finally:
        (REPO_ROOT / ".env.check.test-only").unlink(missing_ok=True)


def test_stack_smoke_signup_payload_matches_public_contract() -> None:
    """The smoke signup body must use the API's own field names.

    Parses the AST so explanatory comments mentioning the retired field names do
    not trip the assertion — only the literal payload sent to ``/auth/signup``.
    """
    import ast

    tree = ast.parse(inspect.getsource(stack_smoke._ensure_owner_session))

    payload_keys: set[str] = set()
    signup_found = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "post"):
            continue
        if not node.args or not isinstance(node.args[0], ast.Constant):
            continue
        if node.args[0].value != "/auth/signup":
            continue
        signup_found = True
        for kw in node.keywords:
            if kw.arg == "json" and isinstance(kw.value, ast.Dict):
                for key in kw.value.keys:
                    assert isinstance(key, ast.Constant)
                    payload_keys.add(key.value)

    assert signup_found, "the smoke runner no longer posts to /auth/signup"
    assert payload_keys == {"full_name", "email", "organization_name", "password"}


def test_stack_smoke_contract_fields_exist_in_openapi() -> None:
    """Guard against the contract drifting away from what the smoke posts."""
    import json

    spec = json.loads((REPO_ROOT / "contracts" / "openapi.json").read_text(encoding="utf-8"))
    schema_ref = spec["paths"]["/auth/signup"]["post"]["requestBody"]["content"][
        "application/json"
    ]["schema"]["$ref"]
    schema_name = schema_ref.rsplit("/", 1)[-1]
    schema = spec["components"]["schemas"][schema_name]

    required = set(schema["required"])
    assert {"organization_name", "email", "password"} <= required
    assert "work_email" not in schema["properties"]
    assert "company_name" not in schema["properties"]


@pytest.mark.parametrize("field", ["organization_name", "email", "password"])
def test_required_signup_fields_are_documented(field: str) -> None:
    import json

    spec = json.loads((REPO_ROOT / "contracts" / "openapi.json").read_text(encoding="utf-8"))
    schema = spec["components"]["schemas"]["PublicSignupRequest"]
    assert field in schema["properties"]
