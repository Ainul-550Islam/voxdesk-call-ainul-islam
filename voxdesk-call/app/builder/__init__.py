"""Visual flow builder: node contracts, durable workflow storage and execution.

``node`` defines the node vocabulary a drag-and-drop editor would produce;
``workflow_schemas`` / ``workflow_store`` are the persistence contract;
``workflow_repository`` is the only durable boundary; ``workflow_executor``
coordinates resume/restart over the shared job queue.

The package is import-light on purpose: the executor resolves the repository
and session lazily so a route module can import a schema without opening a
database connection.
"""

__all__ = [
    "node",
    "workflow_executor",
    "workflow_repository",
    "workflow_schemas",
    "workflow_state",
    "workflow_store",
]
