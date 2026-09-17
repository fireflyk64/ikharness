"""Bone maps and the Godot import retargeter."""

import os
import shutil
from pathlib import Path

import pytest

from ikharness.retarget import (PROFILE_BONES, bone_map_tres, bvh_perfume_bone_map, load_bone_map,
                                mixamo_bone_map, rest_conformance, scene_import_text, vrm_bone_map)

PERFUME = Path.home() / "dev/animations/ANIM_perfume/ANIM_aachan.glb"


def test_presets_cover_the_body():
    for name, fn in (("vrm", vrm_bone_map), ("bvh_perfume", bvh_perfume_bone_map), ("mixamo", mixamo_bone_map)):
        m = fn()
        for bone in ("Hips", "Spine", "Head", "LeftUpperArm", "LeftHand", "RightFoot", "LeftToes"):
            assert bone in m, (name, bone)
        assert set(m) <= set(PROFILE_BONES), name
    assert vrm_bone_map()["LeftIndexProximal"] == "J_Bip_L_Index1"
    assert bvh_perfume_bone_map()["LeftUpperArm"] == "LeftShoulder"  # BVH "Shoulder" is the upper arm


def test_bone_map_resource_and_import_text(tmp_path):
    tres = bone_map_tres({"Hips": "hip", "Head": "head"})
    assert 'type="BoneMap"' in tres and 'bone_map/Hips = &"hip"' in tres and 'bone_map/LeftHand = &""' in tres
    text = scene_import_text("m.glb", "Root/Skeleton3D", "res://bone_map.tres")
    assert '"PATH:Root/Skeleton3D"' in text and '"retarget/rest_fixer/retarget_method": 1' in text
    j = tmp_path / "map.json"
    j.write_text('{"Hips": "a", "Head": "b"}')
    assert load_bone_map(str(j)) == {"Hips": "a", "Head": "b"}
    j.write_text('{"Bogus": "a"}')
    with pytest.raises(KeyError):
        load_bone_map(str(j))


def test_rest_conformance_of_shipped_dataset():
    from ikharness.dataset import Dataset
    d = Dataset.load(Path(__file__).parent / "data/mini_walk.json")
    dev = rest_conformance(d.skeleton)
    assert dev and max(dev.values()) < 0.1


@pytest.mark.skipif(not PERFUME.exists(), reason="Perfume clips not available")
def test_retarget_perfume_through_godot(tmp_path):
    exe = os.environ.get("GODOT") or shutil.which("godot") or str(Path.home() / ".local/bin/godot")
    if not Path(exe).exists():
        pytest.skip("Godot not found")
    os.environ["GODOT"] = exe
    from ikharness.build_dataset import main
    out = tmp_path / "perfume.json"
    rc = main(["--model", str(PERFUME), "--anim", str(PERFUME), "--bone-map", "bvh_perfume", "--frames", "4",
               "--hips-mode", "absolute", "--out", str(out)])
    assert rc == 0 and out.exists()
    from ikharness.dataset import Dataset
    d = Dataset.load(out)
    assert d.skeleton.has("LeftUpperArm") and d.skeleton.has("RightToes")
    assert max(rest_conformance(d.skeleton).values()) < 0.5
    assert 0.7 < d.skeleton.hips_height < 1.1
