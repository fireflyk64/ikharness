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

## Open items

* No `XR_HTCX_vive_tracker_interaction` in Monado's OpenXR layer: trackers reach OpenXR apps
  only through Monado's `XR_MNDX_xdev_space`. Godot and Unity's OpenXR plugins expect HTCX.
  See [godot-openxr.md](godot-openxr.md).
* The SteamVR plugin forwards only HMD and controllers upstream.
