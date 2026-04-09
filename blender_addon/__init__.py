"""
NovelViews Assets — Blender Add-on

Exposes the three blender_scripts as operators accessible from a sidebar panel:
  - Setup Hunyuan3D Cameras (blender_camera_setup)
  - Create Camera Tour      (blender_camera_tour)
  - Normalize Mesh          (blender_normalize_mesh)

Install:
  1. Zip the blender_addon/ folder (the zip must contain __init__.py at its root).
  2. In Blender: Edit > Preferences > Add-ons > Install … > select the zip.
  3. Enable "3D View: NovelViews Assets".
  4. Open the N-panel in the 3D Viewport and find the "NovelViews" tab.
"""

bl_info = {
    "name": "NovelViews Assets",
    "author": "leob03",
    "version": (1, 0, 0),
    "blender": (3, 0, 0),
    "location": "View3D > Sidebar > NovelViews",
    "description": "Hunyuan3D MultiView camera setup, camera tour, and mesh normalization",
    "category": "3D View",
}

import os
import bpy
import math
from mathutils import Vector, Matrix, Quaternion
from bpy.props import (
    FloatProperty, IntProperty, BoolProperty, PointerProperty, StringProperty,
    EnumProperty,
)
from bpy.types import Operator, Panel, PropertyGroup


# ---------------------------------------------------------------------------
# Shared camera math (mirroring the logic in the original scripts exactly)
# ---------------------------------------------------------------------------

def _get_camera_position_hunyuan(elev, azim, distance):
    elev_rad = math.radians(-elev)
    azim_rad = math.radians(azim + 90)
    x = distance * math.cos(elev_rad) * math.cos(azim_rad)
    y = distance * math.cos(elev_rad) * math.sin(azim_rad)
    z = distance * math.sin(elev_rad)
    return Vector((x, y, z))


def _hunyuan_to_blender(pos):
    """Inverse of Hunyuan3D's mesh transform (-X, Z, -Y) applied to camera pos."""
    return Vector((-pos.x, -pos.z, pos.y))


def _blender_pos_for_view(view_name, distance):
    azim, elev = _VIEW_TO_AZIM_ELEV[view_name]
    return _hunyuan_to_blender(_get_camera_position_hunyuan(elev, azim, distance))


# View definitions shared by both operators
_CAMERA_AZIMS = [0, 90, 180, 270, 0, 180, 270, 90]
_CAMERA_ELEVS = [0, 0, 0, 0, 90, -90, -45, -45]
_VIEW_NAMES   = ["top", "right", "bottom", "left", "back", "front", "front_left", "front_right"]

_VIEW_UP_AXIS = {"top": "UP_Z", "bottom": "UP_X"}
_DEFAULT_UP   = "UP_Y"
_VIEW_Z_ROT   = {"bottom": math.pi / 2}   # extra Z rotation after track_quat

_VIEW_TO_AZIM_ELEV = {
    "top":         (0,    0),
    "right":       (90,   0),
    "bottom":      (180,  0),
    "left":        (270,  0),
    "back":        (0,   90),
    "front":       (180, -90),
    "front_left":  (270, -45),
    "front_right": (90,  -45),
}

_TOUR_TRAJECTORY = [
    "front", "front_left", "left", "back",
    "right", "front_right", "front", "top", "back", "bottom",
]


# ---------------------------------------------------------------------------
# Scene properties (all user-facing parameters)
# ---------------------------------------------------------------------------

def _poll_camera(self, obj):
    return obj.type == 'CAMERA'


class NovelViewsProperties(PropertyGroup):
    # ---- Collapsed state ----
    camera_setup_expanded: BoolProperty(name="Camera Setup", default=False)
    camera_tour_expanded: BoolProperty(name="Camera Spherical Tour", default=False)
    camera_360_expanded: BoolProperty(name="Camera 360 Tour", default=False)
    normalize_mesh_expanded: BoolProperty(name="Normalize Mesh", default=False)
    render_passes_expanded: BoolProperty(name="Render Passes", default=False)

    # ---- Camera Setup ----
    camera_distance: FloatProperty(
        name="Camera Distance",
        description="Distance from camera to origin",
        default=1.45, min=0.01, max=20.0, step=1,
    )
    ortho_scale: FloatProperty(
        name="Ortho Scale",
        description="Orthographic camera scale (>=1.15 to fit a normalized mesh)",
        default=1.2, min=0.01, max=20.0, step=1,
    )
    render_size: IntProperty(
        name="Render Size (px)",
        description="Square render resolution",
        default=1024, min=64, max=8192,
    )
    use_constraints: BoolProperty(
        name="Use Track-To Constraint",
        description="Attach a Track-To constraint instead of baking rotation (easier to adjust later)",
        default=True,
    )

    # ---- Camera Tour ----
    travel_seconds: FloatProperty(
        name="Travel Duration (s)",
        description="Total movement time across all segments (pauses are added on top)",
        default=5.0, min=0.1, max=300.0, step=10,
    )
    pause_seconds: FloatProperty(
        name="Pause per Waypoint (s)",
        description="Hold time inserted at each waypoint",
        default=0.2, min=0.0, max=30.0, step=1,
    )

    # ---- Camera 360 Tour ----
    elevation_360: FloatProperty(
        name="Elevation (°)",
        description="Camera height angle in degrees (0 = horizontal ring around the subject)",
        default=0.0, min=-89.0, max=89.0, step=100,
    )
    travel_seconds_360: FloatProperty(
        name="Travel Duration (s)",
        description="Time for one full 360° revolution",
        default=5.0, min=0.1, max=300.0, step=10,
    )

    # ---- Render Passes ----
    render_camera: PointerProperty(
        name="Camera",
        description="Camera / trajectory to render from",
        type=bpy.types.Object,
        poll=_poll_camera,
    )
    render_pass_type: EnumProperty(
        name="Pass",
        description="Which render pass to output",
        items=[
            ('DEPTH',    "Depth",          "Depth normalized to object bounds (16-bit PNG)"),
            ('NORMAL',   "Surface Normal",  "Camera-space normals remapped 0–1 (RGB PNG)"),
            ('POSITION', "Position",        "World-space position map remapped 0–1 (RGB PNG)"),
        ],
        default='DEPTH',
    )
    render_output_format: EnumProperty(
        name="Output Format",
        description="Write individual PNG frames or a single MP4 video",
        items=[
            ('IMAGE_SEQ', "Image Sequence", "PNG frames written to depth/ or normal/ subfolder"),
            ('MP4',       "MP4 Video",      "Single H.264 .mp4 file via Blender's FFMPEG encoder"),
        ],
        default='IMAGE_SEQ',
    )
    depth_invert: BoolProperty(
        name="Invert Depth",
        description="Invert depth so near=white (1) and far=black (0)",
        default=True,
    )
    depth_mask_bg: BoolProperty(
        name="Mask Background",
        description="Use the alpha pass to zero out background pixels in both depth and normal maps",
        default=True,
    )
    render_output_dir: StringProperty(
        name="Output Dir",
        description="Root folder for rendered frames (depth/ and normal/ subfolders created automatically)",
        default="//renders/",
        subtype='DIR_PATH',
    )

    # ---- Normalize Mesh ----
    mesh_scale_factor: FloatProperty(
        name="Bounding Sphere Diameter",
        description="Target bounding-sphere diameter (Hunyuan3D default: 1.15)",
        default=1.15, min=0.001, max=1000.0, step=1,
    )


# ---------------------------------------------------------------------------
# Operator: Setup Hunyuan3D Cameras
# ---------------------------------------------------------------------------

class NOVELVIEWS_OT_setup_cameras(Operator):
    bl_idname  = "novelviews.setup_cameras"
    bl_label   = "Setup Cameras"
    bl_description = (
        "Create 8 orthographic cameras matching the Hunyuan3D MultiView viewpoints "
        "and configure render settings"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = context.scene.novelviews_props
        cameras = self._create_cameras(context, props)
        self._setup_render(context, props)
        if cameras:
            context.scene.camera = cameras[0]
        self.report({"INFO"}, f"Created {len(cameras)} Hunyuan3D cameras in 'Hunyuan3D_Cameras' collection")
        return {"FINISHED"}

    # ------------------------------------------------------------------
    def _create_cameras(self, context, props):
        col_name = "Hunyuan3D_Cameras"
        if col_name in bpy.data.collections:
            col = bpy.data.collections[col_name]
            for obj in list(col.objects):
                bpy.data.objects.remove(obj, do_unlink=True)
        else:
            col = bpy.data.collections.new(col_name)
            context.scene.collection.children.link(col)

        # Target empty for Track-To constraints
        target = None
        if props.use_constraints:
            t_name = "Hy3D_Camera_Target"
            if t_name in bpy.data.objects:
                target = bpy.data.objects[t_name]
            else:
                target = bpy.data.objects.new(t_name, None)
                col.objects.link(target)
            target.location = (0, 0, 0)
            target.empty_display_type  = "SPHERE"
            target.empty_display_size  = 0.1

        cameras = []
        for azim, elev, name in zip(_CAMERA_AZIMS, _CAMERA_ELEVS, _VIEW_NAMES):
            pos = _hunyuan_to_blender(_get_camera_position_hunyuan(elev, azim, props.camera_distance))

            cam_data = bpy.data.cameras.new(name=f"Hy3D_Camera_{name}")
            cam_data.type        = "ORTHO"
            cam_data.ortho_scale = props.ortho_scale
            cam_data.clip_start  = 0.1
            cam_data.clip_end    = 100.0

            cam_obj = bpy.data.objects.new(f"Hy3D_Camera_{name}", cam_data)
            col.objects.link(cam_obj)
            cam_obj.location = pos

            z_offset = _VIEW_Z_ROT.get(name, 0)

            if props.use_constraints and z_offset == 0:
                c = cam_obj.constraints.new(type="TRACK_TO")
                c.target     = target
                c.track_axis = "TRACK_NEGATIVE_Z"
                c.up_axis    = _VIEW_UP_AXIS.get(name, _DEFAULT_UP)
            else:
                direction = (Vector((0, 0, 0)) - pos).normalized()
                up_ref    = _VIEW_UP_AXIS.get(name, _DEFAULT_UP).replace("UP_", "")
                rot       = direction.to_track_quat("-Z", up_ref).to_euler()
                if z_offset:
                    rot.z += z_offset
                cam_obj.rotation_euler = rot

            cameras.append(cam_obj)

        return cameras

    def _setup_render(self, context, props):
        s = context.scene
        s.render.resolution_x        = props.render_size
        s.render.resolution_y        = props.render_size
        s.render.resolution_percentage = 100
        s.render.film_transparent    = True


# ---------------------------------------------------------------------------
# Operator: Camera Tour
# ---------------------------------------------------------------------------

class NOVELVIEWS_OT_camera_tour(Operator):
    bl_idname  = "novelviews.camera_tour"
    bl_label   = "Create Camera Tour"
    bl_description = (
        "Bake a single animated orthographic camera travelling through all "
        "Hunyuan3D viewpoints with configurable travel speed and waypoint pauses"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = context.scene.novelviews_props
        col   = self._ensure_collection("Hunyuan3D_PathCamera")
        cam   = self._make_camera(context, col, props)
        # optional visual target
        tgt   = bpy.data.objects.new("Hy3D_PathTarget", None)
        tgt.empty_display_type = "SPHERE"
        tgt.empty_display_size = 0.08
        tgt.location = (0, 0, 0)
        col.objects.link(tgt)

        self._bake(context, cam, _TOUR_TRAJECTORY, props)
        self.report({"INFO"}, "Camera tour baked — scrub the timeline to preview")
        return {"FINISHED"}

    # ------------------------------------------------------------------
    def _ensure_collection(self, name):
        if name in bpy.data.collections:
            col = bpy.data.collections[name]
            for obj in list(col.objects):
                bpy.data.objects.remove(obj, do_unlink=True)
            return col
        col = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(col)
        return col

    def _make_camera(self, context, col, props):
        cam_data = bpy.data.cameras.new("Hy3D_PathCam")
        cam_data.type        = "ORTHO"
        cam_data.ortho_scale = props.ortho_scale
        cam_data.clip_start  = 0.1
        cam_data.clip_end    = 100.0
        cam_obj = bpy.data.objects.new("Hy3D_PathCam", cam_data)
        col.objects.link(cam_obj)
        context.scene.camera = cam_obj
        return cam_obj

    # --- math helpers (same as blender_camera_tour.py) ---

    @staticmethod
    def _clamp(x, a, b):
        return max(a, min(b, x))

    def _slerp(self, u, v, t):
        dot   = self._clamp(u.dot(v), -1.0, 1.0)
        omega = math.acos(dot)
        if omega < 1e-9:
            return u.copy()
        so = math.sin(omega)
        return ((math.sin((1.0 - t) * omega) / so) * u +
                (math.sin(t * omega) / so) * v).normalized()

    @staticmethod
    def _rotation_from_forward_up(forward, up):
        f = forward.normalized()
        r = f.cross(up.normalized())
        if r.length < 1e-8:
            alt = Vector((1, 0, 0)) if abs(f.dot(Vector((1, 0, 0)))) < 0.9 else Vector((0, 1, 0))
            r = f.cross(alt)
        r.normalize()
        u        = r.cross(f).normalized()
        backward = (-f).normalized()
        return Matrix((r, u, backward)).transposed().to_quaternion()

    @staticmethod
    def _parallel_transport_up(prev_fwd, fwd, prev_up):
        f = fwd.normalized()
        u = prev_up - f * prev_up.dot(f)
        if u.length < 1e-8:
            pf = prev_fwd.normalized()
            r  = pf.cross(prev_up)
            if r.length < 1e-8:
                r = Vector((1, 0, 0))
            u = r.cross(f)
        return u.normalized()

    def _bake(self, context, cam_obj, trajectory, props):
        scene = context.scene
        fps   = scene.render.fps if scene.render.fps > 0 else 24

        n_seg           = len(trajectory) - 1
        travel_frames   = max(1, int(round(props.travel_seconds * fps)))
        pause_frames    = int(round(props.pause_seconds * fps))

        wp_positions = [_blender_pos_for_view(v, props.camera_distance) for v in trajectory]
        wp_dirs      = [p.normalized() for p in wp_positions]

        angles      = [math.acos(self._clamp(wp_dirs[i].dot(wp_dirs[i+1]), -1, 1)) for i in range(n_seg)]
        total_angle = sum(angles) or 1.0

        seg_frames, remaining = [], travel_frames
        for i, ang in enumerate(angles):
            n = remaining if i == len(angles) - 1 else max(1, int(round(travel_frames * ang / total_angle)))
            seg_frames.append(n)
            remaining -= n

        cam_obj.animation_data_clear()
        cam_obj.rotation_mode = "QUATERNION"

        def key_pose(f, pos, q):
            cam_obj.location            = pos
            cam_obj.rotation_quaternion = q
            cam_obj.keyframe_insert(data_path="location",            frame=f)
            cam_obj.keyframe_insert(data_path="rotation_quaternion", frame=f)

        frame   = 1
        pos     = wp_dirs[0] * props.camera_distance
        forward = (-pos).normalized()
        wz, wy  = Vector((0, 0, 1)), Vector((0, 1, 0))
        init_up = wz if abs(forward.dot(wz)) <= 0.95 else wy
        up      = (init_up - forward * init_up.dot(forward)).normalized()
        q       = self._rotation_from_forward_up(forward, up)
        key_pose(frame, pos, q)
        prev_fwd, prev_up = forward.copy(), up.copy()

        for _ in range(pause_frames):
            frame += 1
            key_pose(frame, pos, q)

        for i in range(n_seg):
            u, v   = wp_dirs[i], wp_dirs[i + 1]
            nframes = seg_frames[i]
            for step in range(1, nframes + 1):
                t       = step / nframes
                d       = self._slerp(u, v, t)
                pos     = d * props.camera_distance
                forward = (-pos).normalized()
                up      = self._parallel_transport_up(prev_fwd, forward, prev_up)
                q       = self._rotation_from_forward_up(forward, up)
                frame  += 1
                key_pose(frame, pos, q)
                prev_fwd, prev_up = forward, up

            for _ in range(pause_frames):
                frame += 1
                key_pose(frame, pos, q)

        scene.frame_start = 1
        scene.frame_end   = frame
        scene.render.film_transparent = True

        if cam_obj.animation_data and cam_obj.animation_data.action:
            for fcu in cam_obj.animation_data.action.fcurves:
                for kp in fcu.keyframe_points:
                    kp.interpolation = "LINEAR"


# ---------------------------------------------------------------------------
# Operator: Normalize Mesh
# ---------------------------------------------------------------------------

class NOVELVIEWS_OT_normalize_mesh(Operator):
    bl_idname  = "novelviews.normalize_mesh"
    bl_label   = "Normalize Mesh"
    bl_description = (
        "Center the active mesh at the origin and scale it so its bounding sphere "
        "matches Hunyuan3D's preprocessing (diameter = Bounding Sphere Diameter)"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = context.scene.novelviews_props
        obj   = context.active_object

        if obj is None:
            self.report({"ERROR"}, "No object selected — please select a mesh first.")
            return {"CANCELLED"}
        if obj.type != "MESH":
            self.report({"ERROR"}, f"'{obj.name}' is not a mesh (type: {obj.type}).")
            return {"CANCELLED"}

        wm       = obj.matrix_world
        verts    = [wm @ v.co for v in obj.data.vertices]
        if not verts:
            self.report({"ERROR"}, "Mesh has no vertices.")
            return {"CANCELLED"}

        min_co = Vector((min(v.x for v in verts), min(v.y for v in verts), min(v.z for v in verts)))
        max_co = Vector((max(v.x for v in verts), max(v.y for v in verts), max(v.z for v in verts)))
        center  = (min_co + max_co) / 2

        max_dist = max((v - center).length for v in verts)
        diameter = max_dist * 2.0
        if diameter == 0:
            self.report({"ERROR"}, "Mesh has zero size.")
            return {"CANCELLED"}

        scale = props.mesh_scale_factor / diameter
        obj.location = obj.location - center
        obj.scale    = obj.scale * scale

        context.view_layer.objects.active = obj
        bpy.ops.object.transform_apply(location=True, rotation=False, scale=True)

        self.report({"INFO"}, (
            f"Normalized '{obj.name}': "
            f"original diameter={diameter:.4f}, scale applied={scale:.4f}, "
            f"target diameter={props.mesh_scale_factor}"
        ))
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Operator: Camera 360 Tour
# ---------------------------------------------------------------------------

class NOVELVIEWS_OT_camera_360_tour(Operator):
    bl_idname  = "novelviews.camera_360_tour"
    bl_label   = "Create 360 Tour"
    bl_description = (
        "Bake a single animated camera orbiting 360° horizontally around the subject "
        "at a fixed elevation. Keyframes loop seamlessly."
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = context.scene.novelviews_props
        col   = self._ensure_collection("Hunyuan3D_360Camera")
        cam   = self._make_camera(context, col, props)
        self._bake(context, cam, props)
        self.report({"INFO"}, "360 tour baked — timeline loops seamlessly")
        return {"FINISHED"}

    def _ensure_collection(self, name):
        if name in bpy.data.collections:
            col = bpy.data.collections[name]
            for obj in list(col.objects):
                bpy.data.objects.remove(obj, do_unlink=True)
            return col
        col = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(col)
        return col

    def _make_camera(self, context, col, props):
        cam_data = bpy.data.cameras.new("Hy3D_360Cam")
        cam_data.type        = "ORTHO"
        cam_data.ortho_scale = props.ortho_scale
        cam_data.clip_start  = 0.1
        cam_data.clip_end    = 100.0
        cam_obj = bpy.data.objects.new("Hy3D_360Cam", cam_data)
        col.objects.link(cam_obj)
        context.scene.camera = cam_obj
        return cam_obj

    def _bake(self, context, cam_obj, props):
        scene = context.scene
        fps   = scene.render.fps if scene.render.fps > 0 else 24
        total_frames = max(2, int(round(props.travel_seconds_360 * fps)))

        cam_obj.animation_data_clear()
        cam_obj.rotation_mode = "QUATERNION"

        elev_rad = math.radians(props.elevation_360)
        radius   = props.camera_distance * math.cos(elev_rad)
        height   = props.camera_distance * math.sin(elev_rad)

        for step in range(total_frames):
            # Orbit directly in Blender space around the world Z axis
            angle = (step / total_frames) * 2 * math.pi - math.pi / 2
            pos   = Vector((radius * math.cos(angle), radius * math.sin(angle), height))

            # Build rotation so camera looks at origin with world +Z as up.
            # to_track_quat("-Z", "Z") is invalid (same axis), so we construct
            # the matrix explicitly:
            #   camera -Z  → forward (toward origin)
            #   camera +Y  → cam_up  (kept = world Z, giving pure yaw, no roll)
            #   camera +X  → right
            forward = (-pos).normalized()
            world_up = Vector((0, 0, 1))
            # Fallback when camera is directly above/below (forward ≈ ±Z)
            if abs(forward.dot(world_up)) > 0.999:
                world_up = Vector((0, 1, 0))
            right   = forward.cross(world_up).normalized()
            cam_up  = right.cross(forward).normalized()
            # Columns of the rotation matrix: right, cam_up, -forward (= local +Z)
            q = Matrix((right, cam_up, (-forward))).transposed().to_quaternion()

            frame = step + 1
            cam_obj.location            = pos
            cam_obj.rotation_quaternion = q
            cam_obj.keyframe_insert(data_path="location",            frame=frame)
            cam_obj.keyframe_insert(data_path="rotation_quaternion", frame=frame)

        scene.frame_start = 1
        scene.frame_end   = total_frames
        scene.render.film_transparent = True

        if cam_obj.animation_data and cam_obj.animation_data.action:
            for fcu in cam_obj.animation_data.action.fcurves:
                for kp in fcu.keyframe_points:
                    kp.interpolation = "LINEAR"
                # Seamless loop: after last frame wrap back to first
                fcu.modifiers.new(type="CYCLES")


# ---------------------------------------------------------------------------
# Operators: Render Passes
# ---------------------------------------------------------------------------

class NOVELVIEWS_OT_setup_render_passes(Operator):
    bl_idname  = "novelviews.setup_render_passes"
    bl_label   = "Setup Compositor"
    bl_description = (
        "Enable the chosen render pass, apply preprocessing (invert, mask), "
        "and wire compositor nodes for the selected output format. "
        "WARNING: clears existing compositor nodes."
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = context.scene.novelviews_props
        scene = context.scene

        if props.render_camera:
            scene.camera = props.render_camera
        elif scene.camera is None:
            self.report({"ERROR"}, "No camera selected and no active scene camera.")
            return {"CANCELLED"}

        # Enable required view-layer passes (always enable alpha for masking)
        vl = context.view_layer
        vl.use_pass_z        = (props.render_pass_type == 'DEPTH')
        vl.use_pass_normal   = (props.render_pass_type == 'NORMAL')
        vl.use_pass_position = (props.render_pass_type == 'POSITION')
        if props.depth_mask_bg:
            scene.render.film_transparent = True  # guarantees alpha is available

        # Build compositor tree
        scene.use_nodes = True
        tree = scene.node_tree
        tree.nodes.clear()

        rl = tree.nodes.new("CompositorNodeRLayers")
        rl.location = (0, 0)

        out_root = bpy.path.abspath(props.render_output_dir)

        if props.render_pass_type == 'DEPTH':
            self._setup_depth(scene, tree, rl, out_root, props)
        elif props.render_pass_type == 'NORMAL':
            self._setup_normal(scene, tree, rl, out_root, props)
        else:
            self._setup_position(scene, tree, rl, out_root, props)

        # Output color management: Override → Display P3 / Raw
        # This is what makes depth/normal output match Hunyuan3D's value range.
        try:
            scene.render.image_settings.color_management = 'OVERRIDE'
            scene.render.image_settings.display_settings.display_device = 'Display P3'
            scene.render.image_settings.view_settings.view_transform = 'Raw'
        except Exception as e:
            self.report({"WARNING"}, f"Could not set color management override: {e}")

        self.report({"INFO"}, f"Compositor ready — output: {out_root}")
        return {"FINISHED"}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _output_node(self, scene, tree, out_root, pass_name, props):
        """Return (node, input_index) for the final output step."""
        if props.render_output_format == 'MP4':
            scene.render.image_settings.file_format = 'FFMPEG'
            scene.render.ffmpeg.format              = 'MPEG4'
            scene.render.ffmpeg.codec               = 'H264'
            scene.render.ffmpeg.constant_rate_factor = 'HIGH'
            scene.render.filepath = os.path.join(out_root, f"{pass_name}.mp4")
            node = tree.nodes.new("CompositorNodeComposite")
            node.label = f"{pass_name.title()} Output"
            return node, 0   # "Image" input
        else:
            node = tree.nodes.new("CompositorNodeOutputFile")
            node.label     = f"{pass_name.title()} Output"
            node.base_path = os.path.join(out_root, pass_name)
            node.file_slots[0].path = f"{pass_name}_"
            return node, 0   # first file slot

    def _mask_node(self, tree, x):
        """MULTIPLY by alpha → background becomes black (0). Used for depth."""
        m = tree.nodes.new("CompositorNodeMixRGB")
        m.blend_type = 'MULTIPLY'
        m.inputs[0].default_value = 1.0
        m.location = (x, 0)
        return m

    def _white_bg_mask_node(self, tree, x):
        """MIX with alpha as factor, white as color1 → background becomes white.
        Usage: link alpha → inputs[0], computed → inputs[2]; white is inputs[1]."""
        m = tree.nodes.new("CompositorNodeMixRGB")
        m.blend_type = 'MIX'
        m.inputs[1].default_value = (1.0, 1.0, 1.0, 1.0)  # white background
        m.location = (x, 0)
        return m

    def _setup_depth(self, scene, tree, rl, out_root, props):
        x = 260

        # Per-frame normalization within the masked region only.
        # Double-normalize trick — equivalent to Hunyuan3D's masked min-max:
        #
        #   Pass 1:  Z × alpha  →  Normalize  →  Invert  →  × alpha
        #     • masking before normalize excludes background from the range
        #     • invert + re-mask brings background back to 0 (it became 1 after invert)
        #   Pass 2:  Normalize
        #     • stretches the remaining foreground range to [0, 1]
        #     • result: near = white (1), far = black (0), background = black (0)

        # Normalize raw Z to 0-1 using object bounds derived from camera & mesh settings.
        # near/far computed from camera_distance ± mesh_radius so that only the
        # foreground object spans the full [0,1] range — background gets clamped to 1.
        mesh_radius = props.mesh_scale_factor / 2.0
        near = props.camera_distance - mesh_radius
        far  = props.camera_distance + mesh_radius

        mv = tree.nodes.new("CompositorNodeMapValue")
        mv.offset[0] = -near
        mv.size[0]   = 1.0 / (far - near)
        mv.use_min   = True
        mv.use_max   = True
        mv.min[0]    = 0.0
        mv.max[0]    = 1.0
        mv.location  = (x, 0)
        tree.links.new(rl.outputs["Depth"], mv.inputs[0])
        prev = mv.outputs[0]
        x += 220

        # Invert: 1 - depth  (near=white, far=black)
        if props.depth_invert:
            inv = tree.nodes.new("CompositorNodeMath")
            inv.operation = 'SUBTRACT'
            inv.inputs[0].default_value = 1.0
            inv.location = (x, 0)
            tree.links.new(prev, inv.inputs[1])
            prev = inv.outputs[0]
            x += 220

        # Mask background to 0
        if props.depth_mask_bg:
            mask = tree.nodes.new("CompositorNodeMath")
            mask.operation = 'MULTIPLY'
            mask.location = (x, 0)
            tree.links.new(prev,                mask.inputs[0])
            tree.links.new(rl.outputs["Alpha"], mask.inputs[1])
            prev = mask.outputs[0]
            x += 220

        # Output
        out, slot = self._output_node(scene, tree, out_root, "depth", props)
        out.location = (x, 0)
        if props.render_output_format == 'IMAGE_SEQ':
            out.format.file_format = 'PNG'
            out.format.color_mode  = 'BW'
            out.format.color_depth = '16'
        tree.links.new(prev, out.inputs[slot])

    def _setup_normal(self, scene, tree, rl, out_root, props):
        x = 260

        # 1. Remap camera-space normals −1..1 → 0..1  (mul * 0.5 + 0.5)
        mul = tree.nodes.new("CompositorNodeMixRGB")
        mul.blend_type = 'MULTIPLY'
        mul.inputs[0].default_value = 1.0
        mul.inputs[2].default_value = (0.5, 0.5, 0.5, 1.0)
        mul.location = (x, 0)
        tree.links.new(rl.outputs["Normal"], mul.inputs[1])
        x += 220

        add = tree.nodes.new("CompositorNodeMixRGB")
        add.blend_type = 'ADD'
        add.inputs[0].default_value = 1.0
        add.inputs[2].default_value = (0.5, 0.5, 0.5, 1.0)
        add.location = (x, 0)
        tree.links.new(mul.outputs[0], add.inputs[1])
        prev = add.outputs[0]
        x += 220

        # 2. White background: mix computed normals with white using alpha as factor
        if props.depth_mask_bg:
            mask = self._white_bg_mask_node(tree, x)
            tree.links.new(rl.outputs["Alpha"], mask.inputs[0])  # factor
            tree.links.new(prev,               mask.inputs[2])  # foreground
            prev = mask.outputs[0]
            x += 220

        # 3. Output
        out, slot = self._output_node(scene, tree, out_root, "normal", props)
        out.location = (x, 0)
        if props.render_output_format == 'IMAGE_SEQ':
            out.format.file_format = 'PNG'
            out.format.color_mode  = 'RGB'
            out.format.color_depth = '8'
        tree.links.new(prev, out.inputs[slot])

    def _setup_position(self, scene, tree, rl, out_root, props):
        x = 260

        # Remap world-space positions to 0-1.
        # Mesh is normalized to radius = scale_factor/2, so positions lie in
        # [-scale_factor/2, scale_factor/2] per axis.
        # Formula: pos / scale_factor + 0.5  →  maps [-0.575, 0.575] → [0, 1]
        inv_s = 1.0 / props.mesh_scale_factor

        mul = tree.nodes.new("CompositorNodeMixRGB")
        mul.blend_type = 'MULTIPLY'
        mul.inputs[0].default_value = 1.0
        mul.inputs[2].default_value = (inv_s, inv_s, inv_s, 1.0)
        mul.location = (x, 0)
        tree.links.new(rl.outputs["Position"], mul.inputs[1])
        x += 220

        add = tree.nodes.new("CompositorNodeMixRGB")
        add.blend_type = 'ADD'
        add.inputs[0].default_value = 1.0
        add.inputs[2].default_value = (0.5, 0.5, 0.5, 1.0)
        add.location = (x, 0)
        tree.links.new(mul.outputs[0], add.inputs[1])
        prev = add.outputs[0]
        x += 220

        # White background: mix computed positions with white using alpha as factor
        mask = self._white_bg_mask_node(tree, x)
        tree.links.new(rl.outputs["Alpha"], mask.inputs[0])  # factor
        tree.links.new(prev,               mask.inputs[2])   # foreground
        prev = mask.outputs[0]
        x += 220

        # Output
        out, slot = self._output_node(scene, tree, out_root, "position", props)
        out.location = (x, 0)
        if props.render_output_format == 'IMAGE_SEQ':
            out.format.file_format = 'PNG'
            out.format.color_mode  = 'RGB'
            out.format.color_depth = '8'
        tree.links.new(prev, out.inputs[slot])


class NOVELVIEWS_OT_render_preview(Operator):
    bl_idname  = "novelviews.render_preview"
    bl_label   = "Preview Frame"
    bl_description = (
        "Render the current frame to check the compositor output. "
        "In MP4 mode a PNG preview is saved next to the video path."
    )
    bl_options = {"REGISTER"}

    def execute(self, context):
        props = context.scene.novelviews_props
        scene = context.scene

        if props.render_output_format == 'MP4':
            # Temporarily switch to PNG so a single frame is saved cleanly
            orig_format = scene.render.image_settings.file_format
            orig_path   = scene.render.filepath
            scene.render.image_settings.file_format = 'PNG'
            pass_name   = "depth" if props.render_pass_type == 'DEPTH' else "normal"
            out_root    = bpy.path.abspath(props.render_output_dir)
            scene.render.filepath = os.path.join(out_root, f"{pass_name}_preview.png")
            bpy.ops.render.render(animation=False, write_still=True)
            scene.render.image_settings.file_format = orig_format
            scene.render.filepath                   = orig_path
        else:
            bpy.ops.render.render(animation=False, write_still=True)

        return {"FINISHED"}


class NOVELVIEWS_OT_render_animation(Operator):
    bl_idname  = "novelviews.render_animation"
    bl_label   = "Render Animation"
    bl_description = "Render the full animation timeline and write all frames / the video to the output folder"
    bl_options = {"REGISTER"}

    def execute(self, context):
        bpy.ops.render.render(animation=True)
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Panel
# ---------------------------------------------------------------------------

class NOVELVIEWS_PT_main_panel(Panel):
    bl_label      = "NovelViews Assets"
    bl_idname     = "NOVELVIEWS_PT_main_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category   = "NovelViews"

    def draw(self, context):
        layout = self.layout
        props  = context.scene.novelviews_props

        # ---- Camera Setup ----
        box = layout.box()
        row = box.row()
        row.prop(
            props, "camera_setup_expanded",
            icon="TRIA_DOWN" if props.camera_setup_expanded else "TRIA_RIGHT",
            icon_only=True, emboss=False,
        )
        row.label(text="Camera Setup", icon="CAMERA_DATA")
        if props.camera_setup_expanded:
            col = box.column(align=True)
            col.prop(props, "camera_distance")
            col.prop(props, "ortho_scale")
            col.prop(props, "render_size")
            col.prop(props, "use_constraints")
            box.operator("novelviews.setup_cameras", icon="SCENE")

        # ---- Camera Tour ----
        box = layout.box()
        row = box.row()
        row.prop(
            props, "camera_tour_expanded",
            icon="TRIA_DOWN" if props.camera_tour_expanded else "TRIA_RIGHT",
            icon_only=True, emboss=False,
        )
        row.label(text="Camera Spherical Tour", icon="ANIM")
        if props.camera_tour_expanded:
            col = box.column(align=True)
            col.prop(props, "travel_seconds")
            col.prop(props, "pause_seconds")
            box.operator("novelviews.camera_tour", icon="PLAY")

        # ---- Camera 360 Tour ----
        box = layout.box()
        row = box.row()
        row.prop(
            props, "camera_360_expanded",
            icon="TRIA_DOWN" if props.camera_360_expanded else "TRIA_RIGHT",
            icon_only=True, emboss=False,
        )
        row.label(text="Camera 360 Tour", icon="SPHERECURVE")
        if props.camera_360_expanded:
            col = box.column(align=True)
            col.prop(props, "elevation_360")
            col.prop(props, "travel_seconds_360")
            box.operator("novelviews.camera_360_tour", icon="PLAY")

        # ---- Normalize Mesh ----
        box = layout.box()
        row = box.row()
        row.prop(
            props, "normalize_mesh_expanded",
            icon="TRIA_DOWN" if props.normalize_mesh_expanded else "TRIA_RIGHT",
            icon_only=True, emboss=False,
        )
        row.label(text="Normalize Mesh", icon="MESH_DATA")
        if props.normalize_mesh_expanded:
            col = box.column(align=True)
            col.prop(props, "mesh_scale_factor")
            box.operator("novelviews.normalize_mesh", icon="MODIFIER")

        # ---- Render Passes ----
        box = layout.box()
        row = box.row()
        row.prop(
            props, "render_passes_expanded",
            icon="TRIA_DOWN" if props.render_passes_expanded else "TRIA_RIGHT",
            icon_only=True, emboss=False,
        )
        row.label(text="Render Passes", icon="RENDER_ANIMATION")
        if props.render_passes_expanded:
            col = box.column(align=True)
            col.prop(props, "render_camera")
            col.separator()
            col.label(text="Pass:")
            col.row().prop(props, "render_pass_type", expand=True)
            col.separator()
            if props.render_pass_type == 'DEPTH':
                col.prop(props, "depth_invert")
            if props.render_pass_type != 'POSITION':
                col.prop(props, "depth_mask_bg")
            col.separator()
            col.label(text="Output Format:")
            col.row().prop(props, "render_output_format", expand=True)
            col.prop(props, "render_output_dir")
            box.operator("novelviews.setup_render_passes", icon="NODETREE")
            row2 = box.row(align=True)
            row2.operator("novelviews.render_preview",   icon="RESTRICT_RENDER_OFF")
            row2.operator("novelviews.render_animation", icon="RENDER_ANIMATION")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

_classes = [
    NovelViewsProperties,
    NOVELVIEWS_OT_setup_cameras,
    NOVELVIEWS_OT_camera_tour,
    NOVELVIEWS_OT_camera_360_tour,
    NOVELVIEWS_OT_normalize_mesh,
    NOVELVIEWS_OT_setup_render_passes,
    NOVELVIEWS_OT_render_preview,
    NOVELVIEWS_OT_render_animation,
    NOVELVIEWS_PT_main_panel,
]


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.novelviews_props = PointerProperty(type=NovelViewsProperties)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.novelviews_props


if __name__ == "__main__":
    register()
