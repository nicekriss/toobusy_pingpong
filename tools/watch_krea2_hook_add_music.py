import json
import subprocess
import time
import urllib.request
import uuid
from pathlib import Path


COMFY = "http://127.0.0.1:8188"
OUTPUT_ROOT = Path(r"C:\Users\kriss\AppData\Local\Comfy-Desktop\ComfyUI-Shared\output")
VIDEO_IN = OUTPUT_ROOT / "video" / "krea2_hook_orbit_controlnet_2k_48fps.mp4"
MUSIC_PREFIX = "audio/krea2_hook_epic_opening_ace15xl"
FINAL_VIDEO = OUTPUT_ROOT / "video" / "krea2_hook_orbit_controlnet_2k_48fps_epic_music.mp4"
WORKFLOW_OUT = Path(r"D:\working\pingpong\workflows\audio_ace_krea2_hook_epic_opening_API.json")
LOG = Path(r"D:\working\pingpong\logs\krea2_hook_music_watcher.log")


def log(message: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with LOG.open("a", encoding="utf-8") as f:
        f.write(f"[{stamp}] {message}\n")
    print(message, flush=True)


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
        log("requested Comfy memory free")
    except Exception as exc:
        log(f"memory free request skipped: {exc}")


def wait_for_video() -> None:
    last_size = -1
    stable_hits = 0
    while True:
        if VIDEO_IN.exists():
            size = VIDEO_IN.stat().st_size
            if size == last_size and size > 0:
                stable_hits += 1
            else:
                stable_hits = 0
                last_size = size
            if stable_hits >= 3:
                log(f"video ready: {VIDEO_IN} ({size} bytes)")
                return
        time.sleep(20)


def build_audio_workflow() -> dict:
    workflow = {
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "seed": ["109", 0],
                "steps": 8,
                "cfg": 1,
                "sampler_name": "euler",
                "scheduler": "simple",
                "denoise": 1,
                "model": ["78", 0],
                "positive": ["94", 0],
                "negative": ["47", 0],
                "latent_image": ["98", 0],
            },
        },
        "18": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["3", 0], "vae": ["106", 0]}},
        "47": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["94", 0]}},
        "78": {"class_type": "ModelSamplingAuraFlow", "inputs": {"shift": 3, "model": ["104", 0]}},
        "94": {
            "class_type": "TextEncodeAceStepAudio1.5",
            "inputs": {
                "tags": (
                    "Epic cinematic trailer opening, 140 BPM, massive orchestral brass, hybrid taiko drums, "
                    "deep sub impacts, heroic choir pads, cyberpunk synth risers, dramatic build, huge final hit, "
                    "premium YouTube hook intro, instrumental, no vocals, no lyrics"
                ),
                "lyrics": "[Instrumental]",
                "seed": ["109", 0],
                "bpm": 140,
                "duration": 8,
                "timesignature": "4",
                "language": "en",
                "keyscale": "E minor",
                "generate_audio_codes": True,
                "cfg_scale": 2.35,
                "temperature": 0.82,
                "top_p": 0.92,
                "top_k": 80,
                "min_p": 0.01,
                "clip": ["105", 0],
            },
        },
        "98": {"class_type": "EmptyAceStep1.5LatentAudio", "inputs": {"seconds": 8, "batch_size": 1}},
        "104": {"class_type": "UNETLoader", "inputs": {"unet_name": "aceStepAudioGen_v15XLTurbo.safetensors", "weight_dtype": "default"}},
        "105": {
            "class_type": "DualCLIPLoader",
            "inputs": {
                "clip_name1": "aceStepAudioGen_tencQwen06bAce15.safetensors",
                "clip_name2": "aceStepAudioGen_tencQwen4bAce15.safetensors",
                "type": "ace",
                "device": "default",
            },
        },
        "106": {"class_type": "VAELoader", "inputs": {"vae_name": "aceStepAudioGen_vae.safetensors"}},
        "107": {"class_type": "SaveAudioMP3", "inputs": {"filename_prefix": MUSIC_PREFIX, "quality": "V0", "audio": ["18", 0]}},
        "109": {"class_type": "PrimitiveInt", "inputs": {"value": int(time.strftime("%m%d%H%M"))}},
    }
    WORKFLOW_OUT.write_text(json.dumps(workflow, ensure_ascii=False, indent=2), encoding="utf-8")
    return workflow


def queue_workflow(workflow: dict) -> str:
    result = request_json("/prompt", {"prompt": workflow, "client_id": "codex-hook-music-" + uuid.uuid4().hex[:8]}, timeout=30)
    if result.get("node_errors"):
        raise RuntimeError(json.dumps(result["node_errors"], ensure_ascii=False, indent=2))
    return result["prompt_id"]


def wait_history(prompt_id: str) -> dict:
    while True:
        history = request_json(f"/history/{prompt_id}", timeout=10)
        item = history.get(prompt_id)
        if item:
            status = item.get("status", {})
            if status.get("completed") or status.get("status_str") == "success":
                return item
            if status.get("status_str") == "error":
                raise RuntimeError(json.dumps(status, ensure_ascii=False, indent=2))
        time.sleep(3)


def audio_path_from_history(item: dict) -> Path:
    outputs = item.get("outputs", {})
    audio = outputs.get("107", {}).get("audio", []) or outputs.get("107", {}).get("audios", [])
    if audio:
        entry = audio[0]
        return OUTPUT_ROOT / entry.get("subfolder", "") / entry["filename"]
    candidates = sorted((OUTPUT_ROOT / "audio").glob("krea2_hook_epic_opening_ace15xl*.mp3"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        raise RuntimeError("No generated music mp3 found.")
    return candidates[0]


def mux(video: Path, audio: Path, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-i",
            str(video),
            "-i",
            str(audio),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            str(out),
        ],
        check=True,
    )


def main() -> None:
    log("watcher started")
    wait_for_video()
    free_memory()
    workflow = build_audio_workflow()
    prompt_id = queue_workflow(workflow)
    log(f"queued music {prompt_id}")
    item = wait_history(prompt_id)
    audio = audio_path_from_history(item)
    log(f"music ready: {audio}")
    mux(VIDEO_IN, audio, FINAL_VIDEO)
    log(f"final muxed video: {FINAL_VIDEO}")


if __name__ == "__main__":
    main()
