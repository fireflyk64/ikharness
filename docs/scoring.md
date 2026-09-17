# Scoring: the metric, the single number, negative tests

**Goal.** One objective number per IK implementation that goes down when the avatar's pose
gets further from the reference, and that does not depend on how a rig's bones are oriented
in their rest pose.

**State.** Working. Scores are rest-relative; `ikh suite` produces the final number;
`ikh negative` checks that perturbations lower it.

## The metric

For every scored bone `b` in every frame:

```
delta_ref(b) = pose_ref(b).rotation × rest_ref(b).rotation⁻¹      (reference rig)
delta_res(b) = pose_res(b).rotation × rest_res(b).rotation⁻¹      (rig under test)
angle(b)     = 2·acos(|delta_ref · delta_res|)     in degrees
```

`rest_*` is each rig's own global T-pose orientation (the result file carries the harness
rig's rest; the reference dataset carries its own). Because both rigs are in a T-pose, the
deltas describe "how far from T-pose" in world space and are comparable even if one rig
has bone +Y along the bone and the other bone +X. Positions are compared in meters as well
and reported, but only rotations enter the score.

Scored bones (21): hips, spine, chest, upper chest, neck, head, both shoulders, upper and
lower arms, hands, upper and lower legs, feet, toes. Fingers, eyes and jaw are ignored.

Per bone: mean, median, p95, max angle and mean position error over the frames.
Per run: `body_score_deg` = mean over bones of their mean angle; `weighted_score_deg`
uses `DEFAULT_WEIGHTS` (large segments count more, toes and shoulders less).

## The single number (`ikh suite`)

`suites/default.json` lists `(dataset, tracker set, weight)` entries. For an implementation:

```
final_deg = Σ weight_i · weighted_score_deg_i / Σ weight_i        (lower is better)
quality   = 100 · exp(-final_deg / 25)                            (0..100, higher is better)
```

`final_deg` is the number to report; `quality` is a convenience mapping (0° → 100,
13° → 59, 38° → 22). Run it:

```sh
ikh suite --ik renik            # builds missing datasets from the suite's recipes
ikh suite --ik none             # rest-pose baseline
```

Current values (`suites/default.json`, four datasets × 6/11 point sets, 2026-09-15):

| Implementation | final (deg) | quality | walk 6pt | walk 11pt | mocap 6pt | mocap 11pt | perfume 6pt | perfume 11pt | mmd 6pt | mmd 11pt |
|---|---|---|---|---|---|---|---|---|---|---|
| builtin (Godot TwoBoneIK3D + FABRIK3D) | **16.18** | 52.4 | 12.84 | 11.24 | 15.35 | 13.46 | 27.35 | 16.58 | 15.04 | 11.66 |
| renik | **22.10** | 41.3 | 14.26 | 12.33 | 21.36 | 21.87 | 39.54 | 25.03 | 18.75 | 18.11 |
| none (rest pose) | **69.74** | 6.1 | 39.25 | 39.25 | 58.26 | 58.26 | 133.08 | 133.08 | 48.37 | 48.37 |

Numbers are weighted degrees. The rest-pose baseline scores 133° on the Perfume dance
because the dancer turns away from the rest facing. Known oddity: on the mocap set RenIK's
11-point run leaves the hands 3.8 cm off target (0.4 cm with 6 points), so its elbow pole
feeding costs precision; see `docs/godot-harness.md`.

## Negative tests (`ikh negative`)

Two layers, both must degrade monotonically with magnitude:

* **Metric layer** (pure Python, `ikharness.negative.perturb_frames`): rotate all bones, only
  the arms, or only the legs by a fixed angle; jitter randomly. Guarded by
  `tests/test_negative.py`. Proves the scorer measures what we think.
* **Pipeline layer** (`ikh negative --ik renik`): perturb the *tracker inputs* before the
  harness solves: move hand trackers (`hands_offset`), swap hands (`hands_swap`), yaw the head
  (`head_yaw`), lift feet (`feet_offset`), random noise on all trackers (`noise`). The solved
  result must score worse than the unperturbed run, and worse the larger the magnitude.

`ikh negative` prints a ladder and exits non-zero if any step is not worse than the previous
one. Use it after changing the scorer, the tracker rules, or an adapter.

## Feedback loop

1. `pytest tests/test_dataset_pipeline.py tests/test_negative.py` (seconds).
2. `ikh eval --dataset D --tracker-set 6pt --ik echo` must print 0.00° (a harness that
   returns the reference).
3. `ikh negative --dataset tests/data/mini_walk.json --ik renik` must pass.
4. Compare `ikh suite` against the table above after any change; unexpected jumps mean a
   convention broke (rest, space, tracker offsets), not that IK changed.
