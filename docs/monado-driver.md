# Monado driver

**Goal.** Feed virtual tracker poses into closed-source or runtime-level applications
(OpenXR apps, OpenVR apps through Monado's OpenVR target, SteamVR through the plugin).

**State.** Working and tested. Details and the wire protocol: [`../monado/README.md`](../monado/README.md).

## Run

```sh
monado/scripts/setup_monado.sh          # once, ~20 min: clone, patch, build, install
ikh service                             # start monado-service headless (null compositor)
ikh probe                               # read every pose back through OpenXR
ikh replay --dataset out/datasets/vsk_walk.json --tracker-set 6pt --verify
```

## Feedback loop

* `pytest tests/test_monado_driver.py` starts its own service and checks HELLO, ACK, VIEW
  space, stereo IPD, Index grip actions, xdev spaces for trackers, and tracker drop-out.
* `ikh replay --verify` reports the OpenXR read-back error per frame (expect 0.00 mm).
* `IKH_LOG=debug` on the service prints every applied frame.

## Projection (fixed 2026-09-30)

The picture used to look squished and zoomed in. Two causes: the driver described two
portrait 640 × 720 eyes with 90°, and the null compositor recommended 320 × 240 per eye no
matter what. Now the driver reports square 1024 × 1024 eyes with a symmetric 100° field of
view (vertical derived for square pixels) and the patched null compositor recommends that
size. Check: `ikh probe` or the projection assertions in `tests/test_monado_driver.py`.

## Controller inputs and several clients

`IkhClient.send_inputs({"left_hand": Inputs(trigger=1.0), "right_hand": Inputs(trigger=1.0)})`
pulls both triggers (visible as the Index `trigger/value` action in OpenXR). A second
client can connect while a feeder is active and call `get_state()` to read every device's
current pose.

The controllers are Valve Index controllers that also answer to the Oculus Touch and the
simple-controller profiles (the emulation table of Monado's own Index driver). That matters
for Godot: its default action map has no Index profile, so without the emulation a stock
Godot project receives neither hand poses nor buttons.

`python/ikharness/service.py` (`MonadoService`) starts and stops a guarded service from
Python with a temporary config (eye size, tracker list); `ikh xr` uses it.

## Trackers as OpenXR tracker roles

With `monado/patches/0003-htcx-vive-tracker-interaction.patch` (applied by
`setup_monado.sh`) the runtime offers `XR_HTCX_vive_tracker_interaction`: each tracker
whose role name is one of the extension's roles (waist, chest, left/right foot, knee,
elbow, shoulder, wrist, ankle, camera, keyboard, handheld_object) is bound at
`/user/vive_tracker_htcx/role/<role>` with the Vive tracker profile. That is how Godot and
Unity's OpenXR plugin find body trackers. `XR_MNDX_xdev_space` keeps working for clients
that want every device regardless of role. Check: `test_trackers_reach_openxr_through_htcx_roles`.
Details in [godot-openxr.md](godot-openxr.md).

## SteamVR plugin

Monado's plugin for SteamVR (`driver_monado.so`, installed under
`~/.local/monado-ikharness/share/steamvr-monado`, registered with
`vrpathreg adddriver`) runs the whole runtime, including this driver, inside SteamVR's
`vrserver`. `monado/patches/0004-steamvr-plugin-trackers-and-eye-size.patch` changes two
things in it:

* **Per-eye render target size.** Upstream answers `GetRecommendedRenderTargetSize` with the
  size of the whole screen, which holds both eyes side by side: 2867×1433 per eye for a
  square field of view. SteamVR then renders every eye image twice as wide as its frustum,
  and every mirror view shows it stretched. Together with the driver's old portrait eyes
  this is the likely cause of the "squished" view reported on 2026-09-30. Now 1433×1433
  (1024² × the plugin's 140 % supersampling), aspect equal to the field of view.
* **Generic trackers.** Upstream forwards the HMD and two controllers. Every generic tracker
  is now added as `TrackedDeviceClass_GenericTracker`, with the role from its name as
  controller type (`vive_tracker_waist`, ...) and in SteamVR's `trackers` settings
  (`/devices/monado/IKH-TRK-waist` → `TrackerRole_Waist`), poses updated every frame,
  dropped trackers reported as disconnected. `IKH_STEAMVR_TRACKERS=0` restores the old
  behaviour.

SteamVR is not installed on the development machine, so this is tested against
`monado/tests/steamvr_mock_host.cpp`: a small stand-in for `vrserver` that loads the plugin,
activates the devices it adds and records properties, poses and the HMD's optics
(`tests/test_steamvr_plugin.py`, 2 s). What that cannot show is SteamVR's own behaviour
(direct mode on a headless machine, its compositor); try it with
`STEAMVR_EMULATE_INDEX_CONTROLLER=1` if a game wants Index controllers.

```sh
g++ -std=c++17 -I ~/dev/monado/src/external/openvr_includes monado/tests/steamvr_mock_host.cpp -o out/steamvr_mock_host -ldl -lpthread
IKH_PORT=4363 out/steamvr_mock_host ~/.local/monado-ikharness/share/steamvr-monado/bin/linux64/driver_monado.so 15 < /dev/null
```

## Open items

* The SteamVR plugin changes are untested inside SteamVR itself.
