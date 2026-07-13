import json
import time
import urllib.request
import uuid
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps


COMFY = "http://127.0.0.1:8188"
INPUT_ROOT = Path(r"C:\Users\kriss\AppData\Local\Comfy-Desktop\ComfyUI-Shared\input")
OUTPUT_ROOT = Path(r"C:\Users\kriss\AppData\Local\Comfy-Desktop\ComfyUI-Shared\output")
HUB_ROOT = Path(r"D:\working\KREA2_ControlNet_DemoHub")

SOURCE_SUBDIR = "krea2_canny_controlnet_thumbnail_90_sources"
BUNDLE = "krea2_canny_controlnet_thumbnail_90_0710"
OUT_SUBDIR = f"pingpong/{BUNDLE}"

SAMPLES = [
    {
        "slug": "krea2_controlnet_main",
        "label": "KREA2\n\ucee8\ud2b8\ub864\ub137",
        "accent": "\ub4ed\uc2a4 + \uce90\ub2c8",
        "base": OUTPUT_ROOT / "pingpong" / "krea2_depth_showcase_fixed_0710" / "diagonal_neon_runway_02_2k_00001_.png",
        "style_lora": r"KREA2_TOP7\krea2_neondrip.safetensors",
        "style_strength": 0.36,
        "canny_strength": 0.72,
        "seed": 909101,
        "colors": ((255, 246, 90), (0, 238, 255), (255, 37, 132)),
        "prompt": "A 90-point Korean YouTube thumbnail about KREA2 ControlNet, futuristic creator tutorial energy, neon runway hero image, huge clean title area, bold editorial composition, depth and canny comparison vibe, premium tech thumbnail, crisp face, dramatic lighting, no watermark",
    },
    {
        "slug": "depth_vs_canny",
        "label": "\ub4ed\uc2a4 VS\n\uce90\ub2c8",
        "accent": "ControlNet",
        "base": OUTPUT_ROOT / "pingpong" / "krea2_depth_showcase_fixed_0710" / "spotlight_royal_stage_02_2k_00001_.png",
        "style_lora": r"KREA2_TOP7\krea2_vintagetarot.safetensors",
        "style_strength": 0.34,
        "canny_strength": 0.68,
        "seed": 909202,
        "colors": ((255, 255, 255), (255, 184, 35), (28, 240, 154)),
        "prompt": "A premium Korean YouTube thumbnail comparing Depth ControlNet versus Canny ControlNet, split-screen tutorial mood, royal stage and neon UI overlays, big readable title zone, high contrast, professional creator channel design, no watermark",
    },
    {
        "slug": "composition_lock_2k",
        "label": "\uad6c\ub3c4\uace0\uc815\n2K \uc644\uc131",
        "accent": "\ud558\uc774\ub808\uc2a4 \uc2dc\uc5f0",
        "base": OUTPUT_ROOT / "pingpong" / "krea2_depth_showcase_fixed_0710" / "spiral_darkbrush_closeup_02_2k_00001_.png",
        "style_lora": r"KREA2_TOP7\krea2_darkbrush.safetensors",
        "style_strength": 0.38,
        "canny_strength": 0.74,
        "seed": 909303,
        "colors": ((255, 255, 255), (91, 255, 209), (255, 60, 95)),
        "prompt": "A cinematic Korean AI tutorial thumbnail showing composition lock and 2K hires finish, intense close-up creator hero, spiral depth tunnel, glossy UI control graph in background, bold high contrast typography area, premium tech video cover, no watermark",
    },
    {
        "slug": "text_plus_image_canny",
        "label": "\uae00\uc790+\uc774\ubbf8\uc9c0\n\uce90\ub2c8\uc18c\uc2a4",
        "accent": "\ud55c\ubc29\uc5d0",
        "base": OUTPUT_ROOT / "pingpong" / "krea2_canny_creative_showcase_fixed_0710" / "crazy_quality_S086_00001_.png",
        "style_lora": r"KREA2_TOP7\krea2_retroanime.safetensors",
        "style_strength": 0.30,
        "canny_strength": 0.82,
        "seed": 909404,
        "colors": ((255, 236, 70), (255, 255, 255), (0, 210, 255)),
        "prompt": "A top-tier Korean YouTube thumbnail explaining text plus image Canny source control, anime tech tutorial cover, clear title area and powerful portrait, glowing edge-map graphics, cyan yellow magenta accents, professional thumbnail design, no watermark",
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


def rel_input(path: Path) -> str:
    return path.relative_to(INPUT_ROOT).as_posix()


def font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    candidates = ["malgunbd.ttf", "NotoSansKR-Bold.otf", "NotoSansKR-VF.ttf", "malgun.ttf", "gulim.ttc"]
    if not bold:
        candidates = ["malgun.ttf", "NotoSansKR-VF.ttf", "gulim.ttc"]
    for name in candidates:
        path = Path(r"C:\Windows\Fonts") / name
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def fit_cover(path: Path, size: tuple[int, int]) -> Image.Image:
    if not path.exists():
        path = INPUT_ROOT / "krea2_control_top7_landscape" / "control_depth_02_diagonal_runway_16x9.png"
    im = Image.open(path).convert("RGB")
    return ImageOps.fit(im, size, method=Image.Resampling.LANCZOS, centering=(0.55, 0.48))


def text_mask(size: tuple[int, int], label: str, sample: dict) -> Image.Image:
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    lines = label.splitlines()
    sizes = [232, 205] if len(lines) == 2 else [210, 190, 170]
    x = 90
    y = 210 if len(lines[0]) <= 5 else 230
    for i, line in enumerate(lines):
        fnt = font(sizes[min(i, len(sizes) - 1)], True)
        draw.text((x, y), line, font=fnt, fill=255, stroke_width=9, stroke_fill=255)
        bbox = draw.textbbox((x, y), line, font=fnt, stroke_width=9)
        y = bbox[3] + 16
    if sample.get("accent"):
        fnt = font(78, True)
        draw.rounded_rectangle((96, 735, 820, 840), radius=32, fill=255)
        draw.text((135, 747), sample["accent"], font=fnt, fill=0)
    return mask.filter(ImageFilter.MaxFilter(3))


def make_source(sample: dict) -> tuple[Path, Path, Path]:
    input_dir = INPUT_ROOT / SOURCE_SUBDIR
    hub_dir = HUB_ROOT / "control_sources"
    input_dir.mkdir(parents=True, exist_ok=True)
    hub_dir.mkdir(parents=True, exist_ok=True)

    size = (2048, 1152)
    base = fit_cover(sample["base"], size)
    base = ImageEnhance.Contrast(base).enhance(1.12)
    base = ImageEnhance.Color(base).enhance(1.16)

    dark = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(dark, "RGBA")
    d.rectangle((0, 0, 930, 1152), fill=(0, 0, 0, 170))
    d.polygon([(810, 0), (1110, 0), (900, 1152), (660, 1152)], fill=(0, 0, 0, 80))
    preview = Image.alpha_composite(base.convert("RGBA"), dark)

    label = text_mask(size, sample["label"], sample)
    c1, c2, c3 = sample["colors"]
    overlay = Image.new("RGBA", size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay, "RGBA")
    od.rectangle((70, 150, 930, 880), outline=(*c3, 150), width=10)
    overlay.paste((*c1, 255), mask=label.filter(ImageFilter.MaxFilter(7)))
    overlay.paste((*c2, 255), mask=label)
    preview = Image.alpha_composite(preview, overlay).convert("RGB")

    gray = ImageOps.autocontrast(base.convert("L"))
    img_edges = gray.filter(ImageFilter.FIND_EDGES).point(lambda p: 210 if p > 34 else 0)
    img_edges = img_edges.filter(ImageFilter.MaxFilter(3))
    # Make the title dominate the guide while still preserving the visual composition.
    combined = Image.new("L", size, 0)
    combined = Image.composite(Image.new("L", size, 135), combined, img_edges)
    combined = Image.composite(Image.new("L", size, 255), combined, label)
    combined = combined.filter(ImageFilter.MaxFilter(3))

    edge = Image.new("RGB", size, (0, 0, 0))
    edge.paste((248, 252, 255), mask=combined)

    stem = sample["slug"]
    preview_path = input_dir / f"{stem}_preview_2048x1152.png"
    edge_2k_path = input_dir / f"{stem}_edges_2k_2048x1152.png"
    edge_1mp_path = input_dir / f"{stem}_edges_1mp_1024x576.png"
    preview.save(preview_path)
    edge.save(edge_2k_path)
    edge.resize((1024, 576), Image.Resampling.LANCZOS).save(edge_1mp_path)
    for src in (preview_path, edge_2k_path, edge_1mp_path):
        (hub_dir / src.name).write_bytes(src.read_bytes())
    return preview_path, edge_1mp_path, edge_2k_path


def build_workflow(sample: dict, edge_1mp: Path, edge_2k: Path) -> dict:
    prefix = f"{OUT_SUBDIR}/{sample['slug']}_RAW"
    return {
        "1": {"class_type": "LoadImage", "inputs": {"image": rel_input(edge_1mp)}},
        "2": {"class_type": "LoadImage", "inputs": {"image": rel_input(edge_2k)}},
        "3": {"class_type": "UNETLoader", "inputs": {"unet_name": "krea2_turbo_fp8_scaled.safetensors", "weight_dtype": "default"}},
        "4": {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {"model": ["3", 0], "lora_name": sample["style_lora"], "strength_model": sample["style_strength"]},
        },
        "5": {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {
                "model": ["4", 0],
                "lora_name": r"KREA2_ControlNet\NK2E-canny-v0.1.safetensors",
                "strength_model": sample["canny_strength"],
            },
        },
        "6": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen3vl_4b_fp8_scaled.safetensors", "type": "krea2", "device": "default"}},
        "7": {"class_type": "VAELoader", "inputs": {"vae_name": "qwen_image_vae.safetensors"}},
        "8": {"class_type": "EmptyLatentImage", "inputs": {"width": 1024, "height": 576, "batch_size": 1}},
        "9": {"class_type": "VAEEncode", "inputs": {"pixels": ["1", 0], "vae": ["7", 0]}},
        "10": {"class_type": "NK2EInContextModelNode", "inputs": {"model": ["5", 0]}},
        "11": {"class_type": "CLIPTextEncode", "inputs": {"text": sample["prompt"], "clip": ["6", 0]}},
        "12": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["11", 0]}},
        "13": {"class_type": "NK2ESetReferenceNode", "inputs": {"conditioning": ["11", 0], "reference": ["9", 0]}},
        "14": {
            "class_type": "KSampler",
            "inputs": {
                "seed": sample["seed"],
                "steps": 8,
                "cfg": 1,
                "sampler_name": "euler",
                "scheduler": "simple",
                "denoise": 1,
                "model": ["10", 0],
                "positive": ["13", 0],
                "negative": ["12", 0],
                "latent_image": ["8", 0],
            },
        },
        "15": {"class_type": "VAEDecode", "inputs": {"samples": ["14", 0], "vae": ["7", 0]}},
        "16": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": "4xRealWebPhoto_v4_dat2.pth"}},
        "17": {
            "class_type": "ToobusyHiresUpscale",
            "inputs": {
                "image": ["15", 0],
                "vae": ["7", 0],
                "upscale_model_name": "4xRealWebPhoto_v4_dat2.pth",
                "downscale_method": "lanczos",
                "scale_by": 0.5,
                "upscale_model": ["16", 0],
            },
        },
        "18": {"class_type": "VAEEncode", "inputs": {"pixels": ["2", 0], "vae": ["7", 0]}},
        "19": {"class_type": "NK2ESetReferenceNode", "inputs": {"conditioning": ["11", 0], "reference": ["18", 0]}},
        "20": {
            "class_type": "KSampler",
            "inputs": {
                "seed": sample["seed"] + 100,
                "steps": 4,
                "cfg": 1,
                "sampler_name": "euler",
                "scheduler": "simple",
                "denoise": 0.18,
                "model": ["10", 0],
                "positive": ["19", 0],
                "negative": ["12", 0],
                "latent_image": ["17", 1],
            },
        },
        "21": {"class_type": "VAEDecode", "inputs": {"samples": ["20", 0], "vae": ["7", 0]}},
        "22": {"class_type": "SaveImage", "inputs": {"filename_prefix": prefix, "images": ["21", 0]}},
    }


def wait_for_prompt(prompt_id: str, timeout: int = 900) -> dict:
    start = time.time()
    while time.time() - start < timeout:
        hist = request_json(f"/history/{prompt_id}", timeout=30)
        if prompt_id in hist:
            return hist[prompt_id]
        time.sleep(2)
    raise TimeoutError(prompt_id)


def output_paths(history: dict) -> list[Path]:
    paths = []
    for node in (history.get("outputs") or {}).values():
        for image in node.get("images") or []:
            paths.append(OUTPUT_ROOT / (image.get("subfolder") or "") / image["filename"])
    return paths


def draw_final_text(im: Image.Image, sample: dict) -> Image.Image:
    im = ImageOps.fit(im.convert("RGB"), (2048, 1152), method=Image.Resampling.LANCZOS, centering=(0.56, 0.5))
    im = ImageEnhance.Contrast(im).enhance(1.08)
    im = ImageEnhance.Color(im).enhance(1.12)
    overlay = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay, "RGBA")
    d.rectangle((0, 0, 980, 1152), fill=(0, 0, 0, 120))
    d.polygon([(820, 0), (1100, 0), (920, 1152), (700, 1152)], fill=(0, 0, 0, 65))
    c1, c2, c3 = sample["colors"]
    lines = sample["label"].splitlines()
    y = 190
    for i, line in enumerate(lines):
        fnt = font(238 if i == 0 else 204, True)
        d.text((88, y + 12), line, font=fnt, fill=(0, 0, 0, 210), stroke_width=18, stroke_fill=(0, 0, 0, 230))
        fill_rgb = c1 if i == 0 else c2
        d.text((88, y), line, font=fnt, fill=(*fill_rgb, 255), stroke_width=8, stroke_fill=(8, 8, 12, 255))
        bbox = d.textbbox((88, y), line, font=fnt, stroke_width=8)
        y = bbox[3] + 12
    fnt = font(76, True)
    d.rounded_rectangle((95, 770, 880, 875), radius=28, fill=(*c3, 238))
    d.text((135, 787), sample["accent"], font=fnt, fill=(8, 8, 10, 255))
    d.rounded_rectangle((96, 912, 760, 1006), radius=18, outline=(255, 255, 255, 210), width=5)
    d.text((132, 928), "KREA2 CONTROLNET", font=font(48, True), fill=(255, 255, 255, 245))
    return Image.alpha_composite(im.convert("RGBA"), overlay).convert("RGB")


def write_sidecar(path: Path, sample: dict, preview: Path, edge_1mp: Path, edge_2k: Path, raw: Path, idx: int, elapsed: float) -> None:
    with Image.open(path) as im:
        width, height = im.size
    meta = {
        "request": "90+ YouTube thumbnail for KREA2 ControlNet Depth/Canny",
        "mode": "image",
        "bundle": BUNDLE,
        "stage": f"{idx}. {sample['slug']} / final thumbnail lockup",
        "stage_idx": idx,
        "orig": rel_input(edge_2k),
        "orig_1mp": rel_input(edge_1mp),
        "source_preview": rel_input(preview),
        "raw_generation": str(raw),
        "generated": sample["prompt"],
        "note": "The control source combines image Canny edges with thick title-letter Canny edges. Final output adds a clean production title lockup so it reads like a real YouTube thumbnail.",
        "canny_strength": sample["canny_strength"],
        "style_lora": sample["style_lora"],
        "gen_secs": round(elapsed, 1),
        "width": width,
        "height": height,
        "resolution": f"{width}x{height}",
    }
    path.with_suffix(path.suffix + ".pingpong.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def make_contact_sheet(paths: list[Path]) -> Path:
    thumbs = []
    for path in paths:
        im = Image.open(path).convert("RGB")
        im.thumbnail((512, 288), Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", (512, 332), (18, 18, 20))
        canvas.paste(im, ((512 - im.width) // 2, 0))
        d = ImageDraw.Draw(canvas)
        d.text((12, 296), path.name[:54], fill=(238, 238, 238), font=font(22, False))
        thumbs.append(canvas)
    cols = 2
    rows = (len(thumbs) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * 512, rows * 332), (10, 10, 12))
    for i, thumb in enumerate(thumbs):
        sheet.paste(thumb, ((i % cols) * 512, (i // cols) * 332))
    out = OUTPUT_ROOT / OUT_SUBDIR / "_contact_sheet.jpg"
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, quality=92)
    return out


def main() -> None:
    request_json("/free", {"unload_models": False, "free_memory": True}, timeout=10)
    final_paths = []
    manifest = []
    for idx, sample in enumerate(SAMPLES, 1):
        preview, edge_1mp, edge_2k = make_source(sample)
        workflow = build_workflow(sample, edge_1mp, edge_2k)
        started = time.time()
        queued = request_json("/prompt", {"prompt": workflow, "client_id": str(uuid.uuid4())}, timeout=30)
        prompt_id = queued["prompt_id"]
        print(f"[{idx}/{len(SAMPLES)}] queued {sample['slug']} prompt_id={prompt_id}", flush=True)
        history = wait_for_prompt(prompt_id)
        elapsed = time.time() - started
        status = history.get("status", {})
        if status.get("status_str") == "error":
            raise RuntimeError(json.dumps(status, ensure_ascii=False))
        raws = output_paths(history)
        if not raws:
            raise RuntimeError(f"No output for {sample['slug']}")
        raw = max(raws, key=lambda p: p.stat().st_mtime if p.exists() else 0)
        final = OUTPUT_ROOT / OUT_SUBDIR / f"{sample['slug']}_FINAL.png"
        final.parent.mkdir(parents=True, exist_ok=True)
        draw_final_text(Image.open(raw), sample).save(final)
        write_sidecar(final, sample, preview, edge_1mp, edge_2k, raw, idx, elapsed)
        final_paths.append(final)
        manifest.append({"idx": idx, "slug": sample["slug"], "prompt_id": prompt_id, "raw": str(raw), "final": str(final), "elapsed": round(elapsed, 1)})
        print(f"[{idx}/{len(SAMPLES)}] done {final} ({elapsed:.1f}s)", flush=True)
    sheet = make_contact_sheet(final_paths)
    manifest_path = HUB_ROOT / "rehearsal_results" / f"{BUNDLE}_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps({"bundle": BUNDLE, "results": manifest, "contact_sheet": str(sheet)}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"manifest": str(manifest_path), "contact_sheet": str(sheet)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
