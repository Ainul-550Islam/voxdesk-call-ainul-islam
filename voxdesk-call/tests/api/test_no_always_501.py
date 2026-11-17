"""No mounted route may be a permanent "not implemented".

PART 17B (P17B-03). The spec asked for this because a route that can only ever
answer 501 is worse than a route that does not exist: an OpenAPI client
generates a method for it, a dashboard renders a button for it, and every user
who clicks it sees "not implemented" as though the feature were merely broken.
The honest alternatives are (a) implement it, or (b) delete it.

Two distinct things are checked, and the difference matters:

1. **Statically**: a route handler whose entire body is a raise — no return, no
   branch, no call that could succeed — can never answer anything else. Those
   are dead and must not be mounted. This walks the *live* `app.main:app` route
   table, not a list in the test.

2. **The explicit capability allowlist**: a handler may raise
   `NOT_CONFIGURED` / `UNSUPPORTED_CAPABILITY` **after** doing real work, when a
   specific dependency is missing (Redis, a provider key, KMS). That is a
   declared capability, not a stub: the same route returns data the moment the
   deployment is configured. Those are allowed, named, and each one is required
   to carry a machine-readable `code` so a client can act on it.

The second list is deliberately explicit rather than pattern-matched: adding a
new 501 capability should require editing this file, because that edit is the
moment someone decides the capability is legitimate.
"""
from __future__ import annotations

import ast
import inspect
import textwrap

import pytest

from app.main import app

# ---------------------------------------------------------------------------
# 1. Static: a handler whose body is nothing but a raise
# ---------------------------------------------------------------------------


def _only_statement_is_raise(endpoint) -> tuple[bool, str]:
    try:
        source = textwrap.dedent(inspect.getsource(endpoint))
    except (OSError, TypeError):
        # C-builtins and dynamically created endpoints have no source; they are
        # not hand-written stubs, so there is nothing to accuse them of.
        return False, ""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False, ""
    functions = [
        node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    if not functions:
        return False, ""
    body = functions[0].body
    # Drop the docstring.
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]
    if len(body) != 1:
        return False, ""
    statement = body[0]
    if not isinstance(statement, ast.Raise):
        return False, ""
    # What is it raising? A 409 states a permanent business rule ("this is
    # never allowed"), which is a correct answer and not a dead endpoint. A 4xx
    # other than 409 would be odd but is still an answer. Only a route whose
    # single statement cannot answer anything except a raise is dead — and the
    # 409 case is excluded deliberately, with the reason written down here so
    # the exclusion is a decision rather than an oversight.
    detail = ast.unparse(statement.exc) if statement.exc is not None else "raise"
    if _is_permanent_refusal(detail):
        return False, ""
    return True, detail


def _is_permanent_refusal(detail: str) -> bool:
    """True when the raise is a 409 — a rule, not a missing feature."""
    return detail.startswith("HTTPException(409") or "CONFLICT" in detail


def _mounted_routes():
    for route in app.routes:
        endpoint = getattr(route, "endpoint", None)
        if endpoint is None:
            continue
        path = getattr(route, "path", None)
        if path is None:
            continue
        yield route, endpoint, path


def test_no_mounted_route_is_a_permanent_raise() -> None:
    offenders = []
    for _route, endpoint, path in _mounted_routes():
        dead, detail = _only_statement_is_raise(endpoint)
        if dead:
            offenders.append(
                f"{path} ({getattr(endpoint, '__module__', '?')}.{endpoint.__name__}) -> {detail}"
            )
    assert not offenders, (
        "these mounted routes can only ever raise, so they are dead endpoints a "
        "client will still generate a method for:\n  " + "\n  ".join(offenders)
    )


# ---------------------------------------------------------------------------
# 2. Every 501 that remains is a declared, machine-readable capability
# ---------------------------------------------------------------------------

#: Route paths allowed to answer 501 *when a dependency is missing*. Values say
#: which capability. A path that appears here and stops raising 501 is fine —
#: the entries are guidance, not a contract — but a NEW 501 outside this list
#: fails the test below, which forces the decision to be made deliberately.
#: Every entry is a route that *works* once its dependency exists: the handler
#: validates, loads, and mutates first, and only the missing dependency turns
#: into a 501. Each value names what must be present. Anything not listed here
#: fails `test_every_route_that_can_answer_501_is_a_declared_capability`, which
#: is the point: a new 501 must be a deliberate decision.
DECLARED_501_CAPABILITIES: dict[str, str] = {
    # --- Redis-backed admission control ------------------------------------
    # The limiter is the gate; without Redis the route cannot safely proceed,
    # and silently skipping the limit would remove a safety property.
    "/api/analysis/backfill": "redis_rate_limit",
    "/api/post-call-analysis/backfill": "redis_rate_limit",
    "/api/qa/reviews": "redis_rate_limit",
    # --- A provider or transport that is not configured ---------------------
    "/api/integrations/crm/{provider}": "redis",
    # --- A capability the deployed SIP gateway does not implement -----------
    # 501 here is literal: the gateway answered "unsupported", and pointing the
    # deployment at a gateway that supports pcap turns the same call green.
    # --- A telephony provider that cannot do what the caller asked ----------
    # `UnsupportedCapability` is a typed provider error carrying `capability`
    # in its body; both routes do the full takeover and only the provider's
    # refusal becomes a 501.
    "/api/calls/{call_id}/monitor": "call_monitoring",
    "/api/calls/{call_id}/takeover": "supervisor_takeover",
}


def _raises_501(endpoint) -> list[str]:
    """Every literal `501`/`HTTP_501` this handler could raise."""
    found: list[str] = []
    try:
        source = textwrap.dedent(inspect.getsource(endpoint))
    except (OSError, TypeError):
        return found
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return found
    for node in ast.walk(tree):
        # HTTPException(501, ...) or HTTPException(status_code=501, ...)
        if isinstance(node, ast.Call):
            for arg in node.args:
                if isinstance(arg, ast.Constant) and arg.value == 501:
                    found.append("501")
            for keyword in node.keywords:
                if keyword.arg == "status_code" and isinstance(keyword.value, ast.Constant) \
                        and keyword.value.value == 501:
                    found.append("501")
        # status.HTTP_501_NOT_IMPLEMENTED
        if isinstance(node, ast.Attribute) and node.attr == "HTTP_501_NOT_IMPLEMENTED":
            found.append("501")
    return found


def test_every_route_that_can_answer_501_is_a_declared_capability() -> None:
    undeclared = []
    for _route, endpoint, path in _mounted_routes():
        if not _raises_501(endpoint):
            continue
        if path in DECLARED_501_CAPABILITIES:
            continue
        undeclared.append(f"{path} ({getattr(endpoint, '__module__', '?')}.{endpoint.__name__})")
    assert not undeclared, (
        "these routes can answer 501 but are not listed in "
        "DECLARED_501_CAPABILITIES, so nobody has decided whether the "
        "capability is legitimate or the endpoint is a stub:\n  "
        + "\n  ".join(undeclared)
    )


def test_a_501_capability_names_itself_in_the_body() -> None:
    """A client cannot act on a 501 that does not say what is missing."""
    silent = []
    for _route, endpoint, path in _mounted_routes():
        if not _raises_501(endpoint):
            continue
        if path not in DECLARED_501_CAPABILITIES:
            continue
        source = inspect.getsource(endpoint)
        capability = DECLARED_501_CAPABILITIES[path]
        # The declared capability string (or a `capability` key) must appear in
        # the handler, so the response body explains itself.
        self_describing = (
            capability in source
            or "capability" in source          # {"capability": ...}
            or '"code"' in source              # {"code": "NOT_CONFIGURED", ...}
            or "as_dict()" in source           # typed provider error -> body
        )
        if not self_describing:
            silent.append(f"{path} claims {capability!r} but the handler never names it")
    assert not silent, "\n".join(silent)


# ---------------------------------------------------------------------------
# 3. The specific stubs the spec named
# ---------------------------------------------------------------------------


def test_outbound_call_routes_has_no_not_implemented_handlers() -> None:
    """The spec's P17B-03 names this module specifically."""
    import app.api.outbound_call_routes as module

    source = inspect.getsource(module)
    for marker in ("_not_implemented", "NotImplementedError", "HTTP_501_NOT_IMPLEMENTED"):
        assert marker not in source, f"app/api/outbound_call_routes.py still contains {marker!r}"

    # And the handlers the spec called out are real: each one must reference a
    # service call or a persistent mutation, not merely raise.
    tree = ast.parse(source)
    wanted = {"pause_call", "resume_call", "reopen_call", "send_dtmf_batch"}
    seen = set()
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name not in wanted:
            continue
        seen.add(node.name)
        has_awaits = any(isinstance(n, ast.Await) for n in ast.walk(node))
        assert has_awaits, f"{node.name} does no awaited work; it cannot be a real handler"
    assert seen == wanted, f"missing handlers: {sorted(wanted - seen)}"


def test_call_analytics_reports_what_is_stored_rather_than_nulls() -> None:
    """The stub that survived after the 501s were gone.

    `/api/calls/{id}/analytics` computed a duration and returned four nulls for
    fields that were already in the database. A null that means "we do not
    implement this" is indistinguishable from a null that means "this call has
    none", so the contract must say which.
    """
    import app.api.outbound_call_routes as module

    source = textwrap.dedent(inspect.getsource(module.get_call_analytics))
    # The old docstring quotes the four nulls it is warning about, so scanning
    # the raw text would match the warning. Scan only the *code* — the returned
    # literal that built the response.
    tree = ast.parse(source)
    literals: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Return) and isinstance(node.value, (ast.Dict, ast.Call)):
            literals.append(ast.unparse(node.value))
    returned = "\n".join(literals)
    assert returned, "get_call_analytics returns nothing recognisable"
    compact = returned.replace(" ", "")
    # Three of the four were pure stubs: the data was already stored.
    for marker in ("provider=None", "recording_url=None", "transcript_excerpt=None"):
        assert marker not in compact, f"the analytics handler still hard-codes {marker}"
    # `cost_cents=None` is allowed — and only while it carries an explanation.
    # A bare null is the defect; an explained null is the honest contract.
    if "cost_cents=None" in compact:
        assert "cost_basis=" in compact, "cost_cents is null with no explanation"
        assert "cost_basis=None" not in compact, "cost_basis is null too; nothing is explained"
    # It must read the stored recording URL and the stored turns.
    assert "call.recording_url" in source
    assert "Turn" in source, "the excerpt is not read from the turns table"
    # And it must say why cost is absent rather than leaving a bare null.
    assert "cost_basis" in source

    model = module.CallAnalyticsOut.model_json_schema()["properties"]
    assert "cost_basis" in model, "cost_cents can be null with no explanation"
    assert "turn_count" in model, "an excerpt with no total is ambiguous"
    assert "transcript_truncated" in model


@pytest.mark.parametrize(
    "call_sid,expected",
    [
        ("CA" + "a" * 32, "twilio"),
        ("ca" + "b" * 32, "twilio"),
        ("PL" + "c" * 32, "unknown"),
        ("something-else", "unknown"),
        (None, None),
        ("", None),
    ],
)
def test_provider_is_named_only_when_the_sid_proves_it(call_sid, expected) -> None:
    import app.api.outbound_call_routes as module

    assert module._provider_from_call_sid(call_sid) == expected
