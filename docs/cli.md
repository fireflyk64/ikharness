# `ikh` command line driver

**Goal.** One entry point for everything, with sane defaults, so a run is one command.

**State.** Working. `scripts/ikh` is a wrapper that sets the environment (Godot binary,
Monado runtime manifest, `XDG_RUNTIME_DIR`) and calls `python -m ikharness.ikh`.

```
ikh status                                   what is installed / running / built
ikh dataset build --model M --anim A ...     export a dataset (see docs/datasets.md)
ikh dataset info DATASET                     bones, limb lengths, ground contact
ikh eval --dataset D --tracker-set 6pt --ik renik [--perturb NAME:MAG] [--settle N]
ikh suite [--suite suites/default.json] --ik renik [--build]      the single number
ikh negative --dataset D --ik renik          perturbation ladder, fails if not monotonic
ikh service                                  start monado-service headless (foreground)
ikh probe                                    read poses back through OpenXR
ikh replay --dataset D --tracker-set 6pt [--verify] [--loop]      stream into Monado
ikh devices | ikh pose DEV x y z [qx qy qz qw] | ikh drop DEV     poke the driver
```

Environment variables honoured: `GODOT` (path to the Godot 4.7 binary), `MONADO_PREFIX`,
`XR_RUNTIME_JSON`, `IKH_PORT`, `IKH_DEBUG`.

## Feedback loop

`ikh status` is the first thing to run on a fresh machine; every line should say ok.
`ikh --help` and each subcommand's `--help` document the flags.
