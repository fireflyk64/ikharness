"""A minimal headless OpenXR session for probing poses out of a runtime.

Uses ``XR_MND_headless`` so no graphics API, window or GPU output is needed.
Meant for tests and tooling that only want to read spaces and actions.

Headless sessions have no frame loop (Monado rejects ``xrBeginFrame`` and goes
straight to FOCUSED after ``xrBeginSession``), so times for ``xrLocateSpace``
come from ``XR_KHR_convert_timespec_time`` via :meth:`HeadlessSession.now`.
"""

from __future__ import annotations

import ctypes as C
import time
from typing import Dict, List, Optional, Sequence

import xr

# OpenXR space location flag bits (XrSpaceLocationFlags).
ORIENTATION_VALID = 0x1
POSITION_VALID = 0x2
ORIENTATION_TRACKED = 0x4
POSITION_TRACKED = 0x8
ALL_VALID_AND_TRACKED = ORIENTATION_VALID | POSITION_VALID | ORIENTATION_TRACKED | POSITION_TRACKED

TIMESPEC_EXT = "XR_KHR_convert_timespec_time"


class _timespec(C.Structure):
    _fields_ = [("tv_sec", C.c_long), ("tv_nsec", C.c_long)]


_PFN_ConvertTimespec = C.CFUNCTYPE(C.c_int, xr.Instance, C.POINTER(_timespec), C.POINTER(C.c_int64))

HTCX_EXT = "XR_HTCX_vive_tracker_interaction"
HTCX_PROFILE = "/interaction_profiles/htc/vive_tracker_htcx"
HTCX_ROLE_PREFIX = "/user/vive_tracker_htcx/role/"
_TYPE_VIVE_TRACKER_PATHS_HTCX = 1000103000


class _ViveTrackerPaths(C.Structure):
    _fields_ = [("type", C.c_int32), ("next", C.c_void_p), ("persistent_path", C.c_uint64), ("role_path", C.c_uint64)]


_PFN_EnumerateViveTrackerPaths = C.CFUNCTYPE(C.c_int32, xr.Instance, C.c_uint32, C.POINTER(C.c_uint32), C.POINTER(_ViveTrackerPaths))


def identity_pose() -> xr.Posef:
    return xr.Posef(orientation=xr.Quaternionf(0, 0, 0, 1), position=xr.Vector3f(0, 0, 0))


def pose_to_tuple(p: xr.Posef):
    return (
        (p.position.x, p.position.y, p.position.z),
        (p.orientation.x, p.orientation.y, p.orientation.z, p.orientation.w),
    )


class HeadlessSession:
    """Instance + system + headless session + STAGE/VIEW spaces + pose actions.

    ``hand_profiles`` lists the interaction profiles to suggest grip-pose
    bindings for; the runtime picks whichever matches its controllers.
    """

    def __init__(
        self,
        extra_extensions: Sequence[str] = (),
        hand_profiles: Sequence[str] = (
            "/interaction_profiles/valve/index_controller",
            "/interaction_profiles/khr/simple_controller",
        ),
        app_name: str = "ikharness",
        tracker_roles: Sequence[str] = (),
    ):
        # tracker_roles: roles of XR_HTCX_vive_tracker_interaction ("waist", "left_foot", ...)
        # to bind a pose action to; the extension is enabled automatically.
        self.tracker_roles = list(tracker_roles)
        if self.tracker_roles and HTCX_EXT not in extra_extensions:
            extra_extensions = list(extra_extensions) + [HTCX_EXT]
        self.tracker_action = None
        self.tracker_paths: Dict[str, xr.Path] = {}
        self.tracker_spaces: Dict[str, xr.Space] = {}
        self.extra_extensions = list(extra_extensions)
        self.hand_profiles = list(hand_profiles)
        self.app_name = app_name
        self.instance: Optional[xr.Instance] = None
        self.session: Optional[xr.Session] = None
        self.system_id = None
        self.state = xr.SessionState.UNKNOWN
        self.stage: Optional[xr.Space] = None
        self.local: Optional[xr.Space] = None
        self.view: Optional[xr.Space] = None
        self.action_set = None
        self.grip_action = None
        self.trigger_action = None
        self.hand_paths: Dict[str, xr.Path] = {}
        self.grip_spaces: Dict[str, xr.Space] = {}
        self._active_sets = None
        self.view_configuration_type = xr.ViewConfigurationType.PRIMARY_STEREO

    # -- lifecycle -----------------------------------------------------------

    def __enter__(self) -> "HeadlessSession":
        exts = [xr.MND_HEADLESS_EXTENSION_NAME, TIMESPEC_EXT] + self.extra_extensions
        self.instance = xr.create_instance(
            xr.InstanceCreateInfo(
                application_info=xr.ApplicationInfo(
                    application_name=self.app_name, application_version=1, engine_name="ikharness",
                    engine_version=1, api_version=xr.XR_CURRENT_API_VERSION,
                ),
                enabled_extension_names=exts,
            )
        )
        self._convert_timespec = C.cast(
            xr.get_instance_proc_addr(self.instance, "xrConvertTimespecTimeToTimeKHR"), _PFN_ConvertTimespec
        )
        self.system_id = xr.get_system(
            self.instance, xr.SystemGetInfo(form_factor=xr.FormFactor.HEAD_MOUNTED_DISPLAY)
        )
        self._setup_actions()
        self.session = xr.create_session(self.instance, xr.SessionCreateInfo(system_id=self.system_id))
        self.stage = xr.create_reference_space(
            self.session,
            xr.ReferenceSpaceCreateInfo(reference_space_type=xr.ReferenceSpaceType.STAGE, pose_in_reference_space=identity_pose()),
        )
        self.local = xr.create_reference_space(
            self.session,
            xr.ReferenceSpaceCreateInfo(reference_space_type=xr.ReferenceSpaceType.LOCAL, pose_in_reference_space=identity_pose()),
        )
        self.view = xr.create_reference_space(
            self.session,
            xr.ReferenceSpaceCreateInfo(reference_space_type=xr.ReferenceSpaceType.VIEW, pose_in_reference_space=identity_pose()),
        )
        self._attach_actions()
        self.wait_for_state(xr.SessionState.READY)
        xr.begin_session(self.session, xr.SessionBeginInfo(primary_view_configuration_type=self.view_configuration_type))
        self.wait_for_state(xr.SessionState.FOCUSED)
        return self

    def __exit__(self, *exc) -> None:
        try:
            if self.session is not None and self.state not in (xr.SessionState.IDLE, xr.SessionState.EXITING, xr.SessionState.UNKNOWN):
                try:
                    xr.request_exit_session(self.session)
                    self.wait_for_state(xr.SessionState.STOPPING, timeout=2.0)
                    xr.end_session(self.session)
                except Exception:
                    pass
        finally:
            for s in list(self.grip_spaces.values()) + list(self.tracker_spaces.values()) + [self.stage, self.local, self.view]:
                if s is not None:
                    try:
                        xr.destroy_space(s)
                    except Exception:
                        pass
            if self.session is not None:
                xr.destroy_session(self.session)
                self.session = None
            if self.instance is not None:
                xr.destroy_instance(self.instance)
                self.instance = None

    # -- events ----------------------------------------------------------------

    def poll_events(self) -> None:
        while True:
            try:
                event = xr.poll_event(self.instance)
            except xr.EventUnavailable:
                return
            if event.type == xr.StructureType.EVENT_DATA_SESSION_STATE_CHANGED:
                changed = C.cast(C.byref(event), C.POINTER(xr.EventDataSessionStateChanged)).contents
                self.state = xr.SessionState(changed.state)

    def wait_for_state(self, state: xr.SessionState, timeout: float = 10.0) -> None:
        deadline = time.monotonic() + timeout
        while self.state != state:
            self.poll_events()
            if self.state == state:
                break
            if time.monotonic() > deadline:
                raise TimeoutError(f"session did not reach {state.name} (now {self.state.name})")
            time.sleep(0.01)

    # -- time -------------------------------------------------------------------

    def now(self) -> int:
        """The current time as an XrTime, for xrLocateSpace / xrLocateViews."""
        ns = time.clock_gettime_ns(time.CLOCK_MONOTONIC)
        ts = _timespec(tv_sec=ns // 1_000_000_000, tv_nsec=ns % 1_000_000_000)
        out = C.c_int64(0)
        exc = xr.check_result(xr.Result(self._convert_timespec(self.instance, C.byref(ts), C.byref(out))), "xrConvertTimespecTimeToTimeKHR")
        if exc.is_exception():
            raise exc
        return out.value

    # -- actions ---------------------------------------------------------------

    def _setup_actions(self) -> None:
        self.action_set = xr.create_action_set(
            self.instance, xr.ActionSetCreateInfo(action_set_name="ikharness", localized_action_set_name="IK Harness", priority=0)
        )
        for hand in ("left", "right"):
            self.hand_paths[hand] = xr.string_to_path(self.instance, f"/user/hand/{hand}")
        subaction_paths = (xr.Path * 2)(self.hand_paths["left"], self.hand_paths["right"])
        self.grip_action = xr.create_action(
            self.action_set,
            xr.ActionCreateInfo(
                action_type=xr.ActionType.POSE_INPUT,
                action_name="grip_pose",
                localized_action_name="Grip Pose",
                count_subaction_paths=2,
                subaction_paths=subaction_paths,
            ),
        )
        self.trigger_action = xr.create_action(
            self.action_set,
            xr.ActionCreateInfo(
                action_type=xr.ActionType.FLOAT_INPUT,
                action_name="trigger",
                localized_action_name="Trigger",
                count_subaction_paths=2,
                subaction_paths=subaction_paths,
            ),
        )
        for profile in self.hand_profiles:
            # Index has an analog trigger, the simple controller only a select click.
            trigger_path = "input/select/click" if profile.endswith("simple_controller") else "input/trigger/value"
            bindings = (xr.ActionSuggestedBinding * 4)(
                xr.ActionSuggestedBinding(action=self.grip_action, binding=xr.string_to_path(self.instance, "/user/hand/left/input/grip/pose")),
                xr.ActionSuggestedBinding(action=self.grip_action, binding=xr.string_to_path(self.instance, "/user/hand/right/input/grip/pose")),
                xr.ActionSuggestedBinding(action=self.trigger_action, binding=xr.string_to_path(self.instance, f"/user/hand/left/{trigger_path}")),
                xr.ActionSuggestedBinding(action=self.trigger_action, binding=xr.string_to_path(self.instance, f"/user/hand/right/{trigger_path}")),
            )
            xr.suggest_interaction_profile_bindings(
                self.instance,
                xr.InteractionProfileSuggestedBinding(
                    interaction_profile=xr.string_to_path(self.instance, profile),
                    count_suggested_bindings=4,
                    suggested_bindings=bindings,
                ),
            )

        if self.tracker_roles:
            for role in self.tracker_roles:
                self.tracker_paths[role] = xr.string_to_path(self.instance, HTCX_ROLE_PREFIX + role)
            n = len(self.tracker_roles)
            self.tracker_action = xr.create_action(
                self.action_set,
                xr.ActionCreateInfo(
                    action_type=xr.ActionType.POSE_INPUT,
                    action_name="tracker_pose",
                    localized_action_name="Tracker Pose",
                    count_subaction_paths=n,
                    subaction_paths=(xr.Path * n)(*[self.tracker_paths[r] for r in self.tracker_roles]),
                ),
            )
            bindings = (xr.ActionSuggestedBinding * n)(*[
                xr.ActionSuggestedBinding(action=self.tracker_action,
                                          binding=xr.string_to_path(self.instance, f"{HTCX_ROLE_PREFIX}{r}/input/grip/pose"))
                for r in self.tracker_roles])
            xr.suggest_interaction_profile_bindings(
                self.instance,
                xr.InteractionProfileSuggestedBinding(
                    interaction_profile=xr.string_to_path(self.instance, HTCX_PROFILE),
                    count_suggested_bindings=n,
                    suggested_bindings=bindings,
                ),
            )

    def _attach_actions(self) -> None:
        sets = (xr.ActionSet * 1)(self.action_set)
        xr.attach_session_action_sets(self.session, xr.SessionActionSetsAttachInfo(count_action_sets=1, action_sets=sets))
        for hand, path in self.hand_paths.items():
            self.grip_spaces[hand] = xr.create_action_space(
                self.session,
                xr.ActionSpaceCreateInfo(action=self.grip_action, subaction_path=path, pose_in_action_space=identity_pose()),
            )
        for role, path in self.tracker_paths.items():
            self.tracker_spaces[role] = xr.create_action_space(
                self.session,
                xr.ActionSpaceCreateInfo(action=self.tracker_action, subaction_path=path, pose_in_action_space=identity_pose()),
            )
        self._active_sets = (xr.ActiveActionSet * 1)(xr.ActiveActionSet(action_set=self.action_set, subaction_path=xr.NULL_PATH))

    def sync_actions(self) -> None:
        xr.sync_actions(self.session, xr.ActionsSyncInfo(count_active_action_sets=1, active_action_sets=self._active_sets))

    def trigger(self, hand: str) -> float:
        """Current trigger value (0..1) of a hand; call :meth:`sync_actions` first."""
        state = xr.get_action_state_float(
            self.session, xr.ActionStateGetInfo(action=self.trigger_action, subaction_path=self.hand_paths[hand]))
        return float(state.current_state) if state.is_active else 0.0

    def view_fovs(self, time_ns: int):
        """Per-view (left, right, up, down) field of view angles in radians."""
        _, views = self.locate_views(time_ns)
        return [(v.fov.angle_left, v.fov.angle_right, v.fov.angle_up, v.fov.angle_down) for v in views]

    def recommended_view_size(self):
        """(width, height) the runtime recommends for one view's image."""
        views = xr.enumerate_view_configuration_views(self.instance, self.system_id, self.view_configuration_type)
        return int(views[0].recommended_image_rect_width), int(views[0].recommended_image_rect_height)

    def current_interaction_profile(self, hand: str) -> str:
        state = xr.get_current_interaction_profile(self.session, self.hand_paths[hand])
        if state.interaction_profile == xr.NULL_PATH:
            return ""
        return xr.path_to_string(self.instance, state.interaction_profile)

    def tracker_interaction_profile(self, role: str) -> str:
        state = xr.get_current_interaction_profile(self.session, self.tracker_paths[role])
        if state.interaction_profile == xr.NULL_PATH:
            return ""
        return xr.path_to_string(self.instance, state.interaction_profile)

    def vive_tracker_paths(self) -> Dict[str, str]:
        """role path -> persistent path of every tracker the runtime reports (xrEnumerateViveTrackerPathsHTCX)."""
        fn = C.cast(xr.get_instance_proc_addr(self.instance, "xrEnumerateViveTrackerPathsHTCX"), _PFN_EnumerateViveTrackerPaths)
        count = C.c_uint32(0)
        exc = xr.check_result(xr.Result(fn(self.instance, 0, C.byref(count), None)), "xrEnumerateViveTrackerPathsHTCX")
        if exc.is_exception():
            raise exc
        items = (_ViveTrackerPaths * count.value)()
        for item in items:
            item.type = _TYPE_VIVE_TRACKER_PATHS_HTCX
        exc = xr.check_result(xr.Result(fn(self.instance, count.value, C.byref(count), items)), "xrEnumerateViveTrackerPathsHTCX")
        if exc.is_exception():
            raise exc
        return {xr.path_to_string(self.instance, i.role_path): xr.path_to_string(self.instance, i.persistent_path)
                for i in items[: count.value]}

    # -- locating ----------------------------------------------------------------

    def locate(self, space: xr.Space, time_ns: int, base: Optional[xr.Space] = None) -> xr.SpaceLocation:
        return xr.locate_space(space, base if base is not None else self.stage, time_ns)

    def locate_views(self, time_ns: int, base: Optional[xr.Space] = None):
        return xr.locate_views(
            self.session,
            xr.ViewLocateInfo(
                view_configuration_type=self.view_configuration_type,
                display_time=time_ns,
                space=base if base is not None else self.stage,
            ),
        )
