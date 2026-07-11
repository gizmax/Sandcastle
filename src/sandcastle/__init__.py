"""Sandcastle - Production-ready workflow orchestrator for AI agents."""

__version__ = "0.40.2"

__all__ = ["SandcastleClient", "AsyncSandcastleClient", "__version__"]


def __getattr__(name: str):
    """Lazily expose the SDK clients without importing httpx at package import.

    Importing ``sandcastle`` (or any submodule, e.g. ``sandcastle.engine``)
    used to eagerly run ``from sandcastle.sdk import ...``, which pulls in
    httpx. Lightweight tooling that only needs a submodule - such as the hub
    template validator importing ``sandcastle.engine.hub_scanner`` - then
    failed with ``ModuleNotFoundError: No module named 'httpx'`` in minimal
    environments that do not install the HTTP client stack.

    PEP 562 module-level ``__getattr__`` keeps the public API intact
    (``from sandcastle import SandcastleClient`` and
    ``import sandcastle; sandcastle.SandcastleClient`` both work) while
    deferring the httpx import until a client is actually requested.
    """
    if name in ("SandcastleClient", "AsyncSandcastleClient"):
        from sandcastle import sdk

        return getattr(sdk, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
