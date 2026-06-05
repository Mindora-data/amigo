from __future__ import annotations

from typing import Any

__all__ = [
    "InMemoryStateStore",
    "NinoRuntime",
    "NinoService",
    "InternalLoop",
    "NinoScheduler",
    "BackgroundAutonomy",
    "create_app",
    "create_persistent_runtime",
]


def __getattr__(name: str) -> Any:
    if name in {"InMemoryStateStore", "NinoRuntime"}:
        from .runtime import InMemoryStateStore, NinoRuntime

        return {"InMemoryStateStore": InMemoryStateStore, "NinoRuntime": NinoRuntime}[name]
    if name in {"NinoService", "create_app"}:
        from .api import NinoService, create_app

        return {"NinoService": NinoService, "create_app": create_app}[name]
    if name == "InternalLoop":
        from .internal_loop import InternalLoop

        return InternalLoop
    if name == "NinoScheduler":
        from .scheduler import NinoScheduler

        return NinoScheduler
    if name == "BackgroundAutonomy":
        from .autonomy import BackgroundAutonomy

        return BackgroundAutonomy
    if name == "create_persistent_runtime":
        from .persistence import create_persistent_runtime

        return create_persistent_runtime
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
