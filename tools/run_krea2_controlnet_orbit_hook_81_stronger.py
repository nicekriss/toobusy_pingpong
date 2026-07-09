import argparse
import json
import shutil
import time
import urllib.request
import uuid
from pathlib import Path


COMFY = "http://127.0.0.1:8188"
INPUT_ROOT = Path(r"C:\Users\kriss\AppData\Local\Comfy-Desktop\ComfyUI-Shared\input")
OUTPUT_ROOT = Path(r"C:\Users\kriss\AppData\Local\Comfy-Desktop\ComfyUI-Shared\output")
WORKFLOW_TEMPLATE = Path(r"D:\working\pingpong\workflows\image_krea2_controlnet_orbit_hook_81_stronger_2k_API_template.json")


FRAMES_DIR = INPUT_ROOT / "krea2_hook_orbit_frames_81src"
ORDERED_DIR = OUTPUT_ROOT / "krea2_hook_orbit_controlnet_81_stronger_2k_ordered"
VIDEO_OUT = OUTPUT_ROOT / "video" / "krea2_hook_orbit_controlnet_81_stronger_2k_24fps.mp4"


SEGMENTS = [
    {
        "end": 13,
        "lora": r"KREA2_TOP7\krea2_neondrip.safetensors",
        "strength": 0.38,
        "prompt": "Same single Korean male performer, same concert runway, same camera run toward lens, emerald chrome streetwear, neon cyan and magenta rim lights, clean luxury K-pop music video, confident heroic expression, no text",
    },
    {
        "end": 26,
        "lora": r"KREA2_TOP7\krea2_darkbrush.safetensors",
        "strength": 0.34,
        "prompt": "Same single Korean male performer, same concert runway and camera motion, black and emerald leather outfit, dark cinematic brush texture only in lighting and background haze, dramatic smoke, controlled stage spotlights, no text",
    },
    {
        "end": 39,
        "lora": r"KREA2_TOP7\krea2_darkbrush.safetensors",
        "strength": 0.22,
        "prompt": "A solo close-camera Korean male performer only, same concert runway and same running pose, emerald futuristic jacket, subtle graphic music-video color grading, crisp edge highlights, cinematic realism preserved, empty runway around the performer, no other performers, no text",
    },
    {
        "end": 52,
        "lora": r"KREA2_TOP7\krea2_neondrip.safetensors",
        "strength": 0.22,
        "prompt": "A solo close-camera Korean male performer only, same concert runway and same camera orbit, emerald and gold couture details, abstract warm spotlights, clean luxury stage, black empty background panels with no faces, empty runway around the performer, no other performers, no text",
    },
    {
        "end": 65,
        "lora": r"KREA2_TOP7\krea2_realism_lora.safetensors",
        "strength": 0.20,
        "prompt": "A solo close-camera Korean male performer only, same camera orbit and running silhouette, sharp emerald tailored suit, wet reflective runway, realistic skin, strong eye contact, smoke and backlight, abstract stage lights only, no LED screen portraits, empty runway around the performer, no other performers, no text",
    },
    {
        "end": 10_000,
        "lora": r"KREA2_TOP7\krea2_sunsetblur.safetensors",
        "strength": 0.34,
        "prompt": "Same single Korean male performer, same stage and camera orbit, emerald silk outfit with silver accessories, warm gold rim light mixed with neon concert lights, subtle confetti in background, euphoric final hook frame, no text",
    },
]


SOLO_LOCK = (
    "The final image contains exactly one human figure: the main Korean male performer. "
    "All background areas are empty stage architecture, fog, abstract bokeh lights, and soft color dots only. "
    "LED panels and reflections show abstract light patterns only, never faces, never bodies, never another performer. "
    "The runway around the performer is clear and empty. "
)


def request_json(path: str, payload: dict | None = None, timeout: int = 30) -> dict:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(COMFY + path, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        text = response.read().decode("utf-8")
    return json.loads(text) if text else {}


def free_memory() -> None:
    try:
        request_json("/free", {"unload_models": True, "free_memory": True}, timeout=10)
    except Exception:
        pass


def select_segment(frame_index: int) -> dict:
    for segment in SEGMENTS:
        if frame_index <= segment["end"]:
            return segment
    return SEGMENTS[-1]


def rel_input(path: Path) -> str:
    return path.relative_to(INPUT_ROOT).as_posix()


def build_workflow(frame_index: int, frame_path: Path, pilot: bool = False) -> dict:
    segment = select_segment(frame_index)
    prompt = (
        segment["prompt"]
        + ", preserve the source frame depth, body pose, camera angle and orbit perspective, "
        + SOLO_LOCK
        + "keep the same physical scene continuity across frames, change only lighting, costume texture and color grading, "
        + "turn it into a spectacular YouTube opening hook frame, coherent with adjacent frames"
    )
    if pilot:
        prompt += ", extra clean test render"

    seed = 87420000 + frame_index
    prefix = f"krea2_hook_orbit_controlnet_81_stronger_2k/frame_{frame_index:05d}"

    workflow = {
        "1": {"class_type": "LoadImage", "inputs": {"image": rel_input(frame_path)}},
        "2": {
            "class_type": "DepthAnythingV2Preprocessor",
            "inputs": {"image": ["1", 0], "ckpt_name": "depth_anything_v2_vitl.pth", "resolution": 1024},
        },
        "3": {"class_type": "UNETLoader", "inputs": {"unet_name": "krea2_turbo_fp8_scaled.safetensors", "weight_dtype": "default"}},
        "4": {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {"model": ["3", 0], "lora_name": segment["lora"], "strength_model": segment["strength"]},
        },
        "5": {
            "class_type": "Krea2ControlLoRALoader",
            "inputs": {
                "model": ["4", 0],
                "lora_name": r"KREA2_ControlNet\krea2_depth_control_lora.safetensors",
                "strength": 1.05,
            },
        },
        "6": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen3vl_4b_fp8_scaled.safetensors", "type": "krea2", "device": "default"}},
        "7": {"class_type": "VAELoader", "inputs": {"vae_name": "qwen_image_vae.safetensors"}},
        "8": {"class_type": "EmptyLatentImage", "inputs": {"width": 1024, "height": 552, "batch_size": 1}},
        "9": {
            "class_type": "Krea2ControlImageEncode",
            "inputs": {
                "control_image": ["2", 0],
                "vae": ["7", 0],
                "resize": "match_latent_size",
                "upscale_method": "lanczos",
                "crop": "center",
                "channel_mode": "grayscale",
                "normalize": "per_image_minmax",
                "invert": False,
                "batch_mode": "independent_images",
                "latent": ["8", 0],
            },
        },
        "10": {"class_type": "Krea2ControlApply", "inputs": {"model": ["5", 0], "control_latent": ["9", 0]}},
        "11": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["6", 0]}},
        "12": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["11", 0]}},
        "13": {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed,
                "steps": 8,
                "cfg": 1,
                "sampler_name": "euler",
                "scheduler": "simple",
                "denoise": 1,
                "model": ["10", 0],
                "positive": ["11", 0],
                "negative": ["12", 0],
                "latent_image": ["8", 0],
            },
        },
        "14": {"class_type": "VAEDecode", "inputs": {"samples": ["13", 0], "vae": ["7", 0]}},
        "15": {
            "class_type": "ToobusyHiresUpscale",
            "inputs": {
                "image": ["14", 0],
                "vae": ["7", 0],
                "upscale_model_name": "4x_foolhardy_Remacri.pth",
                "downscale_method": "lanczos",
                "scale_by": 0.5,
                "upscale_model": ["16", 0],
            },
        },
        "16": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": "4xRealWebPhoto_v4_dat2.pth"}},
        "17": {
            "class_type": "Krea2ControlImageEncode",
            "inputs": {
                "control_image": ["2", 0],
                "vae": ["7", 0],
                "resize": "match_latent_size",
                "upscale_method": "lanczos",
                "crop": "center",
                "channel_mode": "grayscale",
                "normalize": "per_image_minmax",
                "invert": False,
                "batch_mode": "independent_images",
                "latent": ["15", 1],
            },
        },
        "18": {"class_type": "Krea2ControlApply", "inputs": {"model": ["5", 0], "control_latent": ["17", 0]}},
        "19": {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed + 1,
                "steps": 4,
                "cfg": 1,
                "sampler_name": "euler",
                "scheduler": "simple",
                "denoise": 0.30,
                "model": ["18", 0],
                "positive": ["11", 0],
                "negative": ["12", 0],
                "latent_image": ["15", 1],
            },
        },
        "20": {"class_type": "VAEDecode", "inputs": {"samples": ["19", 0], "vae": ["7", 0]}},
        "21": {"class_type": "SaveImage", "inputs": {"filename_prefix": prefix, "images": ["20", 0]}},
    }
    return workflow


def save_template() -> None:
    sample = build_workflow(0, FRAMES_DIR / "frame_00000.png", pilot=True)
    WORKFLOW_TEMPLATE.write_text(json.dumps(sample, ensure_ascii=False, indent=2), encoding="utf-8")


def queue_workflow(workflow: dict) -> str:
    payload = {"prompt": workflow, "client_id": "codex-krea2-hook-" + uuid.uuid4().hex[:8]}
    result = request_json("/prompt", payload, timeout=30)
    if result.get("node_errors"):
        raise RuntimeError(json.dumps(result["node_errors"], ensure_ascii=False, indent=2))
    return result["prompt_id"]


def wait_history(prompt_id: str, poll_seconds: float = 2.0) -> dict:
    while True:
        history = request_json(f"/history/{prompt_id}", timeout=10)
        item = history.get(prompt_id)
        if item:
            status = item.get("status", {})
            if status.get("completed") or status.get("status_str") == "success":
                return item
            if status.get("status_str") == "error":
                raise RuntimeError(json.dumps(status, ensure_ascii=False, indent=2))
        time.sleep(poll_seconds)


def copy_ordered_output(history_item: dict, frame_index: int) -> Path:
    outputs = history_item.get("outputs", {})
    images = outputs.get("21", {}).get("images", [])
    if not images:
        raise RuntimeError("No SaveImage output found for node 21.")
    image = images[0]
    src = OUTPUT_ROOT / image.get("subfolder", "") / image["filename"]
    dst = ORDERED_DIR / f"frame_{frame_index:05d}.png"
    ORDERED_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return dst


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--count", type=int, default=8)
    parser.add_argument("--frames", type=str, default="")
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--assemble", action="store_true")
    parser.add_argument("--free-first", action="store_true")
    args = parser.parse_args()

    frames = sorted(FRAMES_DIR.glob("frame_*.png"))
    if not frames:
        raise RuntimeError(f"No frames found in {FRAMES_DIR}")

    save_template()
    if args.free_first:
        free_memory()

    selected_frames = []
    if args.frames.strip():
        selected_frames = [int(part.strip()) for part in args.frames.split(",") if part.strip()]
    else:
        end = len(frames) if args.full else min(len(frames), args.start + args.count)
        selected_frames = list(range(args.start, end))

    processed = []
    for frame_index in selected_frames:
        workflow = build_workflow(frame_index, frames[frame_index], pilot=not args.full)
        prompt_id = queue_workflow(workflow)
        print(f"queued frame {frame_index:05d} {prompt_id}", flush=True)
        history_item = wait_history(prompt_id)
        dst = copy_ordered_output(history_item, frame_index)
        processed.append(str(dst))
        print(f"done frame {frame_index:05d} -> {dst}", flush=True)

    if args.assemble:
        import subprocess

        VIDEO_OUT.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-framerate",
                "24",
                "-i",
                str(ORDERED_DIR / "frame_%05d.png"),
                "-c:v",
                "libx264",
                "-crf",
                "17",
                "-preset",
                "slow",
                "-pix_fmt",
                "yuv420p",
                str(VIDEO_OUT),
            ],
            check=True,
        )
        print(f"assembled {VIDEO_OUT}", flush=True)

    print(json.dumps({"processed": processed, "template": str(WORKFLOW_TEMPLATE)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
