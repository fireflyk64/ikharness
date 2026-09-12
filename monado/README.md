# Monado `ikharness` driver

A Monado (OpenXR / OpenVR runtime) driver that publishes a head-mounted
display, two hand controllers and any number of generic body trackers whose
poses come from an external orchestrator over a TCP socket. Nothing is
filtered, predicted or extrapolated: every device holds exactly the last pose
it was given, so an IK evaluation is deterministic and repeatable.

```
 orchestrator (python)  ──TCP 4343──▶  monado-service (ikharness driver)
                                              │
                                     OpenXR / OpenVR / SteamVR plugin
                                              │
                                     application under test (Godot, Unity, VRChat, ...)
```

## Layout

| Path | What |
|---|---|
| `driver/ikharness/ikh_protocol.h` | Wire protocol, dependency free, the single source of truth for clients |
| `driver/ikharness/ikh_hub.c` | Config loading, device table, socket server thread, shared state |
| `driver/ikharness/ikh_hmd.c` | HMD device (`XRT_DEVICE_GENERIC_HMD`, no lens distortion) |
| `driver/ikharness/ikh_controller.c` | Valve Index (default) or Khronos simple controller emulation |
| `driver/ikharness/ikh_tracker.c` | Generic tracker presented as a Vive Tracker Gen3 |
| `target/target_builder_ikharness.c` | Monado "builder" that assembles the system from the config |
| `patches/0001-register-ikharness-driver.patch` | The six small CMake / list edits that register the driver in the Monado tree |
| `config/ikharness.json` | Default device set: HMD, Index controllers, 8 trackers |
| `scripts/setup_monado.sh` | Clone Monado at the pinned commit (`MONADO_COMMIT`), link the driver in, apply the patch, build, install |
| `scripts/run_service.sh` | Start `monado-service` headless with the driver |

The driver sources live in this repository and are *symlinked* into the
Monado tree, so edits here rebuild directly with `ninja -C ~/dev/monado/build`.

## Building

```sh
sudo apt install cmake ninja-build build-essential pkg-config libeigen3-dev libvulkan-dev \
     glslang-tools libcjson-dev libbsd-dev libsystemd-dev libx11-xcb-dev libxrandr-dev \
     libxcb-randr0-dev libgl1-mesa-dev libegl1-mesa-dev libwayland-dev wayland-protocols \
     mesa-vulkan-drivers vulkan-tools libopenxr-dev
monado/scripts/setup_monado.sh          # ~15 min on 2 cores, installs to ~/.local/monado-ikharness
```

Only the drivers needed for testing are compiled (ikharness, simulated,
remote); the OpenXR runtime, the OpenVR runtime target (`libopenvr.so`, lets
OpenVR applications run on Monado directly) and the SteamVR plugin
(`steamvr-monado`) are built.

## Running

```sh
monado/scripts/run_service.sh           # prints XR_RUNTIME_JSON to export for clients
```

`monado-service` runs with the **null compositor** by default
(`XRT_COMPOSITOR_NULL=1`): no window, no display, no GPU needed beyond a
Vulkan ICD (lavapipe is fine). Applications still get swapchains and can
render, the frames are simply discarded. Set `IKH_COMPOSITOR=main` to use the
real compositor when a display is available.

### Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `IKH_ENABLE` | `1` | Set to `0` to hide the driver so other hardware is probed |
| `IKH_CONFIG` | *(none)* | Path to a config JSON (below). Otherwise the `"ikharness"` object of `~/.config/monado/config_v0.json`, otherwise built-in defaults |
| `IKH_PORT` | `4343` | Listen port, overrides the config |
| `IKH_BIND` | `127.0.0.1` | Bind address, use `0.0.0.0` for a remote orchestrator |
| `IKH_LOG` | `info` | `trace`, `debug`, `info`, `warn`, `error` |
| `XRT_COMPOSITOR_NULL` | | `1` selects the null compositor |

### Config file

```json
{
  "version": 1,
  "port": 4343,
  "bind": "127.0.0.1",
  "hmd": { "width": 1280, "height": 720, "view_count": 2, "fov_deg": 90.0, "ipd_m": 0.063, "refresh_hz": 90.0 },
  "controllers": "index",
  "trackers": ["waist", "chest", "left_foot", "right_foot", "left_knee", "right_knee", "left_elbow", "right_elbow"]
}
```

`controllers` is `index`, `simple` or `none`. Tracker roles are free-form
strings; they become the device name `IK Harness Tracker (<role>)` and serial
`IKH-TRK-<role>`, which is how clients find them again.

Device indices on the wire are assigned in order: `0` HMD, `1`/`2` left/right
controller (if any), then the trackers in config order. Clients should not
hard-code this: the HELLO message carries the table.

## Wire protocol

See `driver/ikharness/ikh_protocol.h` for the exact layout. In short:

* TCP stream, 12-byte header `{magic "IKH1", version=1, type, payload_size}` then payload, little-endian, packed.
* On connect the server sends `HELLO`: `{device_count}` + `device_count × {index, kind, role[32], serial[32]}`.
* The client sends `FRAME`: `{frame_id, timestamp_ns (0 = now), pose_count}` + `pose_count × {index, flags, pos[3], quat[4] (x,y,z,w), linvel[3], angvel[3]}`.
  Devices not listed keep their previous pose.
* The server answers every `FRAME` with `ACK {frame_id, applied_at_ns}`; `PING` gets `PONG` with the same payload; `QUERY` re-sends `HELLO`.
* Pose flags: `ORIENTATION_VALID=1`, `POSITION_VALID=2`, `TRACKED=4`, `LINEAR_VELOCITY_VALID=8`, `ANGULAR_VELOCITY_VALID=16`, `CONNECTED=32`.
  Clearing `CONNECTED` makes the device's inputs inactive, clearing the valid bits makes the space location invalid: use this to simulate a tracker dropping out.
* Coordinates: OpenXR convention (right handed, +Y up, -Z forward, meters) in STAGE space (floor origin). Godot uses the same convention; Unity needs a handedness flip.

The Python client (`python/ikharness/protocol.py`) implements the whole thing in ~200 lines.

## How the devices reach applications

| API | HMD | Controllers | Trackers |
|---|---|---|---|
| OpenXR core | VIEW space, `xrLocateViews` | `/interaction_profiles/valve/index_controller` (or `khr/simple_controller`), grip == aim == device pose | — |
| OpenXR `XR_MNDX_xdev_space` (Monado) | ✔ | ✔ | ✔ `xrCreateXDevSpaceMNDX`, matched by serial |
| OpenXR `XR_HTCX_vive_tracker_interaction` | | | ✘ not implemented in Monado's OpenXR layer (marked `ALWAYS_DISABLED` upstream). Godot/Unity harnesses read the socket directly instead |
| OpenVR runtime target (`libopenvr.so` built by Monado) | HMD | Controller class | `TrackedDeviceClass_GenericTracker` |
| SteamVR plugin (`steamvr-monado`) | ✔ | ✔ | ✘ upstream plugin only forwards HMD + controllers |

## Testing

```sh
.venv/bin/pytest tests/test_monado_driver.py -v
```

The test starts `monado-service` itself (or uses a running one with
`IKH_EXTERNAL_SERVICE=1`), pushes poses through the socket and reads them back
through a headless OpenXR session: VIEW space, stereo view separation (IPD),
grip actions on the Index profile, and xdev spaces for the trackers, including
a tracker drop-out.
