"""ShaderMotion: humanoid poses encoded as colored squares in a video frame.

* :mod:`.codec` - slot values <-> colors, the hips float scheme, frame layout, images.
* (next) pose layer: swing-twist angles in Unity's calibrated bone axes <-> bone rotations.

Format reference: ``shader_motion_specification`` in V-Sekai/shader-motion-navy-lead-ostrich
and lox9973's original decoder (``MotionDecoder``/``ShaderImpl``).
"""
