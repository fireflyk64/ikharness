"""End-to-end test of the Monado ikharness driver.

Feeds poses through the wire protocol and reads them back through OpenXR
(headless session): VIEW space for the HMD, grip-pose actions for the
controllers, XR_MNDX_xdev_space for the generic trackers.
"""

import math
import os

import pytest

from ikharness.protocol import IkhClient, Pose, PoseFlags, DeviceKind, wait_for_driver

xr = pytest.importorskip("xr")

from ikharness.openxr_session import (  # noqa: E402
    ALL_VALID_AND_TRACKED,
    HeadlessSession,
    pose_to_tuple,
)
from ikharness.xdev_space import EXTENSION_NAME as XDEV_EXT, XDevList  # noqa: E402

IKH_PORT = int(os.environ.get("IKH_PORT", "4343"))
TOL = 1e-4


def quat_axis_angle(axis, deg):
    x, y, z = axis
    n = math.sqrt(x * x + y * y + z * z)
    s = math.sin(math.radians(deg) / 2) / n
    return (x * s, y * s, z * s, math.cos(math.radians(deg) / 2))


def assert_pose_close(location, pose: Pose, what: str):
    got_pos, got_rot = pose_to_tuple(location.pose)
    assert location.location_flags & ALL_VALID_AND_TRACKED == ALL_VALID_AND_TRACKED, f"{what}: flags {location.location_flags:#x}"
    for a, b in zip(got_pos, pose.position):
        assert abs(a - b) < TOL, f"{what}: position {got_pos} != {pose.position}"
    # q and -q are the same rotation.
    dot = sum(a * b for a, b in zip(got_rot, pose.orientation))
    assert abs(abs(dot) - 1.0) < 1e-4, f"{what}: orientation {got_rot} != {pose.orientation}"


@pytest.fixture(scope="module")
def client(monado_service):
    with wait_for_driver(port=IKH_PORT, timeout=30) as c:
        yield c


def test_hello_lists_expected_devices(client):
    names = client.device_names
    assert names[0] == "hmd"
    assert "left_hand" in names and "right_hand" in names
    for role in ("waist", "chest", "left_foot", "right_foot"):
        assert role in names, names
    kinds = {d.name: d.kind for d in client.devices}
    assert kinds["waist"] == DeviceKind.TRACKER
    assert client.device("waist").serial == "IKH-TRK-waist"


def test_ack_and_ping(client):
    ack = client.send_frame({"hmd": Pose((0, 1.6, 0))}, frame_id=7)
    assert ack.frame_id == 7
    assert ack.applied_at_ns > 0
    pong = client.ping()
    assert pong.frame_id == 7


def test_poses_round_trip_through_openxr(client):
    frame1 = {
        "hmd": Pose((0.10, 1.70, -0.20), quat_axis_angle((0, 1, 0), 30)),
        "left_hand": Pose((-0.30, 1.20, -0.40), quat_axis_angle((1, 0, 0), -45)),
        "right_hand": Pose((0.35, 1.15, -0.45), quat_axis_angle((0, 0, 1), 20)),
        "waist": Pose((0.02, 0.97, -0.05), quat_axis_angle((0, 1, 0), 10)),
        "chest": Pose((0.03, 1.32, -0.08), quat_axis_angle((1, 0, 0), 5)),
        "left_foot": Pose((-0.12, 0.09, 0.02), quat_axis_angle((0, 1, 0), -15)),
        "right_foot": Pose((0.14, 0.08, -0.03), quat_axis_angle((0, 1, 0), 25)),
    }
    client.send_frame(frame1, frame_id=100)

    with HeadlessSession(extra_extensions=[XDEV_EXT]) as s:
        t = s.now()
        s.sync_actions()

        # HMD through the VIEW reference space.
        assert_pose_close(s.locate(s.view, t), frame1["hmd"], "hmd/view")

        # Stereo views must straddle the head pose by the configured IPD.
        _, views = s.locate_views(t)
        assert len(views) == 2
        l, r = views[0].pose.position, views[1].pose.position
        ipd = math.dist((l.x, l.y, l.z), (r.x, r.y, r.z))
        assert abs(ipd - 0.063) < 1e-3, ipd

        # Controllers through grip pose actions.
        profile = s.current_interaction_profile("left")
        assert profile.endswith("index_controller"), profile
        assert_pose_close(s.locate(s.grip_spaces["left"], t), frame1["left_hand"], "left grip")
        assert_pose_close(s.locate(s.grip_spaces["right"], t), frame1["right_hand"], "right grip")

        # Trackers through xdev spaces, matched by serial.
        with XDevList(s.session) as xdl:
            spaces = {}
            for role in ("waist", "chest", "left_foot", "right_foot"):
                dev = xdl.find_by_serial(client.device(role).serial)
                assert dev.can_create_space, dev
                spaces[role] = xdl.create_space(dev)
            for role, space in spaces.items():
                assert_pose_close(s.locate(space, t), frame1[role], role)

            # A second frame must replace the poses; unlisted devices keep theirs.
            frame2 = {
                "hmd": Pose((-0.20, 1.55, 0.10), quat_axis_angle((0, 1, 0), -60)),
                "waist": Pose((-0.05, 0.90, 0.10), quat_axis_angle((0, 0, 1), 8)),
            }
            client.send_frame(frame2, frame_id=101)
            t = s.now()
            s.sync_actions()
            assert_pose_close(s.locate(s.view, t), frame2["hmd"], "hmd/view frame2")
            assert_pose_close(s.locate(spaces["waist"], t), frame2["waist"], "waist frame2")
            assert_pose_close(s.locate(spaces["chest"], t), frame1["chest"], "chest kept")
            assert_pose_close(s.locate(s.grip_spaces["left"], t), frame1["left_hand"], "left kept")

            # Dropping a tracker clears its location flags.
            client.send_frame({"waist": Pose.disconnected()}, frame_id=102)
            t = s.now()
            loc = s.locate(spaces["waist"], t)
            assert loc.location_flags & ALL_VALID_AND_TRACKED == 0, f"{loc.location_flags:#x}"

            # And it comes back.
            client.send_frame({"waist": frame1["waist"]}, frame_id=103)
            t = s.now()
            assert_pose_close(s.locate(spaces["waist"], t), frame1["waist"], "waist back")
            for sp in spaces.values():
                xr.destroy_space(sp)

    # Restore a sane resting pose for anything that runs after us.
    client.send_frame({"hmd": Pose((0, 1.6, 0)), "waist": Pose((0, 0.95, 0))}, frame_id=104)
