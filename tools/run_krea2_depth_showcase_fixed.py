import json
import time
import urllib.request
import uuid
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


COMFY = "http://127.0.0.1:8188"
INPUT_ROOT = Path(r"C:\Users\kriss\AppData\Local\Comfy-Desktop\ComfyUI-Shared\input")
OUTPUT_ROOT = Path(r"C:\Users\kriss\AppData\Local\Comfy-Desktop\ComfyUI-Shared\output")
HUB_ROOT = Path(r"D:\working\KREA2_ControlNet_DemoHub")

BUNDLE = "krea2_depth_showcase_fixed_0710"
OUT_SUBDIR = f"pingpong/{BUNDLE}"

SAMPLES = [
    {
        "slug": "spotlight_royal_stage",
        "source": "krea2_control_top7_landscape/control_depth_01_spotlight_stage_16x9.png",
        "lora": r"KREA2_TOP7\krea2_vintagetarot.safetensors",
        "lora_strength": 0.48,
        "control_strength": 0.82,
        "seed": 271001,
        "prompt": "A royal emerald K-pop concert reveal, single Korean male performer centered under a giant spotlight, ornate tarot stage arches, velvet black and gold wardrobe, cinematic fog, strong depth perspective, premium music video frame, no text, no logo, no duplicate people",
    },
    {
        "slug": "diagonal_neon_runway",
        "source": "krea2_control_top7_landscape/control_depth_02_diagonal_runway_16x9.png",
        "lora": r"KREA2_TOP7\krea2_neondrip.safetensors",
        "lora_strength": 0.55,
        "control_strength": 0.86,
        "seed": 271002,
        "prompt": "A futuristic Seoul neon runway shot, single Korean male performer sprinting toward camera, emerald chrome streetwear, glossy diagonal concert runway, laser beams, cyan magenta rim light, dramatic depth-controlled low angle, premium YouTube opening frame, no text, no logo",
    },
    {
        "slug": "spiral_darkbrush_closeup",
        "source": "krea2_control_top7_landscape/control_depth_03_closeup_spiral_16x9.png",
        "lora": r"KREA2_TOP7\krea2_darkbrush.safetensors",
        "lora_strength": 0.52,
        "control_strength": 0.78,
        "seed": 271003,
        "prompt": "A dark painterly close-up music video poster, single Korean male performer surrounded by a spiral tunnel of stage lights, emerald black couture jacket, smoky brush texture, intense eye contact, deep foreground-background separation, premium cinematic image, no text, no logo",
    },
]


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


def wait_for_prompt(prompt_id: str, timeout: int = 900) -> dict:
    start = time.time()
    while time.time() - start < timeout:
        hist = request_json(f"/history/{prompt_id}", timeout=30)
        if prompt_id in hist:
            return hist[prompt_id]
        time.sleep(2)
    raise TimeoutError(prompt_id)


def output_paths(history: dict) -> list[Path]:
    paths: list[Path] = []
    for node in (history.get("outputs") or {}).values():
        for image in node.get("images") or []:
            paths.append(OUTPUT_ROOT / (image.get("subfolder") or "") / image["filename"])
    return paths


def build_workflow(sample: dict) -> dict:
    prefix_1mp = f"{OUT_SUBDIR}/{sample['slug']}_01_1mp"
    prefix_2k = f"{OUT_SUBDIR}/{sample['slug']}_02_2k"
    return {
        "1": {
            "class_type": "LoadImage",
            "inputs": {"image": sample["source"]},
        },
        "2": {
            "class_type": "DepthAnythingV2Preprocessor",
            "inputs": {"image": ["1", 0], "ckpt_name": "depth_anything_v2_vitl.pth", "resolution": 1024},
        },
        "3": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": "krea2_turbo_fp8_scaled.safetensors", "weight_dtype": "default"},
        },
        "4": {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {"model": ["3", 0], "lora_name": sample["lora"], "strength_model": sample["lora_strength"]},
        },
        "5": {
            "class_type": "Krea2ControlLoRALoader",
            "inputs": {
                "model": ["4", 0],
                "lora_name": r"KREA2_ControlNet\krea2_depth_control_lora.safetensors",
                "strength": sample["control_strength"],
            },
        },
        "6": {
            "class_type": "CLIPLoader",
            "inputs": {"clip_name": "qwen3vl_4b_fp8_scaled.safetensors", "type": "krea2", "device": "default"},
        },
        "7": {"class_type": "VAELoader", "inputs": {"vae_name": "qwen_image_vae.safetensors"}},
        "8": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": "4xRealWebPhoto_v4_dat2.pth"}},
        "9": {"class_type": "EmptyLatentImage", "inputs": {"width": 1024, "height": 576, "batch_size": 1}},
        "10": {
            "class_type": "Krea2ControlImageEncode",
            "inputs": {
                "control_image": ["2", 0],
                "vae": ["7", 0],
                "latent": ["9", 0],
                "resize": "match_latent_size",
                "upscale_method": "lanczos",
                "crop": "center",
                "channel_mode": "grayscale",
                "normalize": "per_image_minmax",
                "invert": False,
                "batch_mode": "independent_images",
            },
        },
        "11": {"class_type": "Krea2ControlApply", "inputs": {"model": ["5", 0], "control_latent": ["10", 0]}},
        "12": {"class_type": "CLIPTextEncode", "inputs": {"text": sample["prompt"], "clip": ["6", 0]}},
        "13": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["12", 0]}},
        "14": {
            "class_type": "KSampler",
            "inputs": {
                "seed": sample["seed"],
                "steps": 8,
                "cfg": 1,
                "sampler_name": "euler",
                "scheduler": "simple",
                "denoise": 1,
                "model": ["11", 0],
                "positive": ["12", 0],
                "negative": ["13", 0],
                "latent_image": ["9", 0],
            },
        },
        "15": {"class_type": "VAEDecode", "inputs": {"samples": ["14", 0], "vae": ["7", 0]}},
        "16": {"class_type": "SaveImage", "inputs": {"filename_prefix": prefix_1mp, "images": ["15", 0]}},
        "17": {
            "class_type": "ToobusyHiresUpscale",
            "inputs": {
                "image": ["15", 0],
                "vae": ["7", 0],
                "upscale_model": ["8", 0],
                "upscale_model_name": "4x_foolhardy_Remacri.pth",
                "downscale_method": "lanczos",
                "scale_by": 0.5,
            },
        },
        "18": {
            "class_type": "Krea2ControlImageEncode",
            "inputs": {
                "control_image": ["2", 0],
                "vae": ["7", 0],
                "latent": ["17", 1],
                "resize": "match_latent_size",
                "upscale_method": "lanczos",
                "crop": "center",
                "channel_mode": "grayscale",
                "normalize": "per_image_minmax",
                "invert": False,
                "batch_mode": "independent_images",
            },
        },
        "19": {"class_type": "Krea2ControlApply", "inputs": {"model": ["5", 0], "control_latent": ["18", 0]}},
        "20": {
            "class_type": "KSampler",
            "inputs": {
                "seed": sample["seed"] + 1000,
                "steps": 4,
                "cfg": 1,
                "sampler_name": "euler",
                "scheduler": "simple",
                "denoise": 0.32,
                "model": ["19", 0],
                "positive": ["12", 0],
                "negative": ["13", 0],
                "latent_image": ["17", 1],
            },
        },
        "21": {"class_type": "VAEDecode", "inputs": {"samples": ["20", 0], "vae": ["7", 0]}},
        "22": {"class_type": "SaveImage", "inputs": {"filename_prefix": prefix_2k, "images": ["21", 0]}},
    }


def write_sidecar(path: Path, sample: dict, idx: int, elapsed: float) -> None:
    with Image.open(path) as im:
        width, height = im.size
    stage = "2K final" if "_02_2k" in path.name else "1MP preview"
    meta = {
        "request": "KREA2 Depth ControlNet showcase",
        "mode": "image",
        "bundle": BUNDLE,
        "stage": f"{idx}. {sample['slug']} / {stage}",
        "stage_idx": idx,
        "orig": sample["source"],
        "generated": sample["prompt"],
        "depth_lora_strength": sample["control_strength"],
        "style_lora": sample["lora"],
        "style_lora_strength": sample["lora_strength"],
        "gen_secs": round(elapsed, 1),
        "width": width,
        "height": height,
        "resolution": f"{width}x{height}",
    }
    path.with_suffix(path.suffix + ".pingpong.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def make_contact_sheet(paths: list[Path]) -> Path | None:
    if not paths:
        return None
    thumbs = []
    for path in paths:
        im = Image.open(path).convert("RGB")
        im.thumbnail((420, 236), Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", (420, 280), (24, 25, 28))
        canvas.paste(im, ((420 - im.width) // 2, 8))
        draw = ImageDraw.Draw(canvas)
        try:
            font = ImageFont.truetype(r"C:\Windows\Fonts\malgun.ttf", 20)
        except Exception:
            font = ImageFont.load_default()
        draw.text((12, 246), path.name[:42], fill=(235, 235, 235), font=font)
        thumbs.append(canvas)
    cols = 2
    rows = (len(thumbs) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * 420, rows * 280), (18, 18, 20))
    for i, thumb in enumerate(thumbs):
        sheet.paste(thumb, ((i % cols) * 420, (i // cols) * 280))
    out = OUTPUT_ROOT / OUT_SUBDIR / "_contact_sheet.jpg"
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, quality=90)
    return out


def main() -> None:
    request_json("/free", {"unload_models": False, "free_memory": True}, timeout=10)
    manifest = []
    final_paths: list[Path] = []
    for idx, sample in enumerate(SAMPLES, 1):
        started = time.time()
        workflow = build_workflow(sample)
        payload = {"prompt": workflow, "client_id": str(uuid.uuid4())}
        queued = request_json("/prompt", payload, timeout=30)
        prompt_id = queued["prompt_id"]
        history = wait_for_prompt(prompt_id)
        elapsed = time.time() - started
        paths = output_paths(history)
        for path in paths:
            write_sidecar(path, sample, idx, elapsed)
        final_paths.extend(paths)
        manifest.append(
            {
                "idx": idx,
                "slug": sample["slug"],
                "prompt_id": prompt_id,
                "outputs": [str(p) for p in paths],
                "elapsed": round(elapsed, 1),
            }
        )
        print(json.dumps(manifest[-1], ensure_ascii=False))
    sheet = make_contact_sheet(final_paths)
    manifest_path = HUB_ROOT / "rehearsal_results" / f"{BUNDLE}_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"manifest": str(manifest_path), "contact_sheet": str(sheet)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
