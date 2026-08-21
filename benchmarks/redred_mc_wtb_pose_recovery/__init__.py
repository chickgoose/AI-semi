"""Pure-Python Stage-4 pose recovery geometry."""

from .geometry import (
    BracketInterpolation,
    GeometryError,
    PoseSample,
    RecoveryDecision,
    RecoveryMode,
    extrapolate_constant_angular_velocity,
    interpolate_committed_bracket,
    normalize_quaternion_xyzw,
    recover_causal_cav,
    resample_oracle_groundtruth_1khz,
    resample_counterfactual_1khz,
    rotate_sensor_ray_to_world,
    shortest_arc_slerp_xyzw,
)

__all__ = (
    "BracketInterpolation",
    "GeometryError",
    "PoseSample",
    "RecoveryDecision",
    "RecoveryMode",
    "extrapolate_constant_angular_velocity",
    "interpolate_committed_bracket",
    "normalize_quaternion_xyzw",
    "recover_causal_cav",
    "resample_oracle_groundtruth_1khz",
    "resample_counterfactual_1khz",
    "rotate_sensor_ray_to_world",
    "shortest_arc_slerp_xyzw",
)
