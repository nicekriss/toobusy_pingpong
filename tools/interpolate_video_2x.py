import json
import subprocess
import sys
from pathlib import Path


def main():
    cfg_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(r"D:\working\pingpong\workflows\video_frame_interpolate_2x_ffmpeg_minpolate.json")
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    src = Path(cfg["input_video"])
    dst = Path(cfg["output_video"])
    dst.parent.mkdir(parents=True, exist_ok=True)
    fps = int(cfg.get("target_fps", 48))
    vf = cfg.get("ffmpeg_filter") or f"minterpolate=fps={fps}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1"
    cmd = [
        "ffmpeg", "-y", "-hide_banner",
        "-i", str(src),
        "-vf", vf,
        "-an",
        "-c:v", cfg.get("video_codec", "libx264"),
        "-crf", str(cfg.get("crf", 18)),
        "-preset", cfg.get("preset", "slow"),
        "-pix_fmt", "yuv420p",
        str(dst),
    ]
    subprocess.run(cmd, check=True)
    print(dst)


if __name__ == "__main__":
    main()
