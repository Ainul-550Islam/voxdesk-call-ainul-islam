"""Batch 07: retry classification, backoff, jitter, exhaustion."""
from app.jobs.retry import backoff_seconds, classify_exception, next_attempt_at, decide_retry, should_retry
from app.jobs.types import FailureClass

class FakeExc(Exception): pass

def test_backoff_grows():
    assert backoff_seconds(1) == 30
    assert backoff_seconds(3) <= 120  # capped at 3600; 30*2**2=120

def test_jitter_bounded():
    v = backoff_seconds(2, key="x")
    assert 60 * 0.8 <= v <= 60

def test_classify_permanent():
    fc, cat = classify_exception(ValueError("bad"))
    assert fc == FailureClass.PERMANENT

def test_decide_exhausted():
    d = decide_retry(attempt_count=5, max_attempts=5, failure_class=FailureClass.TRANSIENT, category="t")
    assert d.exhausted and not d.retry
