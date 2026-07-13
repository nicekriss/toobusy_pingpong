import json
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
BUNDLE = "krea2_canny_nk2e_official_structure_probe_0710"
OUT_SUBDIR = f"pingpong/{BUNDLE}"

STAGES = [("S01_040", 0.4), ("S02_070", 0.7), ("S03_100", 1.0)]


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


def make_source() -> tuple[Path, Path]:
    input_dir = INPUT_ROOT / SOURCE_SUBDIR
    hub_dir = HUB_ROOT / "control_sources"
    input_dir.mkdir(parents=True, exist_ok=True)
    hub_dir.mkdir(parents=True, exist_ok=True)

    base = Image.new("RGB", (1024, 576), (0, 0, 0))
    d = ImageDraw.Draw(base, "RGBA")
    f1, f2 = font(210), font(170)
    for text, fnt, y in [("구독", f1, 52), ("좋아요", f2, 330)]:
        bbox = d.textbbox((0, 0), text, font=fnt, stroke_width=8)
        x = (1024 - (bbox[2] - bbox[0])) / 2
        d.text((x, y), text, font=fnt, fill=(255, 255, 255, 255), stroke_fill=(255, 255, 255, 255), stroke_width=8)
    base = base.filter(ImageFilter.GaussianBlur(0.2))
    edges = base.convert("L").filter(ImageFilter.FIND_EDGES).point(lambda p: 255 if p > 12 else 0).filter(ImageFilter.MaxFilter(3))
    edge = Image.new("RGB", base.size, (0, 0, 0))
    edge.paste((255, 255, 255), mask=edges)

    edge_1mp = input_dir / "official_probe_hangul_edges_1024x576.png"
    edge_2k = input_dir / "official_probe_hangul_edges_2048x1152.png"
    edge.save(edge_1mp)
    edge.resize((2048, 1152), Image.Resampling.NEAREST).save(edge_2k)
    for p in (edge_1mp, edge_2k):
        (hub_dir / p.name).write_bytes(p.read_bytes())
    return edge_1mp, edge_2k


def build(edge_1mp: Path, edge_2k: Path, stage: str, strength: float, idx: int) -> dict:
    prompt = (
        "A clean premium Korean neon sign shaped by the canny guide, exact readable Hangul words '구독 좋아요', "
        "white cyan glass tube letters on a black wall, high quality product photo, crisp typography, no people, no extra letters."
    )
    # This follows the NK2E example structure: the encoded reference is also the sampler latent image.
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
                "seed": 992000 + idx,
                "steps": 4,
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
        "12": {"class_type": "SaveImage", "inputs": {"filename_prefix": f"{OUT_SUBDIR}/official_probe_{stage}", "images": ["11", 0]}},
    }
    return workflow


def wait(pid: str, timeout: int = 600) -> dict:
    start = time.time()
    while time.time() - start < timeout:
        h = request_json(f"/history/{pid}", timeout=30)
        if pid in h:
            return h[pid]
        time.sleep(2)
    raise TimeoutError(pid)


def outputs(history: dict) -> list[Path]:
    out = []
    for node in (history.get("outputs") or {}).values():
        for img in node.get("images") or []:
            out.append(OUTPUT_ROOT / (img.get("subfolder") or "") / img["filename"])
    return out


def sidecar(path: Path, edge_1mp: Path, edge_2k: Path, stage_idx: int, strength: float, secs: float) -> None:
    with Image.open(path) as im:
        w, h = im.size
    meta = {
        "request": "KREA2 NK2E Canny official structure probe",
        "generated": f"Official NK2E example structure: reference latent is also sampler latent, strength {strength:.2f}",
        "mode": "image",
        "note": "Probe for original-author workflow structure. Result is 1MP only, no hires stage.",
        "orig": rel_input(edge_2k),
        "orig_1mp": rel_input(edge_1mp),
        "bundle": BUNDLE,
        "stage": f"Canny strength {strength:.2f}",
        "stage_idx": stage_idx,
        "gen_secs": round(secs, 1),
        "width": w,
        "height": h,
        "resolution": f"{w}x{h}",
    }
    path.with_suffix(path.suffix + ".pingpong.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    request_json("/system_stats", timeout=5)
    edge_1mp, edge_2k = make_source()
    results = []
    for i, (stage, strength) in enumerate(STAGES, 1):
        t0 = time.time()
        q = request_json("/prompt", {"prompt": build(edge_1mp, edge_2k, stage, strength, i), "client_id": str(uuid.uuid4())})
        pid = q["prompt_id"]
        print(f"[{i}/{len(STAGES)}] queued {stage} strength={strength} prompt_id={pid}", flush=True)
        hist = wait(pid)
        secs = time.time() - t0
        paths = outputs(hist)
        final = max(paths, key=lambda p: p.stat().st_mtime)
        sidecar(final, edge_1mp, edge_2k, i, strength, secs)
        print(f"[{i}/{len(STAGES)}] done {final} ({secs:.1f}s)", flush=True)
        results.append(str(final))
    manifest = HUB_ROOT / "rehearsal_results" / f"{BUNDLE}_manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({"bundle": BUNDLE, "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"manifest={manifest}")


if __name__ == "__main__":
    main()
