"""Source-bound six-arm companion records for the UZH MC-WTB study."""

from .generator import (
    ARM_NAMES,
    GeneratorFailure,
    IMPLEMENTATION_STATUS,
    PRODUCTION_STATUS,
    PROMOTION_STATUS,
    SYNTHETIC_STATUS,
    generate,
    inspect,
)

__all__ = [
    "ARM_NAMES",
    "GeneratorFailure",
    "IMPLEMENTATION_STATUS",
    "PRODUCTION_STATUS",
    "PROMOTION_STATUS",
    "SYNTHETIC_STATUS",
    "generate",
    "inspect",
]
