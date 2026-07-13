from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageOps


INPUT_ROOT = Path(r"C:\Users\kriss\AppData\Local\Comfy-Desktop\ComfyUI-Shared\input")
OUTPUT_ROOT = Path(r"C:\Users\kriss\AppData\Local\Comfy-Desktop\ComfyUI-Shared\output")
HUB_ROOT = Path(r"D:\working\KREA2_ControlNet_DemoHub")

OUT_DIR = INPUT_ROOT / "krea2_clean_text_canny_sources"
HUB_DIR = HUB_ROOT / "control_sources"

BASES = {
    "controlnet_main": OUTPUT_ROOT / "pingpong" / "krea2_canny_controlnet_thumbnail_90_0710" / "krea2_controlnet_main_FINAL.png",
    "depth_vs_canny": OUTPUT_ROOT / "pingpong" / "krea2_canny_controlnet_thumbnail_90_0710" / "depth_vs_canny_FINAL.png",
    "text_image_canny": OUTPUT_ROOT / "pingpong" / "krea2_canny_controlnet_thumbnail_90_0710" / "text_plus_image_canny_FINAL.png",
}

SOURCES = [
    {
        "slug": "krea2_controlnet_clean",
        "base": BASES["controlnet_main"],
        "title": "KREA2\n\ucee8\ud2b8\ub864\ub137",
        "badge": "\ub4ed\uc2a4 + \uce90\ub2c8",
        "primary": (255, 245, 70),
        "secondary": (0, 235, 255),
        "badge_color": (255, 45, 132),
    },
    {
        "slug": "depth_vs_canny_clean",
        "base": BASES["depth_vs_canny"],
        "title": "\ub4ed\uc2a4 VS\n\uce90\ub2c8",
        "badge": "ControlNet",
        "primary": (255, 255, 255),
        "secondary": (255, 185, 30),
        "badge_color": (34, 240, 150),
    },
    {
        "slug": "text_image_canny_clean",
        "base": BASES["text_image_canny"],
        "title": "\uae00\uc790+\uc774\ubbf8\uc9c0\n\uce90\ub2c8\uc18c\uc2a4",
        "badge": "\ud55c\ubc29\uc5d0",
        "primary": (255, 235, 55),
        "secondary": (255, 255, 255),
        "badge_color": (0, 215, 255),
    },
]


def font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    candidates = ["malgunbd.ttf", "NotoSansKR-Bold.otf", "NotoSansKR-VF.ttf", "malgun.ttf", "gulim.ttc"]
    if not bold:
        candidates = ["malgun.ttf", "NotoSansKR-VF.ttf", "gulim.ttc"]
    for name in candidates:
        path = Path(r"C:\Windows\Fonts") / name
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def fit_text(draw: ImageDraw.ImageDraw, text: str, max_width: int, start_size: int) -> ImageFont.FreeTypeFont:
    size = start_size
    while size > 48:
        fnt = font(size, True)
        bbox = draw.textbbox((0, 0), text, font=fnt, stroke_width=0)
        if bbox[2] - bbox[0] <= max_width:
            return fnt
        size -= 6
    return font(size, True)


def render_source(item: dict) -> tuple[Path, Path]:
    size = (2048, 1152)
    base = item["base"]
    if not base.exists():
        base = INPUT_ROOT / "krea2_control_top7_landscape" / "control_depth_02_diagonal_runway_16x9.png"
    im = Image.open(base).convert("RGB")
    im = ImageOps.fit(im, size, method=Image.Resampling.LANCZOS, centering=(0.56, 0.5))
    im = ImageEnhance.Contrast(im).enhance(1.04)
    im = ImageEnhance.Color(im).enhance(1.04)

    overlay = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay, "RGBA")
    draw.rectangle((0, 0, 980, 1152), fill=(0, 0, 0, 150))
    draw.polygon([(820, 0), (1120, 0), (920, 1152), (690, 1152)], fill=(0, 0, 0, 80))

    y = 180
    for idx, line in enumerate(item["title"].splitlines()):
        fnt = fit_text(draw, line, 790, 238 if idx == 0 else 204)
        fill = item["primary"] if idx == 0 else item["secondary"]
        # Clean readable text, not an edge-map imitation. Canny will extract the outline later.
        draw.text((92, y + 12), line, font=fnt, fill=(0, 0, 0, 235), stroke_width=10, stroke_fill=(0, 0, 0, 240))
        draw.text((92, y), line, font=fnt, fill=(*fill, 255), stroke_width=4, stroke_fill=(255, 255, 255, 80))
        bbox = draw.textbbox((92, y), line, font=fnt, stroke_width=4)
        y = bbox[3] + 10

    badge_font = fit_text(draw, item["badge"], 690, 78)
    draw.rounded_rectangle((96, 770, 880, 878), radius=30, fill=(*item["badge_color"], 245))
    draw.text((136, 790), item["badge"], font=badge_font, fill=(8, 8, 10, 255))
    draw.rounded_rectangle((96, 918, 760, 1012), radius=18, outline=(255, 255, 255, 220), width=5)
    draw.text((132, 936), "KREA2 CONTROLNET", font=font(48, True), fill=(255, 255, 255, 245))

    combined = Image.alpha_composite(im.convert("RGBA"), overlay).convert("RGB")
    text_only = Image.new("RGB", size, (0, 0, 0))
    text_only.paste(Image.alpha_composite(Image.new("RGBA", size, (0, 0, 0, 255)), overlay).convert("RGB"))

    combined_path = OUT_DIR / f"{item['slug']}_clean_combined_2048x1152.png"
    text_path = OUT_DIR / f"{item['slug']}_clean_text_only_2048x1152.png"
    combined.save(combined_path)
    text_only.save(text_path)
    return combined_path, text_path


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    HUB_DIR.mkdir(parents=True, exist_ok=True)
    black = Image.new("RGB", (2048, 1152), (0, 0, 0))
    black_path = OUT_DIR / "black_no_extra_blend_2048x1152.png"
    black.save(black_path)
    paths = [black_path]
    for item in SOURCES:
        paths.extend(render_source(item))
    for path in paths:
        (HUB_DIR / path.name).write_bytes(path.read_bytes())
        print(path)


if __name__ == "__main__":
    main()
