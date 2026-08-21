"""One-shot post-seal runner for the frozen Stage-4 comparison."""

from .runner import OfficialScoreResult, ScoreRunnerError, run_official_score

__all__ = (
    "OfficialScoreResult",
    "ScoreRunnerError",
    "run_official_score",
)
