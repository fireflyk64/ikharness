"""Retargeting foreign rigs onto Godot's humanoid profile with Godot's own importer.

A *bone map* names, for each ``SkeletonProfileHumanoid`` bone, the source rig's bone.
Presets cover the rigs we have; a JSON file ``{"Hips": "J_Bip_C_Hips", ...}`` works too.
:func:`import_retargeted` writes a throw-away Godot project containing the model, a
``BoneMap`` resource and a ``.import`` file with the retarget options (bone renamer,
rest fixer), runs ``godot --headless --import`` and returns the project path so the
exporter can ``load("res://model.glb")`` the retargeted scene.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, Optional

PROFILE_BONES = [
    "Root", "Hips", "Spine", "Chest", "UpperChest", "Neck", "Head", "LeftEye", "RightEye", "Jaw",
    "LeftShoulder", "LeftUpperArm", "LeftLowerArm", "LeftHand",
    "LeftThumbMetacarpal", "LeftThumbProximal", "LeftThumbDistal",
    "LeftIndexProximal", "LeftIndexIntermediate", "LeftIndexDistal",
    "LeftMiddleProximal", "LeftMiddleIntermediate", "LeftMiddleDistal",
    "LeftRingProximal", "LeftRingIntermediate", "LeftRingDistal",
    "LeftLittleProximal", "LeftLittleIntermediate", "LeftLittleDistal",
    "RightShoulder", "RightUpperArm", "RightLowerArm", "RightHand",
    "RightThumbMetacarpal", "RightThumbProximal", "RightThumbDistal",
    "RightIndexProximal", "RightIndexIntermediate", "RightIndexDistal",
    "RightMiddleProximal", "RightMiddleIntermediate", "RightMiddleDistal",
    "RightRingProximal", "RightRingIntermediate", "RightRingDistal",
    "RightLittleProximal", "RightLittleIntermediate", "RightLittleDistal",
    "LeftUpperLeg", "LeftLowerLeg", "LeftFoot", "LeftToes",
    "RightUpperLeg", "RightLowerLeg", "RightFoot", "RightToes",
]

# VRM 0.x / UniVRM ``J_Bip_<side>_<name>`` rigs (the MMD sample).
_VRM_FINGERS = {"Thumb": "Thumb", "Index": "Index", "Middle": "Middle", "Ring": "Ring", "Little": "Little"}


def vrm_bone_map() -> Dict[str, str]:
    m = {
        "Root": "Root", "Hips": "J_Bip_C_Hips", "Spine": "J_Bip_C_Spine", "Chest": "J_Bip_C_Chest",
        "UpperChest": "J_Bip_C_UpperChest", "Neck": "J_Bip_C_Neck", "Head": "J_Bip_C_Head",
        "LeftEye": "J_Adj_L_FaceEye", "RightEye": "J_Adj_R_FaceEye",
    }
    for side, s in (("Left", "L"), ("Right", "R")):
        m[f"{side}Shoulder"] = f"J_Bip_{s}_Shoulder"
        m[f"{side}UpperArm"] = f"J_Bip_{s}_UpperArm"
        m[f"{side}LowerArm"] = f"J_Bip_{s}_LowerArm"
        m[f"{side}Hand"] = f"J_Bip_{s}_Hand"
        m[f"{side}UpperLeg"] = f"J_Bip_{s}_UpperLeg"
        m[f"{side}LowerLeg"] = f"J_Bip_{s}_LowerLeg"
        m[f"{side}Foot"] = f"J_Bip_{s}_Foot"
        m[f"{side}Toes"] = f"J_Bip_{s}_ToeBase"
        for finger in ("Index", "Middle", "Ring", "Little"):
            m[f"{side}{finger}Proximal"] = f"J_Bip_{s}_{finger}1"
            m[f"{side}{finger}Intermediate"] = f"J_Bip_{s}_{finger}2"
            m[f"{side}{finger}Distal"] = f"J_Bip_{s}_{finger}3"
        m[f"{side}ThumbMetacarpal"] = f"J_Bip_{s}_Thumb1"
        m[f"{side}ThumbProximal"] = f"J_Bip_{s}_Thumb2"
        m[f"{side}ThumbDistal"] = f"J_Bip_{s}_Thumb3"
    return m


# BVH-style rigs as in the Perfume clips: Chest..Chest4, Collar/Shoulder/Elbow/Wrist, Hip/Knee/Ankle/Toe.
def bvh_perfume_bone_map() -> Dict[str, str]:
    m = {"Hips": "Hips", "Spine": "Chest", "Chest": "Chest2", "UpperChest": "Chest4", "Neck": "Neck", "Head": "Head"}
    for side in ("Left", "Right"):
        m[f"{side}Shoulder"] = f"{side}Collar"
        m[f"{side}UpperArm"] = f"{side}Shoulder"
        m[f"{side}LowerArm"] = f"{side}Elbow"
        m[f"{side}Hand"] = f"{side}Wrist"
        m[f"{side}UpperLeg"] = f"{side}Hip"
        m[f"{side}LowerLeg"] = f"{side}Knee"
        m[f"{side}Foot"] = f"{side}Ankle"
        m[f"{side}Toes"] = f"{side}Toe"
    return m


# Mixamo (``mixamorig:`` or ``mixamorig6_`` prefixes are stripped by Godot's importer when
# ``mixamo`` naming is detected; the map uses the bare names).
def mixamo_bone_map(prefix: str = "mixamorig_") -> Dict[str, str]:
    m = {"Hips": "Hips", "Spine": "Spine", "Chest": "Spine1", "UpperChest": "Spine2", "Neck": "Neck", "Head": "Head"}
    for side in ("Left", "Right"):
        m[f"{side}Shoulder"] = f"{side}Shoulder"
        m[f"{side}UpperArm"] = f"{side}Arm"
        m[f"{side}LowerArm"] = f"{side}ForeArm"
        m[f"{side}Hand"] = f"{side}Hand"
        m[f"{side}UpperLeg"] = f"{side}UpLeg"
        m[f"{side}LowerLeg"] = f"{side}Leg"
        m[f"{side}Foot"] = f"{side}Foot"
        m[f"{side}Toes"] = f"{side}ToeBase"
        for finger, src in (("Thumb", "Thumb"), ("Index", "Index"), ("Middle", "Middle"), ("Ring", "Ring"), ("Little", "Pinky")):
            names = ["Metacarpal", "Proximal", "Distal"] if finger == "Thumb" else ["Proximal", "Intermediate", "Distal"]
            for i, n in enumerate(names, start=1):
                m[f"{side}{finger}{n}"] = f"{side}Hand{src}{i}"
    return {k: prefix + v for k, v in m.items()}


PRESETS = {
    "vrm": vrm_bone_map,
    "bvh_perfume": bvh_perfume_bone_map,
    "mixamo": mixamo_bone_map,
}


def load_bone_map(spec: str) -> Dict[str, str]:
    """A preset name or a path to a JSON object mapping profile bone -> source bone."""
    if spec in PRESETS:
        return PRESETS[spec]()
    d = json.loads(Path(spec).read_text())
    unknown = [k for k in d if k not in PROFILE_BONES]
    if unknown:
        raise KeyError(f"bone map {spec}: not profile bones: {unknown}")
    return dict(d)


def bone_map_tres(bone_map: Dict[str, str]) -> str:
    """Text of a Godot ``BoneMap`` resource over ``SkeletonProfileHumanoid``."""
    lines = [
        '[gd_resource type="BoneMap" load_steps=2 format=3]',
        "",
        '[sub_resource type="SkeletonProfileHumanoid" id="SkeletonProfileHumanoid_ikh"]',
        "",
        "[resource]",
        'profile = SubResource("SkeletonProfileHumanoid_ikh")',
    ]
    for bone in PROFILE_BONES:
        src = bone_map.get(bone, "")
        lines.append(f'bone_map/{bone} = &"{src}"')
    return "\n".join(lines) + "\n"


def scene_import_text(model_file: str, skeleton_node_path: str, bone_map_res: str, *, fps: int = 30,
                      normalize_position_tracks: bool = False, fix_silhouette: bool = True,
                      skeleton_name: str = "GeneralSkeleton") -> str:
    """A ``.import`` file for a glTF/FBX scene with the retarget options on one skeleton node."""
    is_fbx = model_file.lower().endswith(".fbx")
    node_opts = {
        "retarget/bone_map": "Resource(\"res://bone_map.tres\")",
        "retarget/bone_renamer/rename_bones": "true",
        "retarget/bone_renamer/unique_node/make_unique": "true",
        f"retarget/bone_renamer/unique_node/skeleton_name": f'"{skeleton_name}"',
        "retarget/rest_fixer/apply_node_transforms": "true",
        "retarget/rest_fixer/normalize_position_tracks": "true" if normalize_position_tracks else "false",
        "retarget/rest_fixer/reset_all_bone_poses_after_import": "true",
        "retarget/rest_fixer/retarget_method": "1",
        "retarget/rest_fixer/keep_global_rest_on_leftovers": "true",
        "retarget/rest_fixer/fix_silhouette/enable": "true" if fix_silhouette else "false",
        "retarget/rest_fixer/fix_silhouette/threshold": "15.0",
        "retarget/rest_fixer/fix_silhouette/base_height_adjustment": "0.0",
    }
    node_block = ",\n".join(f'"{k}": {v}' for k, v in node_opts.items())
    text = f'''[remap]

importer="scene"
importer_version=1
type="PackedScene"
path="res://.godot/imported/{model_file}-ikh.scn"

[deps]

source_file="res://{model_file}"
dest_files=["res://.godot/imported/{model_file}-ikh.scn"]

[params]

nodes/root_type=""
nodes/root_name=""
nodes/apply_root_scale=true
nodes/root_scale=1.0
nodes/import_as_skeleton_bones=false
nodes/use_node_type_suffixes=true
meshes/ensure_tangents=false
meshes/generate_lods=false
meshes/create_shadow_meshes=false
meshes/light_baking=0
meshes/lightmap_texel_size=0.2
meshes/force_disable_compression=false
skins/use_named_skins=true
animation/import=true
animation/fps={fps}
animation/trimming=false
animation/remove_immutable_tracks=false
animation/import_rest_as_RESET=false
import_script/path=""
_subresources={{
"nodes": {{
"PATH:{skeleton_node_path}": {{
{node_block}
}}
}}
}}
'''
    if is_fbx:
        text += "fbx/importer=0\nfbx/allow_geometry_helper_nodes=false\nfbx/embedded_image_handling=1\n"
    else:
        text += "gltf/naming_version=1\ngltf/embedded_image_handling=1\n"
    return text


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "godot" / "tools"
PROFILE_JSON = TOOLS / "humanoid_profile.json"


def profile_global_rotations() -> Dict[str, list]:
    """Global reference orientation of every profile bone (from godot/tools/humanoid_profile.json)."""
    doc = json.loads(PROFILE_JSON.read_text())
    return {b["name"]: b["global_reference_pose"]["rotation"] for b in doc["bones"]}


def rest_conformance(skeleton) -> Dict[str, float]:
    """Degrees between each body bone's rest orientation and the humanoid profile's reference."""
    import math

    import numpy as np

    from .dataset import BODY_BONES
    from .mathutil import quat_angle

    prof = profile_global_rotations()
    out: Dict[str, float] = {}
    for b in BODY_BONES:
        if skeleton.has(b) and b in prof:
            out[b] = math.degrees(quat_angle(skeleton.bones[b].rest_global.rotation, np.array(prof[b])))
    return out


def godot_binary() -> str:
    return os.environ.get("GODOT", "godot")


def skeleton_paths(model: Path) -> Dict[str, int]:
    """Scene-relative Skeleton3D paths in a model, with bone counts, via godot/tools/skeleton_path.gd."""
    from .proc import run_guarded
    res = run_guarded([godot_binary(), "--headless", "--path", str(TOOLS), "-s", "skeleton_path.gd", "--", str(model)], timeout=300)
    out: Dict[str, int] = {}
    for line in res.stdout.splitlines():
        if line.startswith("SKELETON "):
            _, path, count = line.split(" ", 2)
            out[path] = int(count)
    if not out:
        raise RuntimeError(f"no Skeleton3D found in {model}:\n{(res.stdout + res.stderr)[-2000:]}")
    return out


def import_retargeted(model: Path, bone_map: Dict[str, str], project_dir: Optional[Path] = None, *,
                      fps: int = 30, fix_silhouette: bool = True, normalize_position_tracks: bool = False,
                      extra_files: Optional[list] = None) -> Path:
    """Import ``model`` through Godot with retargeting; returns the project dir holding ``res://<model name>``."""
    model = Path(model).resolve()
    project_dir = Path(project_dir) if project_dir else Path(tempfile.mkdtemp(prefix="ikh-retarget-"))
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "project.godot").write_text(
        '; ikharness temporary import project\nconfig_version=5\n\n[application]\nconfig/name="ikharness retarget"\n'
        'config/features=PackedStringArray("4.7")\n\n[rendering]\nrenderer/rendering_method="gl_compatibility"\n')
    shutil.copy2(model, project_dir / model.name)
    for extra in extra_files or []:
        shutil.copy2(extra, project_dir / Path(extra).name)
    (project_dir / "bone_map.tres").write_text(bone_map_tres(bone_map))
    paths = skeleton_paths(model)
    skel_path = max(paths, key=paths.get)  # the biggest skeleton is the body
    (project_dir / f"{model.name}.import").write_text(
        scene_import_text(model.name, skel_path, "res://bone_map.tres", fps=fps,
                          normalize_position_tracks=normalize_position_tracks, fix_silhouette=fix_silhouette))
    from .proc import run_guarded
    res = run_guarded([godot_binary(), "--headless", "--path", str(project_dir), "--import"], timeout=1800)
    # Godot names the imported scene with a content hash, whatever the .import file said.
    imported = list((project_dir / ".godot" / "imported").glob(f"{model.name}-*.scn"))
    if res.killed or res.returncode != 0 or not imported:
        raise RuntimeError(f"retarget import failed for {model} (killed={res.killed or 'no'}, peak {res.peak_rss_mb:.0f} MB):\n{res.log[-4000:]}")
    return project_dir
