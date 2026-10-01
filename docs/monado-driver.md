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

## Open items

* No `XR_HTCX_vive_tracker_interaction` in Monado's OpenXR layer: trackers reach OpenXR apps
  only through Monado's `XR_MNDX_xdev_space`. Godot and Unity's OpenXR plugins expect HTCX.
  See [godot-openxr.md](godot-openxr.md).
* The SteamVR plugin forwards only HMD and controllers upstream.
