"""Execution recovery after restart."""
from app.builder.workflow_executor import WorkflowExecutor

def test_executor_recover_contract():
    ex = WorkflowExecutor()
    assert hasattr(ex, "execute")
    assert hasattr(ex, "resume")
