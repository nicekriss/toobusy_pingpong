import json
import math
import time
import urllib.request
import uuid
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont


COMFY = "http://127.0.0.1:8188"
INPUT_ROOT = Path(r"C:\Users\kriss\AppData\Local\Comfy-Desktop\ComfyUI-Shared\input")
OUTPUT_ROOT = Path(r"C:\Users\kriss\AppData\Local\Comfy-Desktop\ComfyUI-Shared\output")
HUB_ROOT = Path(r"D:\working\KREA2_ControlNet_DemoHub")

SOURCE_SUBDIR = "krea2_control_canny_showcase"
BUNDLE = "krea2_canny_nk2e_showcase_korean_text_0710"
OUT_SUBDIR = f"pingpong/{BUNDLE}"

STAGES = [
    ("S01_OFF", 0.0),
    ("S02_050", 0.50),
    ("S03_100", 1.00),
    ("S04_125", 1.25),
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
    candidates = [
        Path(r"C:\Windows\Fonts\NotoSansKR-VF.ttf"),
        Path(r"C:\Windows\Fonts\malgunbd.ttf" if bold else r"C:\Windows\Fonts\malgun.ttf"),
        Path(r"C:\Windows\Fonts\gulim.ttc"),
    ]
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def centered_text(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], text: str, fnt, fill, stroke_fill=None, stroke_width=0) -> None:
    bbox = draw.multiline_textbbox((0, 0), text, font=fnt, spacing=18, stroke_width=stroke_width)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = box[0] + (box[2] - box[0] - tw) / 2
    y = box[1] + (box[3] - box[1] - th) / 2
    draw.multiline_text((x, y), text, font=fnt, fill=fill, spacing=18, align="center", stroke_fill=stroke_fill, stroke_width=stroke_width)


def make_edge(base: Image.Image) -> Image.Image:
    gray = base.convert("L")
    edges = gray.filter(ImageFilter.FIND_EDGES)
    edges = edges.point(lambda p: 255 if p > 24 else 0)
    edges = edges.filter(ImageFilter.MaxFilter(3))
    edge_rgb = Image.new("RGB", edges.size, (0, 0, 0))
    edge_rgb.paste((245, 252, 255), mask=edges)
    return edge_rgb


def save_source(name: str, base: Image.Image) -> tuple[Path, Path, Path]:
    input_dir = INPUT_ROOT / SOURCE_SUBDIR
    hub_dir = HUB_ROOT / "control_sources"
    input_dir.mkdir(parents=True, exist_ok=True)
    hub_dir.mkdir(parents=True, exist_ok=True)

    base_path = input_dir / f"{name}_base_2048x1152.png"
    edge_2k_path = input_dir / f"{name}_edges_2k_2048x1152.png"
    edge_1mp_path = input_dir / f"{name}_edges_1mp_1024x576.png"

    base.save(base_path)
    edge = make_edge(base)
    edge.save(edge_2k_path)
    edge.resize((1024, 576), Image.Resampling.LANCZOS).save(edge_1mp_path)

    for src in (base_path, edge_1mp_path, edge_2k_path):
        (hub_dir / src.name).write_bytes(src.read_bytes())
    return base_path, edge_1mp_path, edge_2k_path


def source_hangul_subscribe() -> tuple[str, str, Path, Path, Path]:
    img = Image.new("RGB", (2048, 1152), (10, 7, 18))
    d = ImageDraw.Draw(img, "RGBA")
    for i in range(18):
        color = (35, 230, 255, 42) if i % 2 else (255, 60, 170, 40)
        d.rounded_rectangle((120 + i * 19, 100 + i * 14, 1928 - i * 19, 1052 - i * 14), radius=42, outline=color, width=6)
    for i in range(22):
        y = 160 + i * 42
        d.line((80, y, 1968, y + math.sin(i) * 45), fill=(255, 255, 255, 22), width=2)
    centered_text(d, (240, 180, 1808, 960), "구독\n좋아요", font(275), (255, 246, 185, 255), (255, 58, 160, 255), 12)
    d.text((685, 970), "KREA2 CANNY TEST", font=font(62, False), fill=(65, 245, 255, 230), stroke_fill=(0, 0, 0, 220), stroke_width=4)
    paths = save_source("control_canny_05_hangul_subscribe", img.filter(ImageFilter.GaussianBlur(0.15)))
    prompt = (
        "A premium Korean neon typography poster, huge readable Hangul letters shaped exactly like the edge guide, "
        "the Korean words read '구독 좋아요', glossy black acrylic background, cyan and hot-pink neon tubes, luxury tech advertisement, "
        "clean graphic design, crisp contours, no person, no random letters, no insects."
    )
    return "hangul_subscribe", prompt, *paths


def source_luxury_sign() -> tuple[str, str, Path, Path, Path]:
    img = Image.new("RGB", (2048, 1152), (12, 10, 14))
    d = ImageDraw.Draw(img, "RGBA")
    d.rounded_rectangle((230, 150, 1818, 1000), radius=90, outline=(245, 210, 125, 210), width=18)
    d.rounded_rectangle((330, 260, 1718, 895), radius=65, outline=(90, 235, 255, 150), width=8)
    d.polygon([(805, 790), (1240, 790), (1345, 435), (690, 435)], fill=(80, 18, 38, 255), outline=(255, 220, 130, 230))
    d.arc((770, 210, 1275, 610), start=190, end=350, fill=(255, 220, 130, 240), width=24)
    for x in range(760, 1290, 85):
        d.line((x, 455, x - 38, 780), fill=(255, 90, 155, 85), width=5)
    centered_text(d, (360, 250, 1688, 445), "너무바쁜", font(170), (255, 236, 188, 255), (90, 20, 42, 255), 10)
    d.text((715, 900), "BURGUNDY BAG DROP", font=font(55, False), fill=(255, 78, 155, 230), stroke_fill=(0, 0, 0, 255), stroke_width=4)
    for i in range(28):
        x = 260 + (i * 69) % 1500
        y = 160 + (i * 131) % 760
        d.ellipse((x, y, x + 15, y + 15), fill=(255, 230, 150, 90))
    paths = save_source("control_canny_06_luxury_hangul_bag_sign", img.filter(ImageFilter.GaussianBlur(0.12)))
    prompt = (
        "A luxury burgundy handbag advertisement in a high-end Seoul boutique window, elegant Korean brand sign shaped by the edge guide, "
        "readable Hangul sign '너무바쁜', burgundy leather bag silhouette centered, gold trim, glossy reflections, premium fashion campaign, "
        "clean product photography, no insects, no extra people."
    )
    return "luxury_hangul_bag_sign", prompt, *paths


def source_comic_sfx() -> tuple[str, str, Path, Path, Path]:
    img = Image.new("RGB", (2048, 1152), (250, 236, 210))
    d = ImageDraw.Draw(img, "RGBA")
    panels = [(90, 80, 900, 520), (990, 80, 1950, 520), (90, 610, 1950, 1060)]
    fills = [(255, 60, 125, 255), (45, 225, 255, 255), (255, 220, 70, 255)]
    for p, c in zip(panels, fills):
        d.rounded_rectangle(p, radius=14, fill=c, outline=(20, 20, 26, 255), width=10)
    cx, cy = 1010, 620
    for i in range(72):
        a = i / 72 * math.tau
        d.line((cx, cy, cx + math.cos(a) * 1080, cy + math.sin(a) * 760), fill=(16, 17, 28, 120), width=3)
    d.ellipse((750, 310, 1285, 840), fill=(30, 26, 36, 255), outline=(255, 255, 255, 230), width=12)
    d.polygon([(610, 690), (420, 950), (820, 840)], fill=(35, 35, 52, 255), outline=(255, 255, 255, 220))
    d.polygon([(1360, 690), (1625, 940), (1190, 845)], fill=(35, 35, 52, 255), outline=(255, 255, 255, 220))
    centered_text(d, (245, 180, 1810, 1000), "쾅!", font(430), (255, 252, 230, 255), (15, 15, 22, 255), 18)
    paths = save_source("control_canny_07_pop_manhwa_sfx", img.filter(ImageFilter.GaussianBlur(0.1)))
    prompt = (
        "A high-quality Korean pop manhwa action cover, dramatic heroic close-up, giant readable Korean sound effect shaped by the edge guide, "
        "the Hangul text reads '쾅!', explosive speed lines, glossy pop colors, sharp ink contours, energetic commercial illustration, "
        "single main character silhouette only, no insects."
    )
    return "pop_manhwa_sfx", prompt, *paths


def build_workflow(slug: str, prompt: str, edge_1mp_path: Path, edge_2k_path: Path, stage: str, strength: float, idx: int) -> dict:
    seed = 803000 + idx * 17
    prefix = f"{OUT_SUBDIR}/{slug}_{stage}"
    workflow = {
        "1": {"class_type": "LoadImage", "inputs": {"image": rel_input(edge_1mp_path)}},
        "2": {"class_type": "LoadImage", "inputs": {"image": rel_input(edge_2k_path)}},
        "3": {"class_type": "UNETLoader", "inputs": {"unet_name": "krea2_turbo_fp8_scaled.safetensors", "weight_dtype": "default"}},
        "4": {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {"model": ["3", 0], "lora_name": r"KREA2_TOP7\krea2_neondrip.safetensors", "strength_model": 0.22},
        },
        "5": {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {"model": ["4", 0], "lora_name": r"KREA2_ControlNet\NK2E-canny-v0.1.safetensors", "strength_model": strength},
        },
        "6": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen3vl_4b_fp8_scaled.safetensors", "type": "krea2", "device": "default"}},
        "7": {"class_type": "VAELoader", "inputs": {"vae_name": "qwen_image_vae.safetensors"}},
        "8": {"class_type": "EmptyLatentImage", "inputs": {"width": 1024, "height": 576, "batch_size": 1}},
        "9": {"class_type": "VAEEncode", "inputs": {"pixels": ["1", 0], "vae": ["7", 0]}},
        "10": {"class_type": "NK2EInContextModelNode", "inputs": {"model": ["5", 0]}},
        "11": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["6", 0]}},
        "12": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["11", 0]}},
        "13": {"class_type": "NK2ESetReferenceNode", "inputs": {"conditioning": ["11", 0], "reference": ["9", 0]}},
        "14": {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed,
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
                "seed": seed + 100,
                "steps": 4,
                "cfg": 1,
                "sampler_name": "euler",
                "scheduler": "simple",
                "denoise": 0.20,
                "model": ["10", 0],
                "positive": ["19", 0],
                "negative": ["12", 0],
                "latent_image": ["17", 1],
            },
        },
        "21": {"class_type": "VAEDecode", "inputs": {"samples": ["20", 0], "vae": ["7", 0]}},
        "22": {"class_type": "SaveImage", "inputs": {"filename_prefix": prefix, "images": ["21", 0]}},
    }
    if strength <= 0:
        workflow["14"]["inputs"]["model"] = ["4", 0]
        workflow["14"]["inputs"]["positive"] = ["11", 0]
        workflow["20"]["inputs"]["model"] = ["4", 0]
        workflow["20"]["inputs"]["positive"] = ["11", 0]
    return workflow


def wait_for_prompt(prompt_id: str, timeout: int = 900) -> dict:
    started = time.time()
    while time.time() - started < timeout:
        hist = request_json(f"/history/{prompt_id}", timeout=30)
        if prompt_id in hist:
            return hist[prompt_id]
        time.sleep(2)
    raise TimeoutError(prompt_id)


def output_paths(history: dict) -> list[Path]:
    paths = []
    for node in (history.get("outputs") or {}).values():
        for image in node.get("images") or []:
            subfolder = image.get("subfolder") or ""
            paths.append(OUTPUT_ROOT / subfolder / image["filename"])
    return paths


def write_sidecar(path: Path, slug: str, edge_1mp_path: Path, edge_2k_path: Path, stage: str, strength: float, idx: int, elapsed: float) -> None:
    with Image.open(path) as im:
        width, height = im.size
    meta = {
        "request": "KREA2 Canny ControlNet showcase",
        "generated": f"{slug}: Canny/NK2E strength {strength:.2f} showcase result.",
        "mode": "image",
        "note": "Left image is the exact 2K Canny edge material; right image changes only by NK2E Canny strength.",
        "orig": rel_input(edge_2k_path),
        "orig_1mp": rel_input(edge_1mp_path),
        "bundle": f"{BUNDLE}_{slug}",
        "stage": "ControlNet OFF" if strength == 0 else f"Canny strength {strength:.2f}",
        "stage_idx": idx,
        "gen_secs": round(elapsed, 1),
        "width": width,
        "height": height,
        "resolution": f"{width}x{height}",
    }
    path.with_suffix(path.suffix + ".pingpong.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    request_json("/system_stats", timeout=5)
    scenarios = [source_hangul_subscribe(), source_luxury_sign(), source_comic_sfx()]
    results = []
    for scenario_idx, (slug, prompt, _base_path, edge_1mp_path, edge_2k_path) in enumerate(scenarios, 1):
        for stage_idx, (stage, strength) in enumerate(STAGES, 1):
            idx = scenario_idx * 100 + stage_idx
            workflow = build_workflow(slug, prompt, edge_1mp_path, edge_2k_path, stage, strength, idx)
            client_id = str(uuid.uuid4())
            t0 = time.time()
            queued = request_json("/prompt", {"prompt": workflow, "client_id": client_id}, timeout=30)
            prompt_id = queued["prompt_id"]
            print(f"[{slug} {stage_idx}/{len(STAGES)}] queued {stage} strength={strength} prompt_id={prompt_id}", flush=True)
            history = wait_for_prompt(prompt_id)
            elapsed = time.time() - t0
            paths = output_paths(history)
            if not paths:
                raise RuntimeError(f"No image output for {slug} {stage}")
            final = max(paths, key=lambda p: p.stat().st_mtime if p.exists() else 0)
            write_sidecar(final, slug, edge_1mp_path, edge_2k_path, stage, strength, stage_idx, elapsed)
            print(f"[{slug} {stage_idx}/{len(STAGES)}] done {final} ({elapsed:.1f}s)", flush=True)
            results.append(str(final))

    manifest = HUB_ROOT / "rehearsal_results" / f"{BUNDLE}_manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({"bundle": BUNDLE, "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"manifest={manifest}")


if __name__ == "__main__":
    main()
