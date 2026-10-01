"""IK harness: tools for objectively evaluating XR avatar inverse kinematics.

Subpackages / modules:

* :mod:`ikharness.protocol` - client for the Monado ``ikharness`` driver wire protocol.
* :mod:`ikharness.dataset` - reference pose datasets (skeleton + sampled global bone poses).
* :mod:`ikharness.trackers` - virtual tracker placement and tracker sets.
* :mod:`ikharness.testfile` - harness input (tracker) and output (result) files.
* :mod:`ikharness.scoring` - per-bone angular error and body scores.
* :mod:`ikharness.build_dataset`, :mod:`ikharness.run_godot`, :mod:`ikharness.replay` - command line tools.
* :mod:`ikharness.openxr_session`, :mod:`ikharness.xdev_space` - headless OpenXR probing.
"""

from .protocol import (  # noqa: F401
    DEFAULT_PORT,
    Buttons,
    DeviceDesc,
    DeviceKind,
    IkhClient,
    Inputs,
    Pose,
    PoseFlags,
)

__all__ = [
    "Buttons",
    "DEFAULT_PORT",
    "DeviceDesc",
    "DeviceKind",
    "IkhClient",
    "Inputs",
    "Pose",
    "PoseFlags",
]
