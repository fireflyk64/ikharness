# Report for V-Sekai/godot-shader-motion: `swing_twist_inv` returns out-of-range swings for quaternions with w < 0

Status: drafted here, **not filed** (filing an issue or pull request on someone else's
repository is the project owner's call). The patch is in
[`swing_twist_inv.patch`](swing_twist_inv.patch).

## Where

`shader_motion/core/humanoid/transform_util.gd`, `static func swing_twist_inv(q: Quaternion) -> Vector3`
(vendored unmodified in this repository as `godot/harness/addons/humanoid/transform_util.gd`).

## What happens

`swing_twist_inv` is the inverse of `swing_twist(Vector3(twist_x, swing_y, swing_z))`. `q` and
`-q` are the same rotation, and bone rotations coming out of Godot (products of rest and pose
quaternions) have w < 0 about as often as not. For those the function returns a swing vector
*longer than π*:

```
swing_twist(Vector3(0.3, 0.5, -0.4)) = q
swing_twist_inv( q) = (0.3,  0.5,    -0.4)       # fine
swing_twist_inv(-q) = (0.3, -4.4063,  3.5251)    # |swing| = 5.64 rad = 323 degrees
```

`swing_twist(0.3, -4.4063, 3.5251)` does give back the same rotation, so a plain round trip
does not show the problem. But the values are muscles: every consumer scales them by 1/π
and clamps or wraps each axis into [-1, 1] (the ShaderMotion encoder has to, a tile holds
[-1, 1]). Wrapping the axes of an over-long swing vector one by one yields a different
rotation. Measured over 20 000 random rotations with moderate angles (twist within ±57°,
swing within ±86° per axis), negated so that w < 0, then wrapped per axis:

| | mean error | median | max | wrong by more than 1° |
|---|---|---|---|---|
| upstream `swing_twist_inv` | 118.6° | 144.5° | 180° | 99 % |
| with the patch | 0.000° | 0.000° | 0.000° | 0 % |

In an encoder built on it this showed as arms 90 to 160 degrees off on about half of the
frames, depending on the sign the quaternion happened to have.

## Cause

```gdscript
if a < 0:
	twistX *= -1
if d < 0:
	twistX *= -1
```

The second flip makes `swingW = a / twistX` negative when `d < 0`, so
`yz = acos(swingW) * 2` lands in (π, 2π): the long way round.

## Fix

Use the representative with w >= 0, then the existing derivation holds as written:

```gdscript
static func swing_twist_inv(q: Quaternion) -> Vector3:
	if q.w < 0.0:
		q = -q          # same rotation; keeps the swing angle within [0, pi]
	var a: float = q.x
	...
	if a < 0:
		twistX *= -1
	# (the `if d < 0` flip goes away)
```

A second, cosmetic point: the last step divides by `twistX` and `twistW` after replacing
zeros with `1e-8`. With `twistW² + twistX² = 1` the two expressions reduce to

```gdscript
var y: float = (b * twistW - c * twistX) / sinc
var z: float = (b * twistX + c * twistW) / sinc
```

which need no epsilon and lose no precision for small twists. The patch includes both.

## How it was found

ikharness encodes IK results as ShaderMotion pixels and scores them against a reference.
With the vendored inverse the CPU encoder disagreed with an independent Python
implementation by 90 to 160 degrees on arm bones; with its own inverse
(`godot/harness/shadermotion_encoder.gd`, same math as the patch) the two agree to 0.013°.
The numbers above come from a direct Python port of the upstream function.
