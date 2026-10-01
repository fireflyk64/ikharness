# `ikh` command line driver

**Goal.** One entry point for everything, with sane defaults, so a run is one command.

**State.** Working. `scripts/ikh` is a wrapper that sets the environment (Godot binary,
Monado runtime manifest, `XDG_RUNTIME_DIR`) and calls `python -m ikharness.ikh`.

```
ikh status                                   what is installed / running / built
ikh dataset build --model M --anim A ...     export a dataset (see docs/datasets.md)
ikh dataset info DATASET                     bones, limb lengths, ground contact
ikh dataset select --dataset D --count N [--out D2]     keep the most different poses (farthest point sampling)
ikh dataset export-retargeted --dataset D --out clip.glb [--scene x.tscn] [--animation x.res] [--verify]
                                            the dataset as GLB / Godot scene / animation on the standard skeleton
ikh eval --dataset D --tracker-set 6pt --ik renik [--perturb NAME:MAG] [--settle N]
         [--readout json|shadermotion|shadermotion-gpu]
                                            read solved poses from JSON, from CPU-encoded ShaderMotion
                                            pixels, or from frames rendered by the recorder shader
                                            (software OpenGL on a private Xvfb display)
         [--calibration rules|tpose]        tracker offsets given, or derived from the T-pose frame
ikh suite [--suite suites/default.json] --ik renik [--build]      the single number
         [--readout json|shadermotion|shadermotion-gpu|xr]   how solved poses come back; xr = whole OpenXR chain per entry
         [--calibration rules|tpose]
ikh report [FILES...] [--baseline N] [--out report.md] [--json report.json]
                                            compare suite reports (default out/suite/*.json) and score files:
                                            markdown tables with deltas, per-bone changes
ikh negative --dataset D --ik renik          perturbation ladder, fails if not monotonic
ikh service                                  start monado-service headless (foreground)
ikh probe                                    read poses back through OpenXR
ikh replay --dataset D --tracker-set 6pt [--verify] [--loop] [--calibrate]     stream into Monado
ikh calibrate --dataset D --tracker-set 6pt  T-pose, hold 1 s, pull both triggers (through the driver)
ikh xr --dataset D --tracker-set 6pt --ik builtin [--frames N] [--capture fbdir|import|ffmpeg] [--perturb NAME:MAG]
                                            the whole OpenXR chain unattended: monado-service, the Godot XR
                                            demo under Xvfb, T-pose + triggers, replay, poses read off the screen
         [--video x264-crf23]               also record the display with ffmpeg and score from the recording
ikh screenshot (--fbdir DIR | --display :N) --out shot.png     grab an Xvfb framebuffer or any X display
ikh video roundtrip --images DIR --skeleton D [--score]        what video codecs do to ShaderMotion poses
ikh video extract --video V --out-dir DIR [--every N | --times T.json]    recording -> frames
ikh devices | ikh pose DEV x y z [qx qy qz qw] | ikh drop DEV     poke the driver
```

Environment variables honoured: `GODOT` (path to the Godot 4.7 binary), `MONADO_PREFIX`,
`XR_RUNTIME_JSON`, `IKH_PORT`, `IKH_DEBUG`, `IKH_MAX_RSS_MB`, `IKH_MIN_FREE_MB`, `IKH_GPU_LAUNCHER` (command
prefix that provides a display for the GPU readout, default `python -m ikharness.xvfb --`).

## Feedback loop

`ikh status` is the first thing to run on a fresh machine; every line should say ok.
`ikh --help` and each subcommand's `--help` document the flags.
