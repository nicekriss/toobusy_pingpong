import argparse
import json
import subprocess
import sys
from pathlib import Path

import cv2
import torch


COMFY_ROOT = Path(r"C:\comfy\ComfyUI")
VFI_ROOT = COMFY_ROOT / "custom_nodes" / "ComfyUI-Frame-Interpolation"


def probe_video(path: Path) -> dict:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,r_frame_rate,duration,nb_frames",
        "-of",
        "json",
        str(path),
    ]
    return json.loads(subprocess.check_output(cmd, text=True, encoding="utf-8"))


def read_video(path: Path, max_frames: int = 0) -> tuple[torch.Tensor, float]:
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
    frames = []
    while True:
        ok, frame_bgr = cap.read()
        if not ok:
            break
        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        frames.append(torch.from_numpy(frame_rgb).float().div_(255.0))
        if max_frames and len(frames) >= max_frames:
            break
    cap.release()
    if len(frames) < 2:
        raise RuntimeError("RIFE interpolation needs at least 2 frames.")
    return torch.stack(frames, dim=0), float(fps)


def write_video(frames: torch.Tensor, path: Path, fps: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = frames.clamp(0, 1).mul(255).byte().cpu()
    height, width = frames.shape[1], frames.shape[2]
    writer = cv2.VideoWriter(
        str(path),
        cv2.VideoWriter_fourcc(*"mp4v"),
        fps,
        (width, height),
    )
    if not writer.isOpened():
        raise RuntimeError(f"Could not create video: {path}")
    for frame_rgb in frames:
        writer.write(cv2.cvtColor(frame_rgb.numpy(), cv2.COLOR_RGB2BGR))
    writer.release()


def main() -> None:
    parser = argparse.ArgumentParser(description="2x frame interpolation with ComfyUI RIFE VFI.")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--ckpt", default="rife49.pth")
    parser.add_argument("--dtype", default="bfloat16", choices=["float32", "float16", "bfloat16"])
    parser.add_argument("--batch-size", default=2, type=int)
    parser.add_argument("--clear-cache-after", default=12, type=int)
    parser.add_argument("--max-frames", default=0, type=int)
    args = parser.parse_args()

    sys.path.insert(0, str(COMFY_ROOT))
    sys.path.insert(0, str(VFI_ROOT))

    from vfi_models.rife import RIFE_VFI

    frames, src_fps = read_video(args.input, args.max_frames)
    model = RIFE_VFI()
    (interp_frames,) = model.vfi(
        ckpt_name=args.ckpt,
        frames=frames,
        clear_cache_after_n_frames=args.clear_cache_after,
        multiplier=2,
        fast_mode=True,
        ensemble=False,
        scale_factor=1.0,
        dtype=args.dtype,
        torch_compile=False,
        batch_size=args.batch_size,
    )
    write_video(interp_frames, args.output, src_fps * 2)
    print(json.dumps({
        "input": str(args.input),
        "output": str(args.output),
        "source_fps": src_fps,
        "output_fps": src_fps * 2,
        "source_frames": int(frames.shape[0]),
        "output_frames": int(interp_frames.shape[0]),
        "probe": probe_video(args.output),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
