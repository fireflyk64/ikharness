"""End-to-end test of the Monado ikharness driver.

Feeds poses through the wire protocol and reads them back through OpenXR
(headless session): VIEW space for the HMD, grip-pose actions for the
controllers, XR_MNDX_xdev_space for the generic trackers.
"""

import math
import os

import pytest
import xr

from ikharness.protocol import Buttons, IkhClient, Inputs, Pose, PoseFlags, DeviceKind, wait_for_driver

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

        # Projection: the configured symmetric field of view, and square pixels (no squish):
        # the image aspect in tangent space must equal the recommended image aspect.
        w, h = s.recommended_view_size()
        for left, right, up, down in s.view_fovs(t):
            assert abs(math.degrees(right - left) - 100.0) < 0.5, math.degrees(right - left)
            assert abs(left + right) < 1e-4 and abs(up + down) < 1e-4
            tan_aspect = (math.tan(right) - math.tan(left)) / (math.tan(up) - math.tan(down))
            assert abs(tan_aspect - w / h) < 0.01 * (w / h), (tan_aspect, w, h)
        assert abs(w / h - 1.0) < 0.01, (w, h)

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


def test_triggers_reach_openxr(client):
    """Controller inputs sent over the wire show up as OpenXR action state (T-pose calibration needs this)."""
    client.send_inputs({"left_hand": Inputs(), "right_hand": Inputs()})
    with HeadlessSession() as s:
        s.sync_actions()
        assert s.trigger("left") < 0.01 and s.trigger("right") < 0.01
        client.send_inputs({"left_hand": Inputs(trigger=1.0)})
        s.sync_actions()
        assert s.trigger("left") > 0.99 and s.trigger("right") < 0.01
        client.send_inputs({"left_hand": Inputs(trigger=0.4), "right_hand": Inputs(trigger=1.0, buttons=Buttons.A_CLICK)})
        s.sync_actions()
        assert abs(s.trigger("left") - 0.4) < 0.01 and s.trigger("right") > 0.99
        client.send_inputs({"left_hand": Inputs(), "right_hand": Inputs()})
        s.sync_actions()
        assert s.trigger("left") < 0.01 and s.trigger("right") < 0.01


def test_second_client_reads_state(client):
    """A feeder and a reader can be connected at once; the reader sees what the feeder sent."""
    sent = {"hmd": Pose((0.2, 1.5, 0.3), quat_axis_angle((0, 1, 0), 45)), "waist": Pose((0.1, 0.9, 0.2))}
    client.send_frame(sent, frame_id=555)
    with IkhClient(port=IKH_PORT) as reader:
        frame_id, state = reader.get_state()
        assert frame_id == 555
        for name, pose in sent.items():
            assert all(abs(a - b) < 1e-6 for a, b in zip(state[name].position, pose.position)), name
            assert abs(abs(sum(a * b for a, b in zip(state[name].orientation, pose.orientation))) - 1.0) < 1e-6
        assert set(state) == set(client.device_names)
        # Both stay usable.
        assert client.ping().frame_id == 555 and reader.ping().frame_id == 555


def test_calibration_gesture_reaches_openxr(client):
    """T-pose, hold, both triggers: an OpenXR client sees the T-pose first, then both triggers rise together."""
    import threading
    import time
    from pathlib import Path

    from ikharness.calibration import run_calibration_gesture
    from ikharness.dataset import Dataset
    from ikharness.replay import tpose_device_poses
    from ikharness.trackers import TRACKER_SETS, rules_for

    dataset = Dataset.load(Path(__file__).parent / "data" / "mini_walk.json")
    roles = TRACKER_SETS["6pt"]
    poses = tpose_device_poses(dataset, roles, rules_for(dataset.skeleton))
    with HeadlessSession() as s:
        with IkhClient(port=IKH_PORT) as feeder:
            t = threading.Thread(target=run_calibration_gesture, args=(feeder, poses),
                                 kwargs=dict(hold=0.4, press=0.5, frame_id=900, log=lambda *_: None))
            t.start()
            seen_tpose_before_press = False
            pressed = False
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                s.sync_actions()
                left, right = s.trigger("left"), s.trigger("right")
                if left > 0.9 and right > 0.9:
                    pressed = True
                    # At the moment of the press the headset is where the T-pose puts it, looking along -Z.
                    loc = s.locate(s.view, s.now())
                    assert_pose_close(loc, poses["hmd"], "hmd at calibration")
                    break
                if left < 0.1 and right < 0.1 and client.ping().frame_id == 900:
                    seen_tpose_before_press = True
                time.sleep(0.02)
            t.join()
            assert pressed and seen_tpose_before_press
            s.sync_actions()
            assert s.trigger("left") < 0.01 and s.trigger("right") < 0.01
    # The T-pose head pose is upright and faces stage -Z.
    assert abs(poses["hmd"].position[1] - dataset.skeleton.eye_height) < 1e-6
    assert abs(abs(poses["hmd"].orientation[3]) - 1.0) < 1e-6


def quat_from_yaw(angle: float):
    return (0.0, math.sin(angle / 2.0), 0.0, math.cos(angle / 2.0))


def test_trackers_reach_openxr_through_htcx_roles(client):
    """XR_HTCX_vive_tracker_interaction (monado/patches/0003): the driver's trackers are bound by role.

    This is the path Godot and Unity use for body trackers; XR_MNDX_xdev_space above is Monado-only.
    """
    roles = ["waist", "left_foot", "right_foot", "chest", "left_knee", "right_elbow"]
    sent = {
        "waist": Pose((0.01, 0.95, -0.02), quat_from_yaw(0.4)),
        "left_foot": Pose((-0.12, 0.09, 0.05), quat_from_yaw(-0.2)),
        "right_foot": Pose((0.13, 0.08, -0.07), quat_from_yaw(0.1)),
        "chest": Pose((0.0, 1.31, 0.03), quat_from_yaw(1.2)),
        "left_knee": Pose((-0.11, 0.5, 0.1), quat_from_yaw(-0.9)),
        "right_elbow": Pose((0.45, 1.2, -0.1), quat_from_yaw(2.0)),
    }
    client.send_frame(sent, frame_id=950)
    with HeadlessSession(tracker_roles=roles + ["left_shoulder"]) as s:
        s.sync_actions()
        t = s.now()
        for role in roles:
            assert s.tracker_interaction_profile(role) == "/interaction_profiles/htc/vive_tracker_htcx", role
            assert_pose_close(s.locate(s.tracker_spaces[role], t), sent[role], f"htcx {role}")
        # No device plays this role: no profile, no pose.
        assert s.tracker_interaction_profile("left_shoulder") == ""
        loc = s.locate(s.tracker_spaces["left_shoulder"], t)
        assert not (int(loc.location_flags) & int(xr.SpaceLocationFlags.POSITION_VALID_BIT))
        paths = s.vive_tracker_paths()
        assert paths["/user/vive_tracker_htcx/role/waist"] == "/devices/htc/vive_tracker_htcx/ikh-trk-waist"
        assert len(paths) == 8 and all(p.startswith("/devices/htc/vive_tracker_htcx/ikh-trk-") for p in paths.values())
        # The trackers move: the pose action follows.
        client.send_frame({"waist": Pose((0.3, 0.9, 0.2))}, frame_id=951)
        s.sync_actions()
        assert_pose_close(s.locate(s.tracker_spaces["waist"], s.now()), Pose((0.3, 0.9, 0.2)), "htcx waist moved")
        # Hands are unaffected by the extra top level paths.
        assert s.current_interaction_profile("left").endswith("index_controller")
