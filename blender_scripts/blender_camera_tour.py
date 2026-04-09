"""
Seamless single-camera trajectory through Hy3D multiview viewpoints
with pauses added ON TOP of a fixed 5s travel (120 frames @ 24fps).

Trajectory:
front -> front_left -> left -> back -> right -> front_right -> front -> top -> back -> bottom

- Travel duration is fixed (TRAVEL_SECONDS).
- Pauses increase total duration.
- Fixed radius (exact).
- Parallel-transported up (no 90° roll flips).
- Baked per frame, linear interpolation.

Usage:
- Paste in Blender Scripting, Run.
- Scrub timeline.
"""

import bpy
import math
from mathutils import Vector, Matrix, Quaternion

# =========================
# PARAMETERS
# =========================
CAMERA_DISTANCE = 1.45
ORTHO_SCALE = 1.2

TRAVEL_SECONDS = 5.0     # movement time only (e.g. 5s -> 120 frames @ 24fps)
PAUSE_SECONDS = 0.2     # pause at EACH waypoint, added on top of travel time

START_AT_CURRENT_FRAME = False
KEY_EVERY_N_FRAMES = 1   # keep 1 for maximum smoothness

# =========================
# Hy3D view definitions (matching your validated mapping)
# =========================
VIEW_TO_AZIM_ELEV = {
    "top":         (0,    0),
    "right":       (90,   0),
    "bottom":      (180,  0),
    "left":        (270,  0),
    "back":        (0,   90),
    "front":       (180, -90),
    "front_left":  (270, -45),
    "front_right": (90,  -45),
}

TRAJECTORY = [
    "front",
    "front_left",
    "left",
    "back",
    "right",
    "front_right",
    "front",
    "top",
    "back",
    "bottom",
]

# =========================
# Hy3D camera position math
# =========================
def get_camera_position_hunyuan(elev, azim, camera_distance):
    elev_transformed = -elev
    azim_transformed = azim + 90

    elev_rad = math.radians(elev_transformed)
    azim_rad = math.radians(azim_transformed)

    x = camera_distance * math.cos(elev_rad) * math.cos(azim_rad)
    y = camera_distance * math.cos(elev_rad) * math.sin(azim_rad)
    z = camera_distance * math.sin(elev_rad)

    return Vector((x, y, z))

def hunyuan_to_blender_position(pos):
    # Inverse camera transform for Hy3D mesh transform (-X, Z, -Y)
    return Vector((-pos.x, -pos.z, pos.y))

def blender_pos_for_view(view_name):
    azim, elev = VIEW_TO_AZIM_ELEV[view_name]
    pos_hy = get_camera_position_hunyuan(elev, azim, CAMERA_DISTANCE)
    return hunyuan_to_blender_position(pos_hy)

# =========================
# Math helpers
# =========================
def clamp(x, a, b):
    return max(a, min(b, x))

def slerp(u: Vector, v: Vector, t: float) -> Vector:
    dot = clamp(u.dot(v), -1.0, 1.0)
    omega = math.acos(dot)
    if omega < 1e-9:
        return u.copy()
    so = math.sin(omega)
    a = math.sin((1.0 - t) * omega) / so
    b = math.sin(t * omega) / so
    return (a * u + b * v).normalized()

def rotation_from_forward_up(forward: Vector, up: Vector) -> Quaternion:
    f = forward.normalized()
    u = up.normalized()

    r = f.cross(u)
    if r.length < 1e-8:
        alt = Vector((1, 0, 0)) if abs(f.dot(Vector((1, 0, 0)))) < 0.9 else Vector((0, 1, 0))
        r = f.cross(alt)
    r.normalize()
    u = r.cross(f).normalized()

    backward = (-f).normalized()
    m = Matrix((r, u, backward)).transposed()
    return m.to_quaternion()

def parallel_transport_up(prev_forward: Vector, forward: Vector, prev_up: Vector) -> Vector:
    f = forward.normalized()
    u = prev_up - f * prev_up.dot(f)
    if u.length < 1e-8:
        pf = prev_forward.normalized()
        prev_right = pf.cross(prev_up)
        if prev_right.length < 1e-8:
            prev_right = Vector((1, 0, 0))
        u = prev_right.cross(f)
    return u.normalized()

# =========================
# Scene / object creation
# =========================
def ensure_collection(name: str):
    if name in bpy.data.collections:
        col = bpy.data.collections[name]
        for obj in list(col.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        return col
    col = bpy.data.collections.new(name)
    bpy.context.scene.collection.children.link(col)
    return col

def create_camera(col, name="Hy3D_PathCam"):
    cam_data = bpy.data.cameras.new(name=name)
    cam_data.type = 'ORTHO'
    cam_data.ortho_scale = ORTHO_SCALE
    cam_data.clip_start = 0.1
    cam_data.clip_end = 100.0

    cam_obj = bpy.data.objects.new(name, cam_data)
    col.objects.link(cam_obj)
    bpy.context.scene.camera = cam_obj
    return cam_obj

def create_target_empty(col, name="Hy3D_PathTarget"):
    target = bpy.data.objects.new(name, None)
    target.empty_display_type = 'SPHERE'
    target.empty_display_size = 0.08
    target.location = (0, 0, 0)
    col.objects.link(target)
    return target

# =========================
# Animation baking with pauses added on top
# =========================
def bake_camera_motion_with_pauses(cam_obj, trajectory_names, travel_seconds, pause_seconds):
    scene = bpy.context.scene
    fps = scene.render.fps if scene.render.fps > 0 else 24

    n_waypoints = len(trajectory_names)
    n_segments = n_waypoints - 1

    travel_frames_total = max(1, int(round(travel_seconds * fps)))
    pause_frames = int(round(pause_seconds * fps))
    total_pause_frames = pause_frames * n_waypoints  # pause at each waypoint

    start_frame = scene.frame_current if START_AT_CURRENT_FRAME else 1

    # Waypoint unit directions
    waypoint_positions = [blender_pos_for_view(v) for v in trajectory_names]
    waypoint_dirs = [p.normalized() for p in waypoint_positions]

    # Segment angles for natural time allocation across travel frames
    angles = []
    for i in range(n_segments):
        dot = clamp(waypoint_dirs[i].dot(waypoint_dirs[i+1]), -1.0, 1.0)
        angles.append(math.acos(dot))
    total_angle = sum(angles) if sum(angles) > 1e-9 else 1.0

    # Allocate travel frames per segment proportional to angle
    seg_frames = []
    remaining = travel_frames_total
    for i, ang in enumerate(angles):
        if i == len(angles) - 1:
            n = remaining
        else:
            n = max(1, int(round(travel_frames_total * (ang / total_angle))))
            remaining -= n
        seg_frames.append(n)

    # Clear animation
    cam_obj.animation_data_clear()
    cam_obj.rotation_mode = 'QUATERNION'

    def key_pose(frame_idx, pos_vec, qrot):
        cam_obj.location = pos_vec
        cam_obj.rotation_quaternion = qrot
        cam_obj.keyframe_insert(data_path="location", frame=frame_idx)
        cam_obj.keyframe_insert(data_path="rotation_quaternion", frame=frame_idx)

    # Initialize at first waypoint
    frame = start_frame
    pos = waypoint_dirs[0] * CAMERA_DISTANCE
    forward = (-pos).normalized()

    # Stable initial up ref
    world_up_z = Vector((0, 0, 1))
    world_up_y = Vector((0, 1, 0))
    init_up = world_up_z if abs(forward.dot(world_up_z)) <= 0.95 else world_up_y
    up = (init_up - forward * init_up.dot(forward)).normalized()

    q = rotation_from_forward_up(forward, up)
    key_pose(frame, pos, q)

    prev_forward = forward.copy()
    prev_up = up.copy()

    # Pause at first waypoint
    for _ in range(pause_frames):
        frame += 1
        key_pose(frame, pos, q)

    # Travel each segment + pause at waypoint
    for i in range(n_segments):
        u = waypoint_dirs[i]
        v = waypoint_dirs[i + 1]
        nframes = seg_frames[i]

        for step in range(1, nframes + 1):
            if (step % KEY_EVERY_N_FRAMES) != 0 and step != nframes:
                continue

            t = step / nframes
            d = slerp(u, v, t)
            pos = d * CAMERA_DISTANCE

            forward = (-pos).normalized()
            up = parallel_transport_up(prev_forward, forward, prev_up)
            q = rotation_from_forward_up(forward, up)

            frame += 1
            key_pose(frame, pos, q)

            prev_forward = forward
            prev_up = up

        # Pause at the reached waypoint
        for _ in range(pause_frames):
            frame += 1
            key_pose(frame, pos, q)

    # Set timeline bounds
    scene.frame_start = start_frame
    scene.frame_end = frame

    # Linear interpolation everywhere (no overshoot)
    if cam_obj.animation_data and cam_obj.animation_data.action:
        for fcu in cam_obj.animation_data.action.fcurves:
            for kp in fcu.keyframe_points:
                kp.interpolation = 'LINEAR'

    scene.render.film_transparent = True

    total_seconds = (scene.frame_end - scene.frame_start) / fps

    print("=" * 70)
    print("DONE: Seamless Hy3D path camera baked WITH pauses added on top.")
    print(f"FPS: {fps}")
    print(f"Travel: {travel_seconds:.2f}s  (~{travel_frames_total} frames)")
    print(f"Pause per waypoint: {pause_seconds:.3f}s (~{pause_frames} frames) x {n_waypoints} = {total_pause_frames} frames")
    print(f"Total length: {total_seconds:.2f}s  (frames {scene.frame_start} -> {scene.frame_end})")
    print("Trajectory:", " -> ".join(trajectory_names))
    print("=" * 70)

# =========================
# RUN
# =========================
def main():
    col = ensure_collection("Hunyuan3D_PathCamera_Seamless_PausesPlus")
    cam = create_camera(col, "Hy3D_PathCam")
    create_target_empty(col, "Hy3D_PathTarget")  # optional visual cue

    bake_camera_motion_with_pauses(cam, TRAJECTORY, TRAVEL_SECONDS, PAUSE_SECONDS)

if __name__ == "__main__":
    main()
