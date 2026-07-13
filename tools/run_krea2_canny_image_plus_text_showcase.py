import json
import time
import urllib.request
import uuid
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps


COMFY = "http://127.0.0.1:8188"
INPUT_ROOT = Path(r"C:\Users\kriss\AppData\Local\Comfy-Desktop\ComfyUI-Shared\input")
OUTPUT_ROOT = Path(r"C:\Users\kriss\AppData\Local\Comfy-Desktop\ComfyUI-Shared\output")
HUB_ROOT = Path(r"D:\working\KREA2_ControlNet_DemoHub")

BASE_IMAGE = Path(
    r"C:\Users\kriss\AppData\Local\Comfy-Desktop\ComfyUI-Shared\output\pingpong"
    r"\krea2_cn_strength_manga_pop_0709\krea2_cn_strength_manga_pop_0709_S04_075_00001_.png"
)
SOURCE_SUBDIR = "krea2_control_canny_showcase"
BUNDLE = "krea2_canny_image_plus_text_official_0710"
OUT_SUBDIR = f"pingpong/{BUNDLE}"

STAGES = [
    ("S01_040", 0.40),
    ("S02_070", 0.70),
    ("S03_090", 0.90),
    ("S04_110", 1.10),
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


def font(size: int) -> ImageFont.FreeTypeFont:
    for name in ("NotoSansKR-VF.ttf", "malgunbd.ttf", "gulim.ttc"):
        p = Path(r"C:\Windows\Fonts") / name
        if p.exists():
            return ImageFont.truetype(str(p), size=size)
    return ImageFont.load_default()


def text_mask(size: tuple[int, int]) -> Image.Image:
    w, h = size
    mask = Image.new("L", size, 0)
    d = ImageDraw.Draw(mask)
    f1 = font(175)
    f2 = font(125)
    # Put the typography over the quieter left/bottom area so it has room and still overlaps the illustration.
    items = [("구독", f1, 118, 740), ("좋아요", f2, 126, 915)]
    for text, fnt, x, y in items:
        d.text((x, y), text, font=fnt, fill=255, stroke_fill=255, stroke_width=10)
    mask = mask.filter(ImageFilter.MaxFilter(5))
    return mask


def make_sources() -> tuple[Path, Path, Path, Path]:
    input_dir = INPUT_ROOT / SOURCE_SUBDIR
    hub_dir = HUB_ROOT / "control_sources"
    input_dir.mkdir(parents=True, exist_ok=True)
    hub_dir.mkdir(parents=True, exist_ok=True)

    base = Image.open(BASE_IMAGE).convert("RGB").resize((2048, 1152), Image.Resampling.LANCZOS)
    base_name = "image_plus_text_base_manga_2048x1152.png"
    source_name = "image_plus_text_canny_source_2048x1152.png"
    edge_2k_name = "image_plus_text_edges_2k_2048x1152.png"
    edge_1mp_name = "image_plus_text_edges_1mp_1024x576.png"

    # Canny-like extraction from the illustration, then overlay a separate Hangul edge mask.
    gray = ImageOps.autocontrast(base.convert("L"))
    img_edges = gray.filter(ImageFilter.FIND_EDGES).point(lambda p: 255 if p > 38 else 0).filter(ImageFilter.MaxFilter(3))
    txt = text_mask(base.size)
    combined = Image.new("L", base.size, 0)
    combined = Image.composite(Image.new("L", base.size, 210), combined, img_edges)
    combined = Image.composite(Image.new("L", base.size, 255), combined, txt)
    combined = combined.filter(ImageFilter.MaxFilter(3))

    edge_rgb = Image.new("RGB", base.size, (0, 0, 0))
    edge_rgb.paste((245, 252, 255), mask=combined)

    # A readable guide preview: original illustration with visible typography guide.
    preview = base.copy()
    pd = ImageDraw.Draw(preview, "RGBA")
    pd.bitmap((0, 0), txt, fill=(255, 60, 170, 210))

    base_path = input_dir / base_name
    source_path = input_dir / source_name
    edge_2k_path = input_dir / edge_2k_name
    edge_1mp_path = input_dir / edge_1mp_name
    base.save(base_path)
    preview.save(source_path)
    edge_rgb.save(edge_2k_path)
    edge_rgb.resize((1024, 576), Image.Resampling.LANCZOS).save(edge_1mp_path)
    for src in (base_path, source_path, edge_2k_path, edge_1mp_path):
        (hub_dir / src.name).write_bytes(src.read_bytes())
    return base_path, source_path, edge_1mp_path, edge_2k_path


def build_workflow(edge_1mp: Path, edge_2k: Path, stage: str, strength: float, idx: int) -> dict:
    prompt = (
        "A high quality Korean pop manhwa YouTube thumbnail poster based on a dynamic close-up hero illustration, "
        "large readable Korean neon title overlay in the lower-left area, exact Hangul words '구독 좋아요', "
        "glossy ink lines, vivid cyan magenta yellow colors, clean heroic face, premium comic cover, crisp typography, "
        "no extra letters, no watermark, no insects."
    )
    # Official NK2E structure: encoded Canny guide is both reference and sampler latent.
    workflow = {
        "1": {"class_type": "LoadImage", "inputs": {"image": rel_input(edge_1mp)}},
        "2": {"class_type": "UNETLoader", "inputs": {"unet_name": "krea2_turbo_fp8_scaled.safetensors", "weight_dtype": "default"}},
        "3": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen3vl_4b_fp8_scaled.safetensors", "type": "krea2", "device": "default"}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": "qwen_image_vae.safetensors"}},
        "5": {"class_type": "VAEEncode", "inputs": {"pixels": ["1", 0], "vae": ["4", 0]}},
        "6": {"class_type": "NK2EInContextEditNode", "inputs": {"model": ["2", 0], "reference": ["5", 0]}},
        "7": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["6", 0], "lora_name": r"KREA2_ControlNet\NK2E-canny-v0.1.safetensors", "strength_model": strength}},
        "8": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["3", 0]}},
        "9": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["8", 0]}},
        "10": {
            "class_type": "KSampler",
            "inputs": {
                "seed": 1207000 + idx * 11,
                "steps": 6,
                "cfg": 1,
                "sampler_name": "euler",
                "scheduler": "simple",
                "denoise": 1,
                "model": ["7", 0],
                "positive": ["8", 0],
                "negative": ["9", 0],
                "latent_image": ["5", 0],
            },
        },
        "11": {"class_type": "VAEDecode", "inputs": {"samples": ["10", 0], "vae": ["4", 0]}},
        "12": {"class_type": "SaveImage", "inputs": {"filename_prefix": f"{OUT_SUBDIR}/image_plus_text_{stage}", "images": ["11", 0]}},
    }
    return workflow


def wait_for_prompt(prompt_id: str, timeout: int = 700) -> dict:
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


def write_sidecar(path: Path, source_path: Path, edge_1mp: Path, edge_2k: Path, stage_idx: int, strength: float, elapsed: float) -> None:
    with Image.open(path) as im:
        w, h = im.size
    meta = {
        "request": "KREA2 Canny image plus Hangul text official structure",
        "generated": f"Existing illustration edges plus Korean title edges, official NK2E latent/reference structure, strength {strength:.2f}",
        "mode": "image",
        "note": "Left image is the combined 2K Canny guide: illustration edges plus separate Hangul text edges. The sampler latent also starts from the 1MP Canny guide.",
        "orig": rel_input(edge_2k),
        "orig_1mp": rel_input(edge_1mp),
        "source_preview": rel_input(source_path),
        "bundle": BUNDLE,
        "stage": f"Canny strength {strength:.2f}",
        "stage_idx": stage_idx,
        "gen_secs": round(elapsed, 1),
        "width": w,
        "height": h,
        "resolution": f"{w}x{h}",
    }
    path.with_suffix(path.suffix + ".pingpong.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    request_json("/system_stats", timeout=5)
    _base_path, source_path, edge_1mp, edge_2k = make_sources()
    results = []
    for idx, (stage, strength) in enumerate(STAGES, 1):
        workflow = build_workflow(edge_1mp, edge_2k, stage, strength, idx)
        t0 = time.time()
        queued = request_json("/prompt", {"prompt": workflow, "client_id": str(uuid.uuid4())}, timeout=30)
        prompt_id = queued["prompt_id"]
        print(f"[{idx}/{len(STAGES)}] queued {stage} strength={strength} prompt_id={prompt_id}", flush=True)
        history = wait_for_prompt(prompt_id)
        elapsed = time.time() - t0
        paths = output_paths(history)
        if not paths:
            raise RuntimeError(f"No output for {stage}")
        final = max(paths, key=lambda p: p.stat().st_mtime if p.exists() else 0)
        write_sidecar(final, source_path, edge_1mp, edge_2k, idx, strength, elapsed)
        print(f"[{idx}/{len(STAGES)}] done {final} ({elapsed:.1f}s)", flush=True)
        results.append(str(final))
    manifest = HUB_ROOT / "rehearsal_results" / f"{BUNDLE}_manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({"bundle": BUNDLE, "edge_1mp": str(edge_1mp), "edge_2k": str(edge_2k), "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"manifest={manifest}")


if __name__ == "__main__":
    main()
