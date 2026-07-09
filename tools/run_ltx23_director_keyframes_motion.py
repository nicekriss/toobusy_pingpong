import json
import time
import urllib.error
import urllib.request
from pathlib import Path


COMFY = "http://127.0.0.1:8188"
BASE_WORKFLOW = Path(r"D:\working\pingpong\workflows\LTX23_pose_control_video_dwpose_API.json")
OUT_WORKFLOW = Path(r"D:\working\pingpong\workflows\video_ltx23_director_keyframes_motion_hook_2stage_API.json")

KEYFRAMES = [
    (0, "hook_kf_00000.png", 0.92, 0.62),
    (13, "hook_kf_00013.png", 0.78, 0.48),
    (26, "hook_kf_00026.png", 0.78, 0.48),
    (40, "hook_kf_00040.png", 0.80, 0.50),
    (53, "hook_kf_00053.png", 0.80, 0.50),
    (66, "hook_kf_00066.png", 0.80, 0.50),
    (80, "hook_kf_00080.png", 0.92, 0.62),
]


PROMPT = (
    "A spectacular cinematic K-pop YouTube opening hook in 16:9. "
    "Exactly one handsome Korean male solo performer runs toward the camera on a glossy concert runway, "
    "then time slows down while the camera orbits around him. Preserve the camera motion and body pose from the guide video. "
    "Use the provided keyframes as exact visual anchors for composition, outfit direction, lighting, and shot continuity. "
    "Keep one main performer only, no backup dancers, no duplicated foreground performer, no insects, no readable text. "
    "Emerald green luxury stagewear, heroic expression, dramatic smoke, rim light, bokeh concert crowd, premium music video, coherent motion."
)

NEGATIVE = (
    "multiple main performers, duplicate foreground person, backup dancers, crowd in foreground, extra person next to subject, "
    "split body, cloned face, deformed face, bad hands, unstable identity, flicker, readable text, subtitles, watermark, logo, "
    "insect, grasshopper, bug costume, monster, low quality"
)


def request_json(method: str, path: str, payload=None, timeout=30):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{COMFY}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return json.loads(raw.decode("utf-8")) if raw else {}


def add_load_image(workflow, node_id: int, image: str):
    workflow[str(node_id)] = {
        "inputs": {"image": image},
        "class_type": "LoadImage",
        "_meta": {"title": f"KEYFRAME {image}"},
    }


def add_keyframe_guide(workflow, node_id: int, source, load_id: int, frame_idx: int, strength: float):
    workflow[str(node_id)] = {
        "inputs": {
            "positive": [str(source), 0],
            "negative": [str(source), 1],
            "vae": ["5", 0],
            "latent": [str(source), 2],
            "image": [str(load_id), 0],
            "frame_idx": frame_idx,
            "strength": strength,
            "crf": 29,
            "blur_radius": 0,
            "interpolation": "lanczos",
            "crop": "center",
            "attention_strength": 0.88,
        },
        "class_type": "LTXVAddGuideAdvancedAttention",
        "_meta": {"title": f"Director keyframe guide f{frame_idx:03d}"},
    }


def build_workflow():
    workflow = json.loads(BASE_WORKFLOW.read_text(encoding="utf-8"))

    workflow["7"]["inputs"]["prompt"] = PROMPT
    workflow["7"]["inputs"]["negative_prompt"] = NEGATIVE
    workflow["7"]["inputs"]["duration_seconds"] = 3.375
    workflow["7"]["inputs"]["frame_rate"] = 24

    workflow["8"]["inputs"]["megapixels"] = 0.5
    workflow["9"]["inputs"]["video"] = "ltx23_camera_run_slow_orbit_previs_fast_div64_00001_.mp4"
    workflow["9"]["inputs"]["force_rate"] = 24
    workflow["9"]["inputs"]["format"] = "LTXV"
    workflow["10"]["inputs"]["resolution"] = 768
    workflow["11"]["class_type"] = "DepthAnythingV2Preprocessor"
    workflow["11"]["inputs"] = {
        "image": ["9", 0],
        "ckpt_name": "depth_anything_v2_vitl.pth",
        "resolution": 1024,
    }

    # Main control line: use the same depth-control frames that drove the KREA2 pass.
    # Do not feed RGB source or DWPose skeleton here; this path is for IC Control Union.
    workflow["14"]["inputs"]["image"] = ["11", 0]
    workflow["14"]["inputs"]["strength"] = 0.25
    workflow["14"]["inputs"]["latent_downscale_factor"] = 1.0
    workflow["25"]["inputs"]["image"] = ["11", 0]
    workflow["25"]["inputs"]["strength"] = 0.14
    workflow["25"]["inputs"]["latent_downscale_factor"] = 1.0
    workflow["35"]["inputs"]["filename_prefix"] = "video/ltx23_director_depthcontrol_keyframes_hook_balanced"

    # Stage 1: motion/pose guide first, then sparse image keyframes as timeline anchors.
    previous = 14
    next_id = 50
    for frame_idx, image, strength1, _strength2 in KEYFRAMES:
        load_id = next_id
        guide_id = next_id + 1
        add_load_image(workflow, load_id, image)
        add_keyframe_guide(workflow, guide_id, previous, load_id, frame_idx, strength1)
        previous = guide_id
        next_id += 2
    stage1_last = previous
    workflow["15"]["inputs"]["video_latent"] = [str(stage1_last), 2]
    workflow["16"]["inputs"]["positive"] = [str(stage1_last), 0]
    workflow["16"]["inputs"]["negative"] = [str(stage1_last), 1]
    workflow["22"]["inputs"]["positive"] = [str(stage1_last), 0]
    workflow["22"]["inputs"]["negative"] = [str(stage1_last), 1]

    # Stage 2: repeat the same anchors more gently after latent upsample.
    previous = 25
    for frame_idx, image, _strength1, strength2 in KEYFRAMES:
        load_id = next_id
        guide_id = next_id + 1
        add_load_image(workflow, load_id, image)
        add_keyframe_guide(workflow, guide_id, previous, load_id, frame_idx, strength2)
        previous = guide_id
        next_id += 2
    stage2_last = previous
    workflow["26"]["inputs"]["video_latent"] = [str(stage2_last), 2]
    workflow["27"]["inputs"]["positive"] = [str(stage2_last), 0]
    workflow["27"]["inputs"]["negative"] = [str(stage2_last), 1]
    workflow["32"]["inputs"]["positive"] = [str(stage2_last), 0]
    workflow["32"]["inputs"]["negative"] = [str(stage2_last), 1]

    return workflow


def queue_and_wait(workflow):
    payload = {"prompt": workflow, "client_id": "codex_ltx_director_keyframes"}
    queued = request_json("POST", "/prompt", payload, timeout=60)
    prompt_id = queued["prompt_id"]
    print(f"queued {prompt_id}")

    while True:
        history = request_json("GET", f"/history/{prompt_id}", timeout=30)
        if prompt_id in history:
            return prompt_id, history[prompt_id]
        queue = request_json("GET", "/queue", timeout=30)
        running = len(queue.get("queue_running", []))
        pending = len(queue.get("queue_pending", []))
        print(f"waiting running={running} pending={pending}")
        time.sleep(10)


def main():
    workflow = build_workflow()
    OUT_WORKFLOW.write_text(json.dumps(workflow, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved workflow: {OUT_WORKFLOW}")

    prompt_id, history = queue_and_wait(workflow)
    outputs = history.get("outputs", {})
    print(f"finished {prompt_id}")
    print(json.dumps(outputs, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as e:
        print(e.read().decode("utf-8", errors="replace"))
        raise
