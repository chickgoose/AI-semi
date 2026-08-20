"""Source-preserving UZH shapes_rotation calibration/pose join importer."""

from importlib import import_module
from typing import Any


STATUS = "PASS_SOURCE_POSE_JOIN_PACKAGE_SCOPED"
EVIDENCE_CLASS = "DATASET_SOURCE_PRESERVING_POSE_JOIN"
PROMOTION_STATUS = "HOLD_MC_WTB_ADAPTER"

__all__ = [
    "EVIDENCE_CLASS",
    "PROMOTION_STATUS",
    "STATUS",
    "JoinFailure",
    "import_join",
    "inspect",
]


def _implementation() -> Any:
    module = import_module(".import_join", __name__)
    # Import machinery installs the submodule under this package attribute.
    # Restore the callable API whose name intentionally matches the CLI module.
    globals()["import_join"] = _import_join
    globals()["inspect"] = _inspect
    return module


def _import_join(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return _implementation().import_join(*args, **kwargs)


def _inspect(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return _implementation().inspect(*args, **kwargs)


import_join = _import_join
inspect = _inspect


def __getattr__(name: str) -> Any:
    """Load the public implementation lazily so ``python -m ...import_join`` is clean."""

    if name not in __all__:
        raise AttributeError(name)
    return getattr(_implementation(), name)
