# -*- coding: utf-8 -*-
"""
너무바쁜베짱이 핑퐁 갤러리 대시보드 (로컬 서버)
- 생성 폴더를 실시간으로 읽어 이미지/영상/음악을 갤러리로 표시
- 생성 요청을 공유 큐(queue/)에 투입 → 봇(pingpong.py)이 순서대로 처리(GPU 충돌 방지)
- 숨김(복구 가능)/삭제(.trash 이동) 지원
실행: python dashboard.py  (또는 대시보드.bat)
"""
import os, sys, json, time, base64, re, mimetypes, threading, webbrowser, struct, zlib, subprocess
import urllib.request
import urllib.parse
import html as html_lib
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(HERE, "config.json")
CFG = json.load(open(CFG_PATH, encoding="utf-8"))
CUSTOM = CFG.get("custom_workflows", {}) or {}
def _auto(k, *parts):
    base = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Comfy-Desktop", "ComfyUI-Shared")
    return CFG.get(k) or os.path.join(base, *parts)
OUTDIR   = _auto("comfy_output_dir", "output")
INPUTDIR = _auto("comfy_input_dir", "input")
COMFY    = CFG.get("comfy_api", "http://127.0.0.1:8188").rstrip("/")
LMAPI    = CFG.get("lmstudio_api", "http://127.0.0.1:1234").rstrip("/")
GALLERY  = os.path.join(OUTDIR, "pingpong")
TRASH    = os.path.join(GALLERY, ".trash")
ALIVE    = os.path.join(GALLERY, ".alive")
QUEUE    = os.path.join(HERE, "queue")
HIDDEN   = os.path.join(HERE, "dashboard_hidden.json")
PRESETS  = os.path.join(HERE, "dashboard_reference_presets.json")
PROGRESS = os.path.join(HERE, "dashboard_comfy_progress.json")
PORT     = int(CFG.get("dashboard_port", 8910))
EVENTS   = []
CPU_LAST = {"t": 0, "idle": 0, "kernel": 0, "user": 0, "cpu": 0}
YOUTUBE_CHANNEL_ID = "UC4xLnbcb7AxfJ8wdkiobaKQ"
YT_CACHE = {"t": 0, "data": None}
LORA_CACHE = {"t": 0, "data": []}
MODE_STATUS_CACHE = {"t": 0, "data": {}}
AUDIO_LYRICS_CACHE = {}
HIDDEN_SUBPROCESS_FLAGS = 0
if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW"):
    HIDDEN_SUBPROCESS_FLAGS = subprocess.CREATE_NO_WINDOW

class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True

def run_hidden(args, **kwargs):
    if HIDDEN_SUBPROCESS_FLAGS and "creationflags" not in kwargs:
        kwargs["creationflags"] = HIDDEN_SUBPROCESS_FLAGS
    return subprocess.run(args, **kwargs)

IMG_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
VID_EXT = {".mp4", ".webm", ".mov", ".mkv"}
AUD_EXT = {".mp3", ".flac", ".wav", ".ogg", ".m4a"}

def event(msg):
    EVENTS.append({"t": time.strftime("%H:%M:%S"), "msg": msg})
    del EVENTS[:-80]

def read_config():
    try:
        return json.load(open(CFG_PATH, encoding="utf-8"))
    except Exception:
        return dict(CFG)

def write_config(data):
    global CFG, CUSTOM, LMAPI
    tmp = CFG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, CFG_PATH)
    CFG = data
    CUSTOM = CFG.get("custom_workflows", {}) or {}
    LMAPI = CFG.get("lmstudio_api", "http://127.0.0.1:1234").rstrip("/")
    MODE_STATUS_CACHE["t"] = 0

def _add_model(out, seen, model_id, label=None, source=""):
    model_id = (model_id or "").strip()
    if not model_id or model_id in seen:
        return
    if source != "config" and re.search(r"\b(embed|embedding|rerank)\b", model_id, re.I):
        return
    seen.add(model_id)
    out.append({"id": model_id, "label": label or model_id, "source": source})

def lmstudio_models():
    cfg = read_config()
    current = cfg.get("llm_model", "")
    out, seen, errors = [], set(), []
    _add_model(out, seen, current, source="config")
    try:
        run_hidden(["lms", "server", "start"], capture_output=True, text=True, encoding="utf-8", timeout=10)
    except Exception as e:
        errors.append("lms server: " + str(e)[:120])
    try:
        r = run_hidden(["lms", "ls", "--json"], capture_output=True, text=True, encoding="utf-8", timeout=20)
        if r.returncode == 0 and r.stdout.strip():
            data = json.loads(r.stdout)
            rows = data if isinstance(data, list) else data.get("models") or data.get("data") or []
            for m in rows:
                if isinstance(m, str):
                    _add_model(out, seen, m, source="lms")
                elif isinstance(m, dict):
                    mid = m.get("modelKey") or m.get("id") or m.get("path") or m.get("name")
                    label = m.get("displayName") or m.get("name") or mid
                    _add_model(out, seen, mid, label=label, source="lms")
    except Exception as e:
        errors.append("lms ls json: " + str(e)[:120])
    if len(out) <= (1 if current else 0):
        try:
            r = run_hidden(["lms", "ls"], capture_output=True, text=True, encoding="utf-8", timeout=20)
            if r.returncode == 0:
                for line in r.stdout.splitlines():
                    line = line.strip()
                    if not line or line.lower().startswith(("you have", "listing", "identifier", "model")):
                        continue
                    line = re.sub(r"^[*>\-\s]+", "", line)
                    mid = re.split(r"\s{2,}|\t", line)[0].strip()
                    if "/" in mid or "\\" in mid or re.search(r"\b(qwen|gemma|llama|mistral|deepseek|phi|yi|openchat)\b", mid, re.I):
                        _add_model(out, seen, mid, source="lms")
        except Exception as e:
            errors.append("lms ls: " + str(e)[:120])
    try:
        data = json.load(urllib.request.urlopen(LMAPI + "/v1/models", timeout=3))
        for m in data.get("data", []):
            _add_model(out, seen, m.get("id"), source="loaded")
    except Exception as e:
        errors.append("api: " + str(e)[:120])
    return {"current": current, "models": out, "errors": errors[-3:]}

def _filetime_to_int(ft):
    return (ft.dwHighDateTime << 32) + ft.dwLowDateTime

def cpu_percent():
    if os.name != "nt":
        return 0
    try:
        import ctypes
        from ctypes import wintypes
        idle = wintypes.FILETIME(); kernel = wintypes.FILETIME(); user = wintypes.FILETIME()
        ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user))
        now = time.time()
        i, k, u = _filetime_to_int(idle), _filetime_to_int(kernel), _filetime_to_int(user)
        if CPU_LAST["t"]:
            idle_delta = i - CPU_LAST["idle"]
            total_delta = (k - CPU_LAST["kernel"]) + (u - CPU_LAST["user"])
            if total_delta > 0:
                CPU_LAST["cpu"] = max(0, min(100, round((1 - idle_delta / total_delta) * 100)))
        CPU_LAST.update({"t": now, "idle": i, "kernel": k, "user": u})
    except Exception:
        pass
    return CPU_LAST["cpu"]

def memory_info():
    if os.name != "nt":
        return {"used": 0, "total": 0, "pct": 0}
    try:
        import ctypes
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]
        stat = MEMORYSTATUSEX(); stat.dwLength = ctypes.sizeof(stat)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
        total = stat.ullTotalPhys / (1024 ** 3)
        used = (stat.ullTotalPhys - stat.ullAvailPhys) / (1024 ** 3)
        return {"used": round(used, 1), "total": round(total, 1), "pct": int(stat.dwMemoryLoad)}
    except Exception:
        return {"used": 0, "total": 0, "pct": 0}

def gpu_info():
    try:
        r = run_hidden(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total,utilization.gpu", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=2
        )
        if r.returncode == 0 and r.stdout.strip():
            used, total, util = [int(x.strip()) for x in r.stdout.splitlines()[0].split(",")[:3]]
            return {"used": used, "total": total, "pct": round(used * 100 / total), "util": util}
    except Exception:
        pass
    return {"used": 0, "total": 0, "pct": 0, "util": 0}

def comfy_snapshot():
    snap = {"ok": False, "running": 0, "pending": 0, "recent": []}
    try:
        q = json.load(urllib.request.urlopen(COMFY + "/queue", timeout=2))
        snap["ok"] = True
        snap["running"] = len(q.get("queue_running") or [])
        snap["pending"] = len(q.get("queue_pending") or [])
    except Exception as e:
        snap["recent"].append("Comfy queue error: " + str(e)[:120])
    try:
        hist = json.load(urllib.request.urlopen(COMFY + "/history", timeout=2))
        for pid, item in list(hist.items())[-5:]:
            status_msg = item.get("status", {}).get("status_str", "done")
            snap["recent"].append(f"{pid[:8]} {status_msg}")
    except Exception:
        pass
    return snap

def comfy_progress():
    try:
        data = json.load(open(PROGRESS, encoding="utf-8"))
        age = time.time() - float(data.get("t") or 0)
        if age > 90:
            return {"active": False}
        status = data.get("status", "")
        active = status not in ("done", "error") or age < 12
        data["active"] = active
        data["age"] = round(age, 1)
        return data
    except Exception:
        return {"active": False}

def local_queue_count():
    try:
        return len([f for f in os.listdir(QUEUE) if f.endswith(".json")])
    except FileNotFoundError:
        return 0
    except Exception:
        return 0

def system_info():
    snap = comfy_snapshot()
    snap["local_queue"] = local_queue_count()
    snap["progress"] = comfy_progress()
    return {"cpu": cpu_percent(), "ram": memory_info(), "gpu": gpu_info(), "comfy": snap}

def comfy_log():
    snap = comfy_snapshot()
    local_q = local_queue_count()
    lines = [f"Comfy {'ONLINE' if snap['ok'] else 'OFFLINE'} | running {snap['running']} | pending {snap['pending']} | pingpong queue {local_q}"]
    prog = snap.get("progress") or {}
    if prog.get("active"):
        lines.append(f"progress {prog.get('pct', 0)}% {prog.get('status', '')} {prog.get('text', '')}")
    lines += snap["recent"]
    lines += [f"{e['t']} {e['msg']}" for e in EVENTS[-20:]]
    return lines[-40:]

def youtube_latest():
    if YT_CACHE["data"] and time.time() - YT_CACHE["t"] < 1800:
        return YT_CACHE["data"]
    data = {"ok": False}
    try:
        url = "https://www.youtube.com/feeds/videos.xml?channel_id=" + YOUTUBE_CHANNEL_ID
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        xml = urllib.request.urlopen(req, timeout=5).read()
        root = ET.fromstring(xml)
        ns = {
            "atom": "http://www.w3.org/2005/Atom",
            "media": "http://search.yahoo.com/mrss/",
            "yt": "http://www.youtube.com/xml/schemas/2015",
        }
        entry = root.find("atom:entry", ns)
        if entry is not None:
            vid = (entry.findtext("yt:videoId", default="", namespaces=ns) or "").strip()
            title = (entry.findtext("atom:title", default="", namespaces=ns) or "").strip()
            link = entry.find("atom:link", ns)
            href = link.get("href") if link is not None else ("https://www.youtube.com/watch?v=" + vid)
            group = entry.find("media:group", ns)
            thumb = ""
            if group is not None:
                th = group.find("media:thumbnail", ns)
                if th is not None:
                    thumb = th.get("url", "")
            data = {"ok": True, "title": title, "url": href, "thumb": thumb, "videoId": vid}
    except Exception as e:
        data = {"ok": False, "err": str(e)[:120]}
    if not data.get("ok"):
        try:
            url = "https://www.youtube.com/channel/" + YOUTUBE_CHANNEL_ID + "/videos"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "ko,en;q=0.9"})
            html = urllib.request.urlopen(req, timeout=8).read().decode("utf-8", "replace")
            m = re.search(r'"videoId":"([^"]+)".{0,3000}?"title":\{"content":"(.*?)"\}', html, flags=re.S)
            if m:
                vid = m.group(1)
                title = html_lib.unescape(m.group(2))
                if "\\u" in title:
                    title = title.encode("utf-8").decode("unicode_escape")
                data = {
                    "ok": True,
                    "title": title,
                    "url": "https://www.youtube.com/watch?v=" + vid,
                    "thumb": "https://i.ytimg.com/vi/" + vid + "/hqdefault.jpg",
                    "videoId": vid,
                }
        except Exception as e:
            data = {"ok": False, "err": str(e)[:120]}
    YT_CACHE.update({"t": time.time(), "data": data})
    return data

def _png_text_chunks(path):
    out = {}
    try:
        with open(path, "rb") as f:
            if f.read(8) != b"\x89PNG\r\n\x1a\n":
                return out
            while True:
                raw = f.read(8)
                if len(raw) < 8:
                    return out
                n, ctype = struct.unpack(">I4s", raw)
                data = f.read(n)
                f.read(4)
                if ctype == b"IEND":
                    return out
                if ctype == b"tEXt" and b"\0" in data:
                    key, val = data.split(b"\0", 1)
                    out[key.decode("latin-1", "replace")] = val.decode("utf-8", "replace")
                elif ctype == b"zTXt" and b"\0" in data:
                    key, rest = data.split(b"\0", 1)
                    if rest:
                        out[key.decode("latin-1", "replace")] = zlib.decompress(rest[1:]).decode("utf-8", "replace")
                elif ctype == b"iTXt" and b"\0" in data:
                    parts = data.split(b"\0", 5)
                    if len(parts) == 6:
                        key, flag, _method, _lang, _translated, text = parts
                        if flag == b"\x01":
                            text = zlib.decompress(text)
                        out[key.decode("utf-8", "replace")] = text.decode("utf-8", "replace")
    except Exception:
        return out

def _clean_prompt(s):
    s = (s or "").strip()
    s = re.sub(r"<think>[\s\S]*?</think>", " ", s, flags=re.I)
    s = re.sub(r"^\s*(?:under\s+\d+\s+words?\??\s*)?(?:let'?s\s+count|word\s+count)\s*[:\-]\s*", "", s, flags=re.I)
    s = re.sub(r"\s*\(\s*\d+\s+words?\s*\)\s*\.?\s*$", "", s, flags=re.I)
    s = re.sub(r'^\s*(?:revised\s+draft|final\s+version|prompt)\s*[:*,-]*\s*', '', s, flags=re.I)
    bad = re.compile(
        r"(?:\b(wait|actually|however|therefore|usually|given|instruction|system prompt|the input|the user|"
        r"i should|i will|i need|let'?s|this means|looking at|since it|if i|we need|missing elements|under \d+ words?|word count)\b"
        r"|^\s*(style|missing elements)\s*:)",
        re.I,
    )
    parts = re.split(r"(?<=[.!?])\s+", s.replace("\n", " "))
    good = [p.strip().strip('"').strip("*").strip() for p in parts if p.strip() and not bad.search(p)]
    if good:
        s = " ".join(good[:3])
    return re.sub(r"\s+", " ", s).strip()[:2000]

def image_prompt(path):
    if os.path.splitext(path)[1].lower() != ".png":
        return ""
    raw = _png_text_chunks(path).get("prompt")
    if not raw:
        return ""
    try:
        wf = json.loads(raw)
    except Exception:
        return ""
    candidates = []
    for node in wf.values():
        inputs = node.get("inputs", {})
        cls = node.get("class_type", "")
        if isinstance(inputs.get("board_json"), str):
            try:
                board = json.loads(inputs["board_json"])
                for it in board.get("items", []):
                    if it.get("type") == "text" and it.get("text"):
                        candidates.append(it["text"])
            except Exception:
                pass
        if isinstance(inputs.get("timeline_data"), str):
            try:
                gp = json.loads(inputs["timeline_data"]).get("global_prompt")
                if gp:
                    candidates.append(gp)
            except Exception:
                pass
        for key in ("positive", "text", "value"):
            val = inputs.get(key)
            if isinstance(val, str) and len(val.strip()) > 8:
                if "expert prompt engineer" in val.lower():
                    continue
                if cls == "PrimitiveStringMultiline" or key in ("positive", "text"):
                    candidates.append(val)
    candidates = [_clean_prompt(x) for x in candidates if _clean_prompt(x)]
    if not candidates:
        return ""
    return max(candidates, key=len)

def media_meta(path):
    try:
        meta = json.load(open(path + ".pingpong.json", encoding="utf-8"))
        return {
            "request": _clean_prompt(meta.get("request", "")),
            "generated": _clean_prompt(meta.get("generated", "")),
            "mode": meta.get("mode", ""),
        }
    except Exception:
        return {"request": "", "generated": "", "mode": ""}

def _decode_audio_text(enc, data):
    if not data:
        return ""
    codec = {0: "latin-1", 1: "utf-16", 2: "utf-16-be", 3: "utf-8"}.get(enc, "utf-8")
    return data.decode(codec, "replace").replace("\x00", "").strip()

def _split_encoded_text(enc, data):
    if enc in (1, 2):
        for sep in (b"\x00\x00",):
            i = data.find(sep)
            if i >= 0:
                return data[:i], data[i + len(sep):]
    for sep in (b"\x00",):
        i = data.find(sep)
        if i >= 0:
            return data[:i], data[i + len(sep):]
    return b"", data

def _id3_size(raw):
    return ((raw[0] & 0x7f) << 21) | ((raw[1] & 0x7f) << 14) | ((raw[2] & 0x7f) << 7) | (raw[3] & 0x7f)

def _audio_lyrics_id3(path):
    try:
        with open(path, "rb") as f:
            head = f.read(10)
            if len(head) < 10 or head[:3] != b"ID3":
                return ""
            major = head[3]
            tag = f.read(_id3_size(head[6:10]))
    except Exception:
        return ""
    pos = 0
    while pos + 10 <= len(tag):
        fid = tag[pos:pos + 4].decode("latin-1", "ignore")
        if not fid.strip("\x00"):
            break
        size = _id3_size(tag[pos + 4:pos + 8]) if major == 4 else int.from_bytes(tag[pos + 4:pos + 8], "big")
        data = tag[pos + 10:pos + 10 + size]
        pos += 10 + max(0, size)
        if not data:
            continue
        if fid in ("USLT", "SYLT") and len(data) > 4:
            enc = data[0]
            _, text = _split_encoded_text(enc, data[4:])
            out = _decode_audio_text(enc, text)
            if out:
                return out
        if fid == "TXXX" and len(data) > 1:
            enc = data[0]
            desc, text = _split_encoded_text(enc, data[1:])
            if "lyric" in _decode_audio_text(enc, desc).lower():
                out = _decode_audio_text(enc, text)
                if out:
                    return out
    return ""

def _audio_lyrics_flac(path):
    try:
        with open(path, "rb") as f:
            if f.read(4) != b"fLaC":
                return ""
            last = False
            while not last:
                h = f.read(4)
                if len(h) < 4:
                    break
                last = bool(h[0] & 0x80)
                typ = h[0] & 0x7f
                size = int.from_bytes(h[1:4], "big")
                block = f.read(size)
                if typ != 4 or len(block) < 8:
                    continue
                p = 0
                vendor_len = int.from_bytes(block[p:p + 4], "little"); p += 4 + vendor_len
                n = int.from_bytes(block[p:p + 4], "little"); p += 4
                for _ in range(n):
                    ln = int.from_bytes(block[p:p + 4], "little"); p += 4
                    text = block[p:p + ln].decode("utf-8", "replace"); p += ln
                    key, _, val = text.partition("=")
                    if key.lower() in ("lyrics", "unsyncedlyrics", "description") and val.strip():
                        return val.strip()
    except Exception:
        pass
    return ""

def _audio_lyrics_sidecar(path):
    try:
        raw = json.load(open(path + ".pingpong.json", encoding="utf-8")).get("generated", "")
    except Exception:
        return ""
    m = re.search(r"LYRICS:\s*(.+)", raw or "", flags=re.I | re.S)
    return m.group(1).strip() if m else ""

def audio_lyrics(path):
    try:
        mt = os.path.getmtime(path)
    except OSError:
        mt = 0
    cached = AUDIO_LYRICS_CACHE.get(path)
    if cached and cached.get("mtime") == mt:
        return cached.get("lyrics", "")
    ext = os.path.splitext(path)[1].lower()
    text = _audio_lyrics_id3(path) if ext in (".mp3", ".m4a") else ""
    if not text and ext == ".flac":
        text = _audio_lyrics_flac(path)
    if not text:
        text = _audio_lyrics_sidecar(path)
    text = re.sub(r"\r\n?", "\n", text or "").strip()
    text = text[:12000]
    AUDIO_LYRICS_CACHE[path] = {"mtime": mt, "lyrics": text}
    return text

def load_hidden():
    try:
        return set(json.load(open(HIDDEN, encoding="utf-8")))
    except Exception:
        return set()
def save_hidden(s):
    try:
        json.dump(sorted(s), open(HIDDEN, "w", encoding="utf-8"))
    except Exception:
        pass

def load_reference_presets():
    try:
        data = json.load(open(PRESETS, encoding="utf-8"))
        return data if isinstance(data, dict) else {"presets": []}
    except Exception:
        return {"presets": []}

def save_reference_presets(data):
    tmp = PRESETS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, PRESETS)

def reference_preset_list():
    data = load_reference_presets()
    rows = []
    for p in data.get("presets", []):
        assets = p.get("assets", []) if isinstance(p, dict) else []
        rows.append({
            "id": p.get("id", ""),
            "name": p.get("name", ""),
            "updated_at": p.get("updated_at", ""),
            "count": len(assets),
        })
    return {"presets": rows}

def reference_preset_get(pid):
    for p in load_reference_presets().get("presets", []):
        if p.get("id") == pid:
            return p
    return None

def reference_preset_save(name, assets):
    name = (name or "").strip()[:80] or time.strftime("preset_%m%d_%H%M%S")
    pid = re.sub(r"[^0-9A-Za-z가-힣_.-]+", "_", name).strip("_")[:60] or "preset"
    if not pid.endswith("_" + time.strftime("%m%d")):
        pid = pid + "_" + time.strftime("%m%d_%H%M%S")
    data = load_reference_presets()
    rows = [p for p in data.get("presets", []) if p.get("id") != pid]
    rows.append({
        "id": pid,
        "name": name,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "assets": assets if isinstance(assets, list) else [],
    })
    data["presets"] = rows[-50:]
    save_reference_presets(data)
    return rows[-1]

def scan(page=1, per=48):
    hidden = load_hidden()
    page = max(1, int(page or 1))
    per = max(12, min(96, int(per or 48)))
    out = {"images": [], "videos": [], "audios": [], "hiddenCount": 0,
           "imagePage": page, "imagePer": per, "imageTotal": 0, "imagePages": 1}
    if not os.path.isdir(GALLERY):
        return out
    images = []
    for root, dirs, files in os.walk(GALLERY):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for fn in files:
            if fn.startswith("."):
                continue
            ext = os.path.splitext(fn)[1].lower()
            kind = "images" if ext in IMG_EXT else "videos" if ext in VID_EXT else "audios" if ext in AUD_EXT else None
            if not kind:
                continue
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, GALLERY).replace("\\", "/")
            if rel in hidden:
                out["hiddenCount"] += 1
                continue
            try:
                mt = os.path.getmtime(full)
            except OSError:
                continue
            item = {"name": fn, "rel": rel,
                    "url": "/media/" + urllib.parse.quote(rel), "mtime": mt}
            if kind == "images":
                item["full"] = full
                images.append(item)
            else:
                if kind == "audios":
                    item["lyrics"] = audio_lyrics(full)
                out[kind].append(item)
    images.sort(key=lambda x: x["mtime"], reverse=True)
    out["imageTotal"] = len(images)
    out["imagePages"] = max(1, (len(images) + per - 1) // per)
    page = min(page, out["imagePages"])
    out["imagePage"] = page
    start = (page - 1) * per
    for item in images[start:start + per]:
        full = item.pop("full")
        meta = media_meta(full)
        item["request"] = meta["request"]
        item["prompt"] = meta["generated"] or image_prompt(full)
        item["mode"] = meta["mode"]
        out["images"].append(item)
    for k in ("videos", "audios"):
        out[k].sort(key=lambda x: x["mtime"], reverse=True)
    return out

def status():
    alive = False
    try:
        alive = (time.time() - os.path.getmtime(ALIVE)) < 90
    except OSError:
        pass
    gen = False
    try:
        q = json.load(urllib.request.urlopen(COMFY + "/queue", timeout=3))
        gen = bool(q.get("queue_running") or q.get("queue_pending"))
    except Exception:
        pass
    try:
        queued = len([f for f in os.listdir(QUEUE) if f.endswith(".json")])
    except FileNotFoundError:
        queued = 0
    return {"alive": alive or gen, "heartbeat": alive, "generating": gen, "queued": queued}

def custom_modes():
    out = []
    for name, spec in CUSTOM.items():
        trigger = spec.get("trigger") or ("/" + name)
        out.append({
            "name": name,
            "mode": "custom:" + name,
            "label": name,
            "trigger": trigger,
            "type": spec.get("type", "image"),
            "llm": spec.get("llm", "none"),
            "image_inputs": len(spec.get("image_nodes", [])),
            "ratio": bool(spec.get("ratio_node")),
        })
    return out

def _norm_model(name):
    return str(name or "").replace("\\", "/").lower()

def _comfy_json(path, timeout=5):
    return json.load(urllib.request.urlopen(COMFY + path, timeout=timeout))

def comfy_is_online():
    try:
        _comfy_json("/system_stats", timeout=3)
        return True
    except Exception:
        return False

def comfy_node_info(cls):
    try:
        data = _comfy_json("/object_info/" + urllib.parse.quote(str(cls), safe=""), timeout=5)
        return data.get(cls) if isinstance(data, dict) else None
    except Exception:
        return None

def comfy_input_options(info, field):
    if not info:
        return None
    for section in ("required", "optional"):
        try:
            opts = info["input"][section][field][0]
            if isinstance(opts, list):
                return opts
        except Exception:
            pass
    return None

def comfy_has_model(expected, options):
    if not expected or options is None:
        return True
    return _norm_model(expected) in [_norm_model(o) for o in options]

def workflow_classes_and_models(path):
    data = json.load(open(path, encoding="utf-8"))
    classes = []
    model_refs = []
    model_ext = (".safetensors", ".ckpt", ".gguf", ".pt", ".pth", ".bin", ".onnx", ".vae", ".lora")
    if not isinstance(data, dict):
        return classes, model_refs
    for node_id, node in data.items():
        if not isinstance(node, dict):
            continue
        cls = node.get("class_type")
        if cls:
            classes.append(str(cls))
        inputs = node.get("inputs") or {}
        if not isinstance(inputs, dict):
            continue
        for field, value in inputs.items():
            if not isinstance(value, str):
                continue
            v = value.strip()
            low = v.lower()
            if low.endswith(model_ext) or "\\" in v or "/" in v:
                model_refs.append((str(node_id), str(cls or ""), str(field), v))
    return sorted(set(classes)), model_refs

BUILTIN_MODE_CHECKS = {
    "image": {
        "label": "Z-Image Turbo",
        "nodes": ["ToobusyZImageTurbo", "ToobusyHiresUpscale"],
        "loader": "ToobusyZImageTurbo",
        "field": "model_name",
        "model_key": "zit",
    },
    "video": {
        "label": "LTX Director",
        "nodes": ["LTXDirector", "UnetLoaderGGUF", "VAEDecodeTiled"],
        "loader": "UnetLoaderGGUF",
        "field": "unet_name",
        "model_key": "ltx_gguf",
    },
    "song": {
        "label": "ACE Step Audio",
        "nodes": ["TextEncodeAceStepAudio1.5"],
        "loader": "UNETLoader",
        "field": "unet_name",
        "model_key": "ace",
    },
    "klein": {
        "label": "Flux2 Klein",
        "nodes": ["ToobusyFlux2Klein", "ToobusyReferenceBoard"],
        "loader": "ToobusyFlux2Klein",
        "field": "model_name",
        "model_key": "klein",
    },
    "faceswap": {
        "label": "Flux2 Klein Face",
        "nodes": ["ToobusyFlux2Klein", "ToobusyReferenceBoard"],
        "loader": "ToobusyFlux2Klein",
        "field": "model_name",
        "model_key": "klein",
    },
}

def builtin_mode_status(mode, online):
    check = BUILTIN_MODE_CHECKS.get(mode) or {}
    missing = []
    need = []
    if not online:
        missing.append("ComfyUI 연결")
    for node in check.get("nodes", []):
        need.append(node)
        if online and not comfy_node_info(node):
            missing.append("커스텀 노드: " + node)
    expected = (CFG.get("models") or {}).get(check.get("model_key"), "")
    overrides = (read_config().get("model_overrides", {}) or {})
    path = workflow_path_for_mode(mode)
    if path and os.path.isfile(path) and check.get("loader") and check.get("field"):
        try:
            _, refs = workflow_classes_and_models(path)
            for node_id, cls, field, value in refs:
                if cls == check.get("loader") and field == check.get("field"):
                    expected = overrides.get(model_override_key(mode, node_id, field), expected or value)
                    break
        except Exception:
            pass
    if expected:
        need.append("모델: " + expected)
        if online and check.get("loader") and check.get("field"):
            opts = comfy_input_options(comfy_node_info(check["loader"]), check["field"])
            if not comfy_has_model(expected, opts):
                missing.append("모델: " + expected)
    return {
        "label": check.get("label", mode),
        "ready": not missing,
        "needs": need,
        "missing": missing,
    }

def custom_mode_status(name, spec, online):
    missing = []
    need = []
    mode = "custom:" + name
    overrides = (read_config().get("model_overrides", {}) or {})
    rel = spec.get("file") or ""
    path = os.path.join(HERE, rel)
    if rel:
        need.append("워크플로우: " + rel)
    if not rel or not os.path.isfile(path):
        missing.append("워크플로우 파일: " + (rel or "미설정"))
        return {"label": name, "ready": False, "needs": need, "missing": missing}
    if not online:
        missing.append("ComfyUI 연결")
        return {"label": name, "ready": False, "needs": need, "missing": missing}
    try:
        classes, model_refs = workflow_classes_and_models(path)
    except Exception as e:
        return {"label": name, "ready": False, "needs": need, "missing": ["워크플로우 읽기 실패: " + str(e)[:120]]}
    for cls in classes:
        if not comfy_node_info(cls):
            missing.append("커스텀 노드: " + cls)
            if len(missing) >= 8:
                break
    if len(missing) < 8:
        for node_id, cls, field, value in model_refs:
            value = overrides.get(model_override_key(mode, node_id, field), value)
            info = comfy_node_info(cls)
            opts = comfy_input_options(info, field)
            if opts is not None:
                need.append("모델: " + value)
                if not comfy_has_model(value, opts):
                    missing.append("모델: " + value)
            if len(missing) >= 8:
                break
    if len(missing) >= 8:
        missing.append("...더 있음")
    return {
        "label": name,
        "ready": not missing,
        "needs": sorted(set(need)),
        "missing": missing,
    }

def mode_statuses():
    if time.time() - MODE_STATUS_CACHE["t"] < 20:
        return MODE_STATUS_CACHE["data"]
    online = comfy_is_online()
    out = {mode: builtin_mode_status(mode, online) for mode in BUILTIN_MODE_CHECKS}
    for name, spec in CUSTOM.items():
        if isinstance(spec, dict):
            out["custom:" + name] = custom_mode_status(name, spec, online)
    MODE_STATUS_CACHE.update({"t": time.time(), "data": out})
    return out

def comfy_loras():
    if time.time() - LORA_CACHE["t"] < 30:
        return LORA_CACHE["data"]
    names = []
    try:
        data = json.load(urllib.request.urlopen(COMFY + "/object_info/LoraLoader", timeout=5))
        info = data.get("LoraLoader", data) if isinstance(data, dict) else {}
        required = (((info.get("input") or {}).get("required") or {}) if isinstance(info, dict) else {})
        raw = required.get("lora_name") or []
        if isinstance(raw, list) and raw and isinstance(raw[0], list):
            names = [str(x) for x in raw[0] if str(x).strip()]
    except Exception:
        names = []
    if not names:
        try:
            data = json.load(urllib.request.urlopen(COMFY + "/object_info", timeout=8))
            for info in (data or {}).values():
                required = (((info.get("input") or {}).get("required") or {}) if isinstance(info, dict) else {})
                raw = required.get("lora_name") or []
                if isinstance(raw, list) and raw and isinstance(raw[0], list):
                    names.extend(str(x) for x in raw[0] if str(x).strip())
        except Exception:
            pass
    names = sorted(set(names), key=str.lower)
    LORA_CACHE.update({"t": time.time(), "data": names})
    return names

BUILTIN_WORKFLOW_FILES = {
    "image": "workflows/toobusy_zimgt.json",
    "video": "workflows/LTX_Director_2_Workflow_ggufdis_API.json",
    "song": "workflows/audio_ace_step1_5_xl_turbo_API.json",
    "klein": "workflows/toobusy_flux2klein_vram.json",
    "faceswap": "workflows/toobusy_flux2klein_vram.json",
}

def model_override_key(mode, node, field):
    return "|".join([str(mode or ""), str(node or ""), str(field or "")])

def workflow_path_for_mode(mode):
    if mode in BUILTIN_WORKFLOW_FILES:
        return os.path.join(HERE, BUILTIN_WORKFLOW_FILES[mode])
    if isinstance(mode, str) and mode.startswith("custom:"):
        name = mode.split(":", 1)[1]
        spec = CUSTOM.get(name)
        if not spec:
            for k, v in CUSTOM.items():
                if str(k).lower() == name.lower():
                    spec = v
                    break
        if spec and spec.get("file"):
            return os.path.join(HERE, spec["file"])
    return None

def model_fields_for_mode(mode):
    path = workflow_path_for_mode(mode)
    if not path or not os.path.isfile(path):
        return {"ok": False, "fields": [], "err": "workflow not found"}
    cfg = read_config()
    overrides = cfg.get("model_overrides", {}) or {}
    try:
        _, refs = workflow_classes_and_models(path)
    except Exception as e:
        return {"ok": False, "fields": [], "err": str(e)}
    fields = []
    seen = set()
    for node_id, cls, field, value in refs:
        key = model_override_key(mode, node_id, field)
        if key in seen:
            continue
        seen.add(key)
        opts = comfy_input_options(comfy_node_info(cls), field) or []
        if not opts:
            continue
        fields.append({
            "key": key,
            "mode": mode,
            "node": node_id,
            "class": cls,
            "field": field,
            "original": value,
            "current": overrides.get(key, value),
            "options": opts,
        })
    return {"ok": True, "fields": fields}

def requested_image_count(text):
    text = text or ""
    m = re.search(r"(?<!\d)(10|[2-9])\s*(?:장|개|컷|枚|images?|pics?|pictures?)", text, flags=re.I)
    if m:
        return max(1, min(10, int(m.group(1))))
    kor = {"두": 2, "세": 3, "네": 4, "다섯": 5, "여섯": 6, "일곱": 7, "여덟": 8, "아홉": 9, "열": 10}
    for word, n in kor.items():
        if re.search(word + r"\s*(?:장|개|컷)", text):
            return n
    return 1

def enqueue(job):
    os.makedirs(QUEUE, exist_ok=True)
    job["source"] = "dashboard"
    fn = "%d_%04d.json" % (int(time.time() * 1000), int.from_bytes(os.urandom(2), "big"))
    json.dump(job, open(os.path.join(QUEUE, fn), "w", encoding="utf-8"), ensure_ascii=False)

def save_dataurl(durl, idx):
    m = re.match(r"data:image/\w+;base64,(.+)", durl, re.S)
    if not m:
        return None
    ts = time.strftime("%m%d_%H%M%S") + "_%d" % idx
    rel = "toobusy_reference_board/images/dash_%s.jpg" % ts
    dest = os.path.join(INPUTDIR, "toobusy_reference_board", "images", "dash_%s.jpg" % ts)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as f:
        f.write(base64.b64decode(m.group(1)))
    return rel

def save_reference_assets(assets, imgs):
    rels = [save_dataurl(d, i) for i, d in enumerate(imgs)]
    rels = [r for r in rels if r]
    image_assets = [a for a in (assets or []) if isinstance(a, dict) and a.get("kind") == "image"]
    out = []
    default_roles = ["character_a", "face_a", "background_a", "pose_a", "style_a", "prop_a"]
    passthrough = (
        "bg_remove_enabled", "bg_remove_model", "bg_remove_background",
        "face_erase_enabled", "face_keep_enabled", "face_erase_fill",
        "face_erase_expand", "face_erase_feather",
        "face_lora_enabled", "face_lora_name", "face_lora_strength",
    )
    for i, rel in enumerate(rels):
        meta = image_assets[i] if i < len(image_assets) else {}
        role = meta.get("role") or default_roles[min(i, len(default_roles) - 1)]
        item = {
            "rel": rel,
            "role": role,
            "enabled": meta.get("enabled", True),
            "name": meta.get("name") or role.replace("_", " ").title(),
            "note": meta.get("note", ""),
        }
        for key in passthrough:
            if key in meta:
                item[key] = meta[key]
        out.append(item)
    for meta in (assets or []):
        if not isinstance(meta, dict):
            continue
        if meta.get("kind") == "lora" or meta.get("type") == "lora":
            name = (meta.get("lora_name") or meta.get("name") or "").strip()
            if not name:
                continue
            out.append({
                "type": "lora",
                "kind": "lora",
                "role": meta.get("role") or "lora_a",
                "name": meta.get("name") or name,
                "lora_name": name,
                "lora_strength": meta.get("lora_strength", 1.0),
                "lora_enabled": meta.get("lora_enabled", meta.get("enabled", True)),
            })
    return out

def save_director_asset(item, idx):
    if isinstance(item, dict) and item.get("kind") == "text":
        out = {
            "kind": "text",
            "name": item.get("name") or "Text Segment",
            "prompt": item.get("prompt") or item.get("text") or "",
        }
        for key in ("start", "length", "trimStart"):
            if key in item:
                out[key] = item[key]
        if item.get("id"):
            out["id"] = item["id"]
        return out
    if isinstance(item, dict) and item.get("gallery_rel"):
        src = safe_path(item.get("gallery_rel"))
        if not src or not os.path.isfile(src):
            return None
        ext = os.path.splitext(src)[1] or ".png"
        mime = (mimetypes.guess_type(src)[0] or "").lower()
        if mime.startswith("audio/"):
            kind = "audio"
        elif mime.startswith("video/"):
            kind = "video"
        else:
            kind = "image"
        ts = time.strftime("%m%d_%H%M%S") + "_%d" % idx
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", os.path.splitext(item.get("name", ""))[0])[:40] or "gallery"
        rel = "whatdreamscost/dash_%s_%s%s" % (ts, safe_name, ext)
        dest = os.path.join(INPUTDIR, "whatdreamscost", "dash_%s_%s%s" % (ts, safe_name, ext))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy2(src, dest)
        out = {"kind": kind, "rel": rel, "name": item.get("name") or os.path.basename(rel)}
        for key in ("start", "length", "trimStart", "isEndFrame", "videoStrength", "videoAttentionStrength", "resampleMode"):
            if key in item:
                out[key] = item[key]
        return out
    durl = item.get("data") if isinstance(item, dict) else item
    name = item.get("name", "") if isinstance(item, dict) else ""
    m = re.match(r"data:([^;]+);base64,(.+)", durl or "", re.S)
    if not m:
        return None
    mime, payload = m.group(1).lower(), m.group(2)
    if mime.startswith("image/"):
        kind = "image"; ext = mimetypes.guess_extension(mime) or ".png"
    elif mime.startswith("video/"):
        kind = "video"; ext = mimetypes.guess_extension(mime) or ".mp4"
    elif mime.startswith("audio/"):
        kind = "audio"; ext = mimetypes.guess_extension(mime) or ".wav"
    else:
        return None
    ts = time.strftime("%m%d_%H%M%S") + "_%d" % idx
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", os.path.splitext(name)[0])[:40] or "asset"
    rel = "whatdreamscost/dash_%s_%s%s" % (ts, safe_name, ext)
    dest = os.path.join(INPUTDIR, "whatdreamscost", "dash_%s_%s%s" % (ts, safe_name, ext))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as f:
        f.write(base64.b64decode(payload))
    out = {"kind": kind, "rel": rel, "name": name or os.path.basename(rel)}
    for key in ("start", "length", "trimStart", "videoStrength", "videoAttentionStrength", "resampleMode"):
        if isinstance(item, dict) and key in item:
            out[key] = item[key]
    return out

def safe_path(rel):
    full = os.path.normpath(os.path.join(GALLERY, rel))
    if not full.startswith(os.path.normpath(GALLERY)):
        return None
    return full

def restart_self():
    code = (
        "import subprocess,sys,time;"
        "time.sleep(1.2);"
        "flags=getattr(subprocess,'CREATE_NO_WINDOW',0)|getattr(subprocess,'CREATE_NEW_PROCESS_GROUP',0)|getattr(subprocess,'DETACHED_PROCESS',0);"
        "subprocess.Popen([sys.argv[1], sys.argv[2]], cwd=sys.argv[3], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)"
    )
    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS | HIDDEN_SUBPROCESS_FLAGS
    subprocess.Popen([sys.executable, "-c", code, sys.executable, __file__, HERE],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=flags)
    threading.Timer(0.8, lambda: os._exit(0)).start()


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _body(self):
        n = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        p = parsed.path
        if p == "/" or p == "/index.html":
            b = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
        elif p == "/api/list":
            qs = urllib.parse.parse_qs(parsed.query)
            self._json(scan(qs.get("page", ["1"])[0], qs.get("per", ["48"])[0]))
        elif p == "/api/status":
            self._json(status())
        elif p == "/api/modes":
            self._json({"custom": custom_modes(), "status": mode_statuses()})
        elif p == "/api/llm_models":
            self._json(lmstudio_models())
        elif p == "/api/loras":
            self._json({"loras": comfy_loras()})
        elif p == "/api/model_fields":
            qs = urllib.parse.parse_qs(parsed.query)
            self._json(model_fields_for_mode(qs.get("mode", [""])[0]))
        elif p == "/api/system":
            self._json(system_info())
        elif p == "/api/comfy_log":
            self._json({"lines": comfy_log()})
        elif p == "/api/youtube_latest":
            self._json(youtube_latest())
        elif p == "/api/reference_presets":
            self._json(reference_preset_list())
        elif p == "/api/reference_preset":
            qs = urllib.parse.parse_qs(parsed.query)
            preset = reference_preset_get(qs.get("id", [""])[0])
            if not preset:
                self._json({"ok": False, "err": "preset not found"}, 404)
            else:
                self._json({"ok": True, "preset": preset})
        elif p.startswith("/media/"):
            self.serve_media(urllib.parse.unquote(p[len("/media/"):]))
        else:
            self.send_error(404)

    def do_POST(self):
        p = urllib.parse.urlparse(self.path).path
        try:
            body = self._body()
        except Exception:
            return self._json({"ok": False, "err": "bad body"}, 400)
        if p == "/api/generate":
            mode = body.get("mode", "image")
            text = body.get("text", "")
            imgs = body.get("images", [])
            assets = body.get("assets", [])
            settings = body.get("settings", {}) if isinstance(body.get("settings", {}), dict) else {}
            if mode in ("klein", "faceswap"):
                refs = save_reference_assets(assets, imgs)
                rels = [r.get("rel") for r in refs if r.get("rel")]
                if mode == "klein":
                    if not rels:
                        return self._json({"ok": False, "err": "사진 필요"}, 400)
                    enqueue({"mode": "klein_board", "reference_assets": refs, "text": text, "settings": settings})
                else:
                    if len(rels) < 2:
                        return self._json({"ok": False, "err": "사진 2장 필요"}, 400)
                    enqueue({"mode": "faceswap_board", "reference_assets": refs, "text": text, "settings": settings})
            else:
                job = {"mode": mode, "text": text, "settings": settings}
                if mode == "image":
                    count = requested_image_count(text)
                    if count > 1:
                        job = {"mode": "image_fanout", "text": text, "count": count, "settings": settings}
                if mode == "video" and assets:
                    saved_assets = [save_director_asset(a, i) for i, a in enumerate(assets)]
                    job["director_assets"] = [a for a in saved_assets if a]
                if isinstance(mode, str) and mode.startswith("custom:") and imgs:
                    rels = [save_dataurl(d, i) for i, d in enumerate(imgs)]
                    job["image_refs"] = [r for r in rels if r]
                enqueue(job)
            event(f"queued {mode}: {text[:80]}")
            return self._json({"ok": True})
        if p == "/api/open_gallery":
            try:
                os.makedirs(GALLERY, exist_ok=True)
                os.startfile(GALLERY)
                event("opened gallery folder")
                return self._json({"ok": True})
            except Exception as e:
                return self._json({"ok": False, "err": str(e)}, 500)
        if p == "/api/interrupt":
            try:
                req = urllib.request.Request(COMFY + "/interrupt", data=b"{}", headers={"Content-Type": "application/json"}, method="POST")
                urllib.request.urlopen(req, timeout=5).read()
                event("comfy interrupt requested")
                return self._json({"ok": True})
            except Exception as e:
                return self._json({"ok": False, "err": str(e)}, 500)
        if p == "/api/llm_model":
            model = (body.get("model") or "").strip()
            if not model:
                return self._json({"ok": False, "err": "모델 이름이 비어 있어요"}, 400)
            try:
                cfg = read_config()
                cfg["llm_model"] = model
                write_config(cfg)
                event("llm model selected: " + model)
                return self._json({"ok": True, "model": model})
            except Exception as e:
                return self._json({"ok": False, "err": str(e)}, 500)
        if p == "/api/restart_dashboard":
            event("dashboard restart requested")
            restart_self()
            return self._json({"ok": True})
        if p == "/api/model_override":
            mode = (body.get("mode") or "").strip()
            node = (body.get("node") or "").strip()
            field = (body.get("field") or "").strip()
            value = (body.get("value") or "").strip()
            if not mode or not node or not field:
                return self._json({"ok": False, "err": "missing model override key"}, 400)
            try:
                cfg = read_config()
                overrides = cfg.setdefault("model_overrides", {})
                key = model_override_key(mode, node, field)
                if value:
                    overrides[key] = value
                else:
                    overrides.pop(key, None)
                write_config(cfg)
                event("model override saved: " + key)
                return self._json({"ok": True})
            except Exception as e:
                return self._json({"ok": False, "err": str(e)}, 500)
        if p == "/api/reference_preset_save":
            try:
                preset = reference_preset_save(body.get("name", ""), body.get("assets", []))
                event("saved reference preset: " + preset.get("name", ""))
                return self._json({"ok": True, "preset": preset})
            except Exception as e:
                return self._json({"ok": False, "err": str(e)}, 500)
        if p == "/api/hide":
            h = load_hidden(); h.add(body.get("rel", "")); save_hidden(h)
            return self._json({"ok": True})
        if p == "/api/unhide_all":
            save_hidden(set())
            return self._json({"ok": True})
        if p == "/api/delete":
            full = safe_path(body.get("rel", ""))
            if full and os.path.isfile(full):
                os.makedirs(TRASH, exist_ok=True)
                dest = os.path.join(TRASH, os.path.basename(full))
                if os.path.exists(dest):
                    dest += "_" + str(int(time.time()))
                try:
                    os.replace(full, dest)
                    event("deleted " + os.path.basename(full))
                except Exception as e: return self._json({"ok": False, "err": str(e)}, 500)
            return self._json({"ok": True})
        self.send_error(404)

    def serve_media(self, rel):
        full = safe_path(rel)
        if not full or not os.path.isfile(full):
            return self.send_error(404)
        size = os.path.getsize(full)
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        rng = self.headers.get("Range")
        start, end = 0, size - 1
        if rng:
            m = re.match(r"bytes=(\d+)-(\d*)", rng)
            if m:
                start = int(m.group(1))
                if m.group(2):
                    end = int(m.group(2))
        end = min(end, size - 1)
        length = end - start + 1
        self.send_response(206 if rng else 200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if rng:
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        self.end_headers()
        with open(full, "rb") as f:
            f.seek(start)
            remain = length
            while remain > 0:
                chunk = f.read(min(65536, remain))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    break
                remain -= len(chunk)


PAGE = r'''<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>너무바쁜베짱이 · 핑퐁 갤러리</title>
<link href="https://fonts.googleapis.com/css2?family=Press+Start+2P&family=VT323&display=swap" rel="stylesheet">
<style>
:root{--ink:#ece9ff;--mut:#a79fd6;--pink:#ff5d8f;--cyan:#56e1ff;--pur:#9d7bff;--grn:#8dffb0;--amb:#ffd166;--b1:#171228;--b2:#241b3e;--ln:rgba(255,255,255,.12)}
*{box-sizing:border-box}
body{margin:0;background:#0a0814;color:var(--ink);font-family:'VT323',monospace;font-size:18px;padding:18px}body.modalopen{overflow:hidden}
.pix{font-family:'Press Start 2P',monospace}
.wrap{max-width:1480px;margin:0 auto}
.topstick{position:sticky;top:0;z-index:20;background:#0a0814;padding-top:12px;padding-bottom:10px;border-bottom:1px solid rgba(255,255,255,.08)}
.bar{display:flex;align-items:center;justify-content:space-between;margin-bottom:14px}
.brand{font-size:15px;line-height:1.9;color:var(--cyan)}.brand b{color:var(--pink)}.brand small{display:block;font-size:9px;color:var(--mut)}
.stat{display:flex;align-items:center;gap:8px;font-size:10px;color:var(--mut)}
.heart{width:46px;height:40px;display:inline-block}
.heart.on{animation:beat 1s infinite}@keyframes beat{0%,100%{transform:scale(1)}45%{transform:scale(1.32)}}
.dot{width:8px;height:8px;border-radius:50%;display:inline-block;background:#555}
.dot.a{background:var(--grn);box-shadow:0 0 8px var(--grn);animation:blink 1s steps(2) infinite}@keyframes blink{50%{opacity:.25}}
.genbar{display:flex;gap:8px;align-items:center;margin-bottom:16px;background:var(--b1);border:1px solid var(--pur);border-radius:10px;padding:10px}
.msel,.gi{background:#0a0814;border:1px solid var(--ln);color:var(--ink);border-radius:7px;height:38px;font-family:'VT323';font-size:19px;padding:0 8px}
.gi{flex:1}.gi::placeholder{color:var(--mut)}
.up{background:#0a0814;border:1px solid var(--ln);color:var(--mut);height:38px;padding:0 10px;border-radius:7px;cursor:pointer;font-family:'VT323';font-size:17px}.up:hover{color:var(--cyan);border-color:var(--cyan)}
.genb{background:var(--pink);border:none;color:#220812;font-family:'Press Start 2P';font-size:10px;border-radius:7px;padding:0 14px;height:38px;cursor:pointer}.genb:hover{background:#ff85a8}
.folderb{background:var(--b2);border:1px solid var(--ln);color:var(--ink);font-family:'VT323';font-size:17px;border-radius:7px;padding:0 10px;height:38px;cursor:pointer}.folderb:hover:not(:disabled){color:var(--cyan);border-color:var(--cyan)}.folderb:disabled{opacity:.38;cursor:default;color:var(--mut);border-color:var(--ln)}
.modehint{display:none;margin:-8px 0 14px;padding:8px 10px;border:1px solid var(--ln);border-radius:8px;background:rgba(17,13,32,.72);color:var(--mut);font-size:16px;line-height:1.25}.modehint.on{display:block}.modehint.ok{border-color:rgba(141,255,176,.32);color:var(--grn)}.modehint.bad{border-color:rgba(255,93,143,.48);color:var(--amb)}.modehint b{font-family:'Press Start 2P';font-size:9px;color:var(--cyan);margin-right:8px}.modehint .miss{color:var(--pink)}
.modehint{position:relative;padding-right:118px}.modelToggle{position:absolute;right:8px;top:7px;background:var(--b2);border:1px solid var(--ln);color:var(--mut);border-radius:6px;height:25px;padding:0 8px;font-family:'VT323';font-size:14px;cursor:pointer}.modelToggle:hover,.modelToggle.warn{border-color:var(--amb);color:var(--amb)}
.modelPanel{display:none;margin:-8px 0 14px;padding:9px 10px;background:rgba(10,8,20,.9);border:1px solid var(--ln);border-radius:9px;color:var(--mut);gap:8px;align-items:center;flex-wrap:wrap}.modelPanel.on{display:flex}.modelPanel label{display:flex;align-items:center;gap:6px;font-size:15px}.modelPanel .ok{font-size:9px;color:var(--amb);margin-right:2px}.modelPanel select{background:#0a0814;border:1px solid var(--ln);color:var(--ink);border-radius:6px;height:28px;font-family:'VT323';font-size:16px;padding:0 7px;max-width:260px}.modelPanel select:focus{outline:none;border-color:var(--cyan)}
.llmbar{display:flex;align-items:center;gap:8px;margin:-8px 0 14px;color:var(--mut);font-size:16px}.llmbar .lk{font-size:9px;color:var(--cyan);min-width:42px}.llmbar select{flex:1;min-width:0;background:#0a0814;border:1px solid var(--ln);color:var(--ink);border-radius:7px;height:30px;font-family:'VT323';font-size:16px;padding:0 8px}.llmbar select:focus{outline:none;border-color:var(--cyan)}.llmbar button{background:var(--b2);border:1px solid var(--ln);color:var(--ink);border-radius:6px;height:30px;padding:0 9px;font-family:'VT323';font-size:15px;cursor:pointer}.llmbar button:hover{border-color:var(--cyan);color:var(--cyan)}.llmstat{max-width:280px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.optbar{display:none;align-items:center;gap:8px;margin:-8px 0 14px;padding:8px 10px;background:rgba(17,13,32,.72);border:1px solid var(--ln);border-radius:9px;color:var(--mut);flex-wrap:wrap}.optbar.on{display:flex}.optbar label{display:flex;align-items:center;gap:6px;font-size:15px}.optbar .ok{font-size:9px;color:var(--amb);margin-right:2px}.optbar select,.optbar input{background:#0a0814;border:1px solid var(--ln);color:var(--ink);border-radius:6px;height:28px;font-family:'VT323';font-size:16px;padding:0 7px}.optbar select:focus,.optbar input:focus{outline:none;border-color:var(--cyan)}.optbar input{width:66px}.optgrp{display:none;gap:8px;align-items:center;flex-wrap:wrap}.optgrp.on{display:flex}
.assets{display:none;gap:6px;align-items:center;margin:-8px 0 12px;flex-wrap:wrap}.assets.on{display:flex}.asset{display:flex;align-items:center;gap:6px;background:var(--b1);border:1px solid var(--ln);border-radius:7px;padding:4px 7px;font-size:15px}.asset b{color:var(--cyan);font-size:12px}.asset select,.asset input{background:#0a0814;border:1px solid var(--ln);color:var(--ink);border-radius:5px;height:24px;font-family:'VT323';font-size:14px}.asset .short{width:54px}.asset .lora-name{width:150px}.asset .use{display:flex;align-items:center;gap:3px;color:var(--amb);font-size:12px}.asset .use input{accent-color:var(--pink);height:auto}.asset.off{opacity:.58}.asset button{border:none;background:rgba(13,11,22,.85);color:var(--ink);border-radius:5px;cursor:pointer}.asset button:hover{background:var(--amb);color:#0a0814}
.director{display:none;margin:-4px 0 14px;border:1px solid var(--pur);border-radius:10px;background:rgba(17,13,32,.72);overflow:hidden}.director.on{display:block}.dhead{display:flex;justify-content:space-between;align-items:center;padding:9px 12px;border-bottom:1px solid var(--ln);font-size:10px;color:var(--cyan)}.dhead button{background:transparent;border:1px solid var(--ln);color:var(--mut);border-radius:6px;height:26px;padding:0 8px;font-family:'VT323';cursor:pointer}.dhead button:hover{color:var(--pink);border-color:var(--pink)}
.dgrid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;padding:10px}.dslot{min-width:0;border:1px solid var(--ln);border-radius:8px;background:#0a0814;padding:9px}.dtop{display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:8px}.dtop b{font-size:9px;color:var(--amb)}.dtop button{background:var(--b2);border:1px solid var(--ln);color:var(--ink);border-radius:6px;height:28px;padding:0 8px;font-family:'VT323';font-size:15px;cursor:pointer}.dtop button:hover{color:var(--cyan);border-color:var(--cyan)}
.dlane{display:flex;flex-direction:column;gap:6px;min-height:44px}.ditem{display:flex;align-items:center;gap:7px;border:1px solid #241d42;border-radius:6px;padding:5px 6px;font-size:15px;background:rgba(255,255,255,.02)}.ditem .thumb{width:38px;height:30px;border-radius:5px;background:#161228;object-fit:cover;display:flex;align-items:center;justify-content:center;color:var(--pink);font-size:15px;flex:none}.ditem span{min-width:0;flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.ditem button{width:24px;height:24px;border:none;border-radius:5px;background:rgba(13,11,22,.85);color:var(--ink);cursor:pointer}.ditem button:hover{background:var(--amb);color:#0a0814}.dhint{color:var(--mut);font-size:14px;line-height:1.25;padding:3px 1px}
.tl{padding:0 10px 10px}.tlbar{height:20px;border:1px solid var(--ln);border-radius:7px;background:#0a0814;position:relative;overflow:hidden}.tlseg{position:absolute;top:3px;height:14px;border-radius:4px;background:var(--cyan);opacity:.8}.tlseg.video{background:var(--pink)}.tlseg.audio{background:var(--amb)}.tlcap{display:flex;justify-content:space-between;color:var(--mut);font-size:13px;margin-top:4px}.ditem{flex-wrap:wrap}.ditem .dmain{display:flex;align-items:center;gap:7px;width:100%}.dtime{display:grid;grid-template-columns:repeat(3,1fr);gap:5px;width:100%;padding-left:45px}.dtime label{font-size:9px;color:var(--mut);display:flex;flex-direction:column;gap:2px}.dtime input{min-width:0;background:#0a0814;border:1px solid var(--ln);border-radius:5px;color:var(--ink);font-family:'VT323';font-size:15px;height:24px;padding:0 5px}.dtime input:focus{outline:none;border-color:var(--cyan)}
.dtbar{display:flex;align-items:center;gap:8px;padding:10px;border-bottom:1px solid var(--ln);flex-wrap:wrap}.dtbar button{background:var(--b2);border:1px solid var(--ln);color:var(--ink);border-radius:6px;height:30px;padding:0 9px;font-family:'VT323';font-size:15px;cursor:pointer}.dtbar button:hover{border-color:var(--cyan);color:var(--cyan)}.dtbar .hint{color:var(--mut);font-size:15px;margin-left:auto}.dtwrap{padding:10px}.dtruler{position:relative;height:24px;margin-left:88px;border-bottom:1px solid var(--ln);color:var(--mut);font-size:13px}.dtruler span{position:absolute;top:0;transform:translateX(-50%)}.dtlane{display:grid;grid-template-columns:80px minmax(0,1fr);gap:8px;align-items:center;margin-top:8px}.dtlabel{font-size:9px;color:var(--amb);text-align:right}.dttrack{position:relative;height:58px;background:#0a0814;border:1px solid var(--ln);border-radius:8px;overflow:hidden;background-image:linear-gradient(90deg,rgba(255,255,255,.04) 1px,transparent 1px);background-size:10% 100%}#track-main{height:152px;background-image:linear-gradient(180deg,transparent 0 93px,rgba(255,255,255,.08) 93px,rgba(255,255,255,.08) 94px,transparent 94px),linear-gradient(90deg,rgba(255,255,255,.04) 1px,transparent 1px);background-size:100% 100%,10% 100%}#track-main::before{content:"KEYFRAME";position:absolute;left:8px;top:6px;color:var(--mut);font-size:12px}#track-main::after{content:"TEXT";position:absolute;left:8px;top:99px;color:var(--mut);font-size:12px}.dtblock{position:absolute;top:8px;height:40px;min-width:18px;border:1px solid rgba(255,255,255,.2);border-radius:7px;background:var(--cyan);color:#07111a;cursor:grab;box-shadow:0 6px 16px rgba(0,0,0,.25);overflow:hidden}.dtblock.video{background:var(--pink);color:#220812}.dtblock.audio{background:var(--amb);color:#201200}.dtblock.text{background:var(--pur);color:#090612}.dtblock.image{background:#0d0a16;color:#fff;min-width:96px}.dtblock.image::after{content:"";position:absolute;inset:0;background:linear-gradient(180deg,rgba(0,0,0,.08),rgba(0,0,0,.72));pointer-events:none}.dtthumb{position:absolute;inset:0;width:100%;height:100%;object-fit:cover}.dttrack#track-main .dtblock{height:76px}.dttrack#track-main .dtblock.text{top:104px;height:36px}.dttrack#track-main .dtblock.image{top:16px}.dttrack#track-main .dtname,.dttrack#track-main .dttime{z-index:2;text-shadow:0 1px 3px #000}.dttrack#track-main .dtname{top:auto;bottom:19px;color:#fff}.dttrack#track-main .dttime{bottom:4px;color:#d9d3ff}.dttrack#track-main .dtblock.text .dtname{top:4px;bottom:auto}.dttrack#track-main .dtblock.text .dttime{bottom:3px}.dttrack#track-main .dth,.dttrack#track-main .dtx{z-index:3}.dtblock.dragging{cursor:grabbing;z-index:5;filter:brightness(1.12)}.dtname{position:absolute;left:18px;right:18px;top:4px;font-size:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.dttime{position:absolute;left:18px;right:18px;bottom:3px;font-size:12px;opacity:.84}.dth{position:absolute;top:0;width:12px;height:100%;cursor:ew-resize;background:rgba(0,0,0,.14)}.dth.l{left:0}.dth.r{right:0}.dtx{position:absolute;right:3px;top:2px;border:none;background:rgba(0,0,0,.18);color:inherit;border-radius:4px;width:16px;height:16px;line-height:12px;cursor:pointer}.dtx:hover{background:rgba(0,0,0,.35)}.dtedit{display:none;margin:10px;border:1px solid var(--ln);border-radius:8px;background:#0a0814;padding:8px;gap:8px;align-items:center}.dtedit.on{display:flex}.dtedit textarea{flex:1;min-height:52px;background:#0a0814;border:1px solid var(--ln);color:var(--ink);border-radius:6px;font-family:'VT323';font-size:16px;padding:6px;resize:vertical}.dtedit span{color:var(--mut);font-size:15px}.dtedit button{background:var(--b2);border:1px solid var(--ln);color:var(--ink);border-radius:6px;height:30px;padding:0 9px;font-family:'VT323';font-size:15px}
.dtblock.text{background:rgba(175,123,255,.34);border-color:rgba(175,123,255,.78);color:#fff;backdrop-filter:blur(2px)}
.dttrack.drop{border-color:var(--cyan);box-shadow:0 0 22px rgba(86,225,255,.22)}
.card[draggable=true],.arow[draggable=true]{cursor:grab}.card[draggable=true]:active,.arow[draggable=true]:active{cursor:grabbing}.videoMode .wrap{max-width:min(1760px,calc(100vw - 40px));display:grid;grid-template-columns:minmax(230px,320px) minmax(680px,1fr) 330px;gap:14px;align-items:start}.videoMode .topstick{grid-column:2;grid-row:1}.videoMode .content{display:contents}.videoMode .maincol{grid-column:1;grid-row:1;min-width:0;position:sticky;top:16px;max-height:calc(100vh - 32px);overflow:auto}.videoMode .audioDock{grid-column:3;grid-row:1;position:sticky;top:16px}.videoMode .grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.videoMode .lab{margin-top:0}
@media (max-width:1300px){.videoMode .wrap{display:block}.videoMode .maincol{position:static;max-height:none;overflow:visible}.videoMode .audioDock{position:sticky;top:auto}}
.refboard{display:none;margin:-4px 0 14px;border:1px solid var(--pink);border-radius:10px;background:rgba(17,13,32,.96);overflow:hidden}.refboard.on{display:block;position:fixed;inset:42px;z-index:90;box-shadow:0 18px 80px rgba(0,0,0,.62)}.rbody{display:grid;grid-template-columns:220px minmax(0,1fr);gap:10px;padding:10px}.refboard.swap .rbody{grid-template-columns:220px 220px minmax(0,1fr)}.rphoto{border:1px dashed var(--ln);border-radius:8px;min-height:168px;background:#0a0814;display:flex;align-items:center;justify-content:center;overflow:hidden;color:var(--mut);font-size:16px;text-align:center;padding:8px;gap:6px;flex-wrap:wrap}.rphoto img{width:100%;height:100%;object-fit:cover}.rphoto.multi img{width:calc(50% - 3px);height:76px;border-radius:5px}.rphoto.off{display:none}.rmeta{border:1px solid var(--ln);border-radius:8px;background:#0a0814;padding:10px;display:flex;flex-direction:column;gap:8px}.rmeta .rk{font-size:9px;color:var(--amb)}.rmeta .rv{font-size:18px;color:var(--ink);line-height:1.2}.racts{display:flex;gap:8px;flex-wrap:wrap}.racts button,.racts input,.racts select{background:var(--b2);border:1px solid var(--ln);color:var(--ink);border-radius:6px;height:30px;padding:0 9px;font-family:'VT323';font-size:15px}.racts input,.racts select{background:#0a0814;min-width:0}.racts input{width:138px}.racts select{width:170px}.racts button{cursor:pointer}.racts button:hover{border-color:var(--cyan);color:var(--cyan)}
.refboard.on .rbody{display:grid;grid-template-columns:minmax(0,1fr) 300px;height:calc(100% - 43px);min-height:0}.refboard.on .rphoto{position:relative;display:block;min-height:100%;padding:0;overflow:auto;text-align:left;background-image:linear-gradient(rgba(255,255,255,.035) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.035) 1px,transparent 1px);background-size:28px 28px}.refboard.on .rphoto.empty{display:flex;align-items:center;justify-content:center;text-align:center;padding:24px}.refboard.on .rphoto.drag{border-color:var(--cyan);box-shadow:0 0 24px rgba(86,225,255,.2)}.refboard.on .rmeta{margin-top:0}.bcard{position:absolute;width:202px;background:var(--b1);border:1px solid var(--ln);border-radius:8px;overflow:hidden;text-align:left;box-shadow:0 10px 26px rgba(0,0,0,.26)}.bcard.off{opacity:.48}.bcard.dragging{z-index:8;cursor:grabbing;border-color:var(--cyan);box-shadow:0 0 22px rgba(86,225,255,.25)}.bcard.drop{border-color:var(--cyan);box-shadow:0 0 22px rgba(86,225,255,.25)}.bcard img{width:100%;height:132px;object-fit:cover;display:block}.bcard .bdrag{height:26px;display:flex;align-items:center;justify-content:space-between;gap:6px;padding:0 7px;background:#0a0814;border-bottom:1px solid var(--ln);color:var(--cyan);font-size:9px;cursor:grab}.bcard .bdrag span{min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.bcard .btools{display:flex;gap:4px;flex:none}.bcard .bdel{width:22px;height:20px;border:none;border-radius:5px;background:rgba(255,255,255,.06);color:var(--ink);cursor:pointer}.bcard .bdel:hover{background:var(--amb);color:#0a0814}.bcard .bm{padding:7px;display:flex;flex-direction:column;gap:6px}.bcard .bn{font-size:15px;color:var(--ink);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.bcard label{display:flex;align-items:center;gap:4px;color:var(--amb);font-size:12px}.bcard .brow{display:flex;gap:5px;align-items:center;flex-wrap:wrap}.bcard select,.bcard input,.bcard textarea{min-width:0;background:#0a0814;border:1px solid var(--ln);color:var(--ink);border-radius:5px;font-family:'VT323';font-size:14px}.bcard select,.bcard input{height:24px}.bcard textarea{width:100%;height:44px;resize:vertical;padding:5px}.bcard select{flex:1}.bcard .short{width:50px;flex:none}.bcard .lora-name{width:100%;flex:none}.bcard .lora-tile{height:132px;display:flex;align-items:center;justify-content:center;background:linear-gradient(135deg,#211936,#0a0814);color:var(--pink);font-size:34px}.bcard input[type=checkbox]{height:auto;accent-color:var(--pink)}
.sysbar{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:-6px 0 14px}.meter{background:var(--b1);border:1px solid var(--ln);border-radius:7px;padding:6px 8px}.meter .mt{display:flex;justify-content:space-between;font-size:9px;color:var(--mut);margin-bottom:5px}.meter .mb{height:5px;background:#0a0814;border-radius:9px;overflow:hidden}.meter .mf{height:100%;background:linear-gradient(90deg,var(--cyan),var(--pink));width:0%}
.shake{animation:sh .3s}@keyframes sh{0%,100%{transform:translateX(0)}25%{transform:translateX(-5px)}75%{transform:translateX(5px)}}
.videoFoldbar{width:100%;display:flex;justify-content:space-between;align-items:center;gap:10px;margin:-2px 0 8px;padding:8px 10px;background:rgba(17,13,32,.72);border:1px solid var(--ln);border-radius:8px;color:var(--mut);font-size:10px;cursor:pointer;text-align:left}
.videoFoldbar:hover{border-color:var(--cyan);color:var(--cyan)}.videoFoldbar .vtitle{display:flex;align-items:center;gap:8px;min-width:0}.videoFoldbar .vtitle span:last-child{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.videoFoldbar .vstate{color:var(--amb);font-size:9px;white-space:nowrap}
.videoFoldbar.open .vchev{transform:rotate(90deg)}.videoFoldbar .vchev{display:inline-block;transition:transform .15s;color:var(--pink)}
.mon{display:flex;gap:14px;margin-bottom:18px}
.mon.collapsed{display:none}
.crt{flex:1.7;background:#070611;border:7px solid #2c2350;border-radius:16px;padding:10px}
.crt video{width:100%;aspect-ratio:16/10;border-radius:8px;background:#000;display:block}
.now{font-size:16px;color:var(--amb);margin-top:8px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.vlist{flex:1;display:flex;flex-direction:column;gap:6px}
.vhead{display:flex;justify-content:space-between;align-items:center;font-size:10px;color:var(--mut)}
.vtog{background:var(--b2);border:1px solid var(--ln);color:var(--ink);border-radius:5px;height:24px;cursor:pointer;font-family:'VT323';font-size:15px}.vtog:hover{color:var(--cyan);border-color:var(--cyan)}
.vrow{display:flex;align-items:center;gap:6px;background:var(--b1);border:1px solid var(--ln);border-radius:8px;padding:7px 9px;font-size:16px}
.vrow .nm{flex:1;cursor:pointer;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.vrow:hover .nm{color:var(--cyan)}.vrow .tg{font-size:9px;color:var(--pink)}
.lab{font-size:11px;color:var(--mut);letter-spacing:1px;display:flex;justify-content:space-between;align-items:center;margin:8px 0 10px}
.pager{display:none;align-items:center;justify-content:center;gap:8px;margin:14px 0 4px}.pager.on{display:flex}.pager button{background:var(--b2);border:1px solid var(--ln);color:var(--ink);border-radius:6px;min-width:34px;height:30px;font-family:'VT323';font-size:16px;cursor:pointer}.pager button:hover:not(:disabled){border-color:var(--cyan);color:var(--cyan)}.pager button:disabled{opacity:.35;cursor:default}.pager .pinfo{color:var(--amb);font-size:17px;min-width:160px;text-align:center}
.content{display:grid;grid-template-columns:minmax(0,1fr) 330px;gap:18px;align-items:start}.maincol{min-width:0}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px}
.card{position:relative;border-radius:10px;overflow:hidden;border:2px solid #2a2150;cursor:pointer;transition:transform .15s,border-color .15s;aspect-ratio:3/4}
.card:hover{transform:scale(1.06);border-color:var(--pink);z-index:2}
.card img{width:100%;height:100%;object-fit:cover;display:block;image-rendering:pixelated}
.card .cap{position:absolute;left:0;right:0;bottom:0;font-size:14px;padding:3px 7px;background:rgba(10,8,20,.78);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tools{position:absolute;top:5px;right:5px;display:none;gap:5px}.card:hover .tools{display:flex}
.tbtn{width:26px;height:26px;border:none;border-radius:6px;background:rgba(13,11,22,.85);color:#fff;cursor:pointer;font-family:'VT323';font-size:14px}.tbtn:hover{background:var(--cyan);color:#0a0814}.tbtn.del:hover{background:var(--amb);color:#0a0814}
.chip{font-size:15px;color:var(--pur);background:var(--b2);border:1px solid var(--pur);border-radius:5px;padding:2px 8px;cursor:pointer}.chip:hover{background:var(--pur);color:#0a0814}.chip.off{display:none}
.empty{color:var(--mut);font-size:18px;padding:30px;text-align:center;border:1px dashed var(--ln);border-radius:10px}
.audioDock{background:var(--b1);border:1px solid var(--ln);border-radius:10px;position:sticky;top:156px;overflow:hidden}
.plistHead{display:flex;align-items:center;justify-content:space-between;padding:8px 12px;border-bottom:1px solid var(--ln);font-size:10px;color:var(--mut)}
.plist{max-height:calc(100vh - 310px);overflow:auto;padding:6px;display:flex;flex-direction:column;gap:5px}
.arow{display:flex;align-items:center;gap:8px;border:1px solid transparent;border-radius:7px;padding:5px 7px;font-size:16px}.arow:hover{border-color:var(--cyan);color:var(--cyan)}.arow.on{background:rgba(255,93,143,.12);border-color:var(--pink);color:var(--amb)}
.arow .anm{flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;cursor:pointer}.arow .adur{color:var(--mut);font-size:14px;min-width:42px;text-align:right}.arow .adel{width:24px;height:24px;border:none;border-radius:5px;background:rgba(13,11,22,.85);color:var(--ink);cursor:pointer}.arow .adel:hover{background:var(--amb);color:#0a0814}
.player{display:flex;align-items:center;gap:10px;padding:10px 14px;flex-wrap:wrap}
.pbtn.on{border-color:var(--pink);color:var(--pink);box-shadow:0 0 12px rgba(255,93,143,.2)}
.lyrics{display:none;border-top:1px solid var(--ln);background:rgba(10,8,20,.55)}.lyrics.on{display:block}.lyhead{width:100%;display:flex;justify-content:space-between;align-items:center;background:transparent;border:none;color:var(--mut);padding:8px 12px;cursor:pointer;font-size:10px;text-align:left}.lyhead:hover{color:var(--cyan)}.lybody{display:none;max-height:240px;overflow:auto;margin:0;padding:0 12px 12px;color:var(--ink);font-family:'VT323';font-size:16px;line-height:1.22;white-space:pre-wrap}.lyrics.open .lybody{display:block}.lyrics .has{color:var(--amb)}
.logbox{margin-top:12px;background:var(--b1);border:1px solid var(--ln);border-radius:10px;overflow:hidden}.loghead{width:100%;display:flex;justify-content:space-between;align-items:center;background:transparent;border:none;color:var(--mut);padding:9px 12px;cursor:pointer;font-size:10px;text-align:left}.loghead:hover{color:var(--cyan)}.logbody{display:none;max-height:260px;overflow:auto;border-top:1px solid var(--ln);padding:8px 10px;color:var(--grn);font-size:14px;white-space:pre-wrap}.logbox.open .logbody{display:block}
.videoMode{padding:20px;background:radial-gradient(circle at 50% -10%,rgba(86,225,255,.09),transparent 34%),#080713}.videoMode .wrap{grid-template-columns:minmax(260px,340px) minmax(760px,1fr) 340px;gap:16px}.videoMode .topstick,.videoMode .maincol,.videoMode .audioDock{border:1px solid rgba(157,123,255,.36);border-radius:14px;background:linear-gradient(180deg,rgba(23,18,40,.94),rgba(10,8,20,.94));box-shadow:0 14px 36px rgba(0,0,0,.28);overflow:hidden}.videoMode .topstick{padding:14px;top:20px;border-bottom:1px solid rgba(157,123,255,.36)}.videoMode .maincol,.videoMode .audioDock{padding:12px;top:20px}.videoMode .bar{margin-bottom:10px}.videoMode .brand{font-size:13px}.videoMode .genbar{margin-bottom:10px;background:rgba(10,8,20,.8)}.videoMode .llmbar,.videoMode .optbar{margin-bottom:10px}.videoMode .director{margin:0 0 10px;border-color:rgba(86,225,255,.42);background:rgba(11,9,22,.72);border-radius:12px}.videoMode .dhead{background:rgba(86,225,255,.06)}.videoMode .dtwrap{padding:12px}.videoMode .sysbar{margin:0 0 10px}.videoMode .videoFoldbar{margin:0 0 8px;background:rgba(10,8,20,.72)}.videoMode .mon{margin-bottom:0}.videoMode .lab,.videoMode .plistHead{height:34px;margin:0 0 10px;padding:0 2px 8px;border-bottom:1px solid var(--ln);color:var(--cyan);letter-spacing:1px}.videoMode .grid{grid-template-columns:repeat(auto-fill,minmax(112px,1fr));gap:9px}.videoMode .card{border-width:1px;border-color:#32275c;border-radius:8px;transition:transform .12s,border-color .12s,box-shadow .12s}.videoMode .card:hover{transform:translateY(-2px);border-color:var(--cyan);box-shadow:0 10px 22px rgba(0,0,0,.26)}.videoMode .card .cap{font-size:13px}.videoMode .pager{margin-bottom:0}.videoMode .audioDock{background:linear-gradient(180deg,rgba(23,18,40,.94),rgba(9,7,18,.96))}.videoMode .plist{max-height:calc(100vh - 260px);padding:0;gap:6px}.videoMode .arow{background:rgba(10,8,20,.52);border-color:rgba(255,255,255,.07);padding:7px 8px}.videoMode .arow:hover{background:rgba(86,225,255,.08)}.videoMode .player{padding:10px 0 0}.videoMode .ytban,.videoMode .logbox{margin-top:10px}
.videoMode .maincol{overflow-y:auto;overflow-x:hidden;scrollbar-width:thin;scrollbar-color:rgba(157,123,255,.55) transparent}.videoMode .audioDock{overflow:hidden}.videoMode .maincol::-webkit-scrollbar,.videoMode .plist::-webkit-scrollbar,.videoMode .logbody::-webkit-scrollbar{width:8px;height:0}.videoMode .maincol::-webkit-scrollbar-track,.videoMode .plist::-webkit-scrollbar-track,.videoMode .logbody::-webkit-scrollbar-track{background:transparent}.videoMode .maincol::-webkit-scrollbar-thumb,.videoMode .plist::-webkit-scrollbar-thumb,.videoMode .logbody::-webkit-scrollbar-thumb{background:rgba(157,123,255,.45);border-radius:999px}.videoMode .maincol::-webkit-scrollbar-thumb:hover,.videoMode .plist::-webkit-scrollbar-thumb:hover,.videoMode .logbody::-webkit-scrollbar-thumb:hover{background:rgba(86,225,255,.55)}.videoMode .maincol{padding-right:10px}.videoMode .grid{align-content:start}.videoMode .card{min-width:0}.videoMode .card img{image-rendering:auto}.videoMode .topstick{min-width:0}.videoMode .audioDock{min-width:0}
.videoMode .mon{display:grid;grid-template-columns:minmax(0,1fr);gap:10px}.videoMode .mon.collapsed{display:none}.videoMode .crt{min-width:0}.videoMode .vlist{min-width:0;display:block}.videoMode .vhead{margin:0 0 6px}.videoMode #vrows{display:grid!important;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:6px!important;max-height:132px;overflow:auto;padding-right:2px}.videoMode .vrow{min-width:0}.videoMode .vrow .nm{min-width:0}.videoMode .vrow .tg{flex:none}
@media (max-width:1300px){.videoMode .wrap{display:block;max-width:1100px}.videoMode .topstick,.videoMode .maincol,.videoMode .audioDock{position:static;margin-bottom:14px;max-height:none;overflow:visible}.videoMode .grid{grid-template-columns:repeat(auto-fill,minmax(150px,1fr))}}
.ytban{display:block;margin-top:12px;border:1px solid var(--pink);border-radius:10px;background:linear-gradient(135deg,rgba(255,93,143,.18),rgba(86,225,255,.08));padding:13px 14px;text-decoration:none;color:var(--ink);box-shadow:0 0 18px rgba(255,93,143,.12)}
.ytban:hover{border-color:var(--cyan);box-shadow:0 0 20px rgba(86,225,255,.2)}.ytban .k{font-size:9px;color:var(--pink);margin-bottom:7px}.ytban .t{font-size:12px;color:var(--cyan);line-height:1.5}.ytban .s{font-size:16px;color:var(--amb);margin-top:6px}
.ytlatest{display:none;margin-top:10px;border:1px solid var(--ln);border-radius:10px;overflow:hidden;background:var(--b1);text-decoration:none;color:var(--ink)}.ytlatest.on{display:block}.ytlatest img{width:100%;display:block;aspect-ratio:16/9;object-fit:cover}.ytlatest .ytxt{padding:9px 10px}.ytlatest .yk{font-size:9px;color:var(--pink);margin-bottom:6px}.ytlatest .yn{font-size:16px;color:var(--amb);line-height:1.15}.ytlatest:hover{border-color:var(--cyan)}
.pbtn{background:var(--b2);border:1px solid var(--ln);color:var(--ink);border-radius:6px;width:34px;height:34px;cursor:pointer;font-size:16px}.pbtn:hover{color:var(--cyan);border-color:var(--cyan)}
.ptrack{flex:1;font-size:17px;color:var(--amb);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.vol{width:120px;accent-color:var(--pink)}
@media (max-width:1000px){.wrap{max-width:1100px}.sysbar{grid-template-columns:repeat(2,1fr)}.dgrid,.rbody{grid-template-columns:1fr}.content{display:block}.audioDock{margin-top:18px;position:sticky;top:auto;bottom:10px}.plist{max-height:150px}}
.lb{display:none;position:fixed;inset:0;background:rgba(6,5,14,.94);z-index:50;overflow:auto;padding:24px 84px;grid-template-rows:minmax(0,1fr) auto;place-items:center}.lb.open{display:grid}.lb.zoomed{display:block}
.lb img{max-height:calc(100vh - 220px);max-width:min(82vw,1200px);border-radius:8px;border:2px solid var(--pink);image-rendering:auto;cursor:zoom-in;object-fit:contain}.lb.zoomed img{max-height:none;max-width:none;cursor:zoom-out;display:block;margin:24px auto}
.nav{position:fixed;top:50%;transform:translateY(-50%);z-index:55;background:var(--b2);border:1px solid var(--ln);color:var(--ink);width:46px;height:70px;border-radius:8px;font-size:26px;cursor:pointer}.nav.prev{left:28px}.nav.next{right:28px}.nav:hover{color:var(--pink);border-color:var(--pink)}
.lbtools{position:fixed;top:20px;right:24px;display:flex;gap:8px}.lbtools button{background:rgba(13,11,22,.7);border:1px solid var(--ln);color:var(--ink);border-radius:6px;width:36px;height:36px;cursor:pointer;font-size:15px}.lbtools button:hover{border-color:var(--pink);color:var(--pink)}
.lbcap{width:min(900px,82vw);max-height:22vh;overflow:auto;text-align:left;font-size:18px;color:var(--amb);background:rgba(10,8,20,.86);border:1px solid var(--ln);border-radius:8px;padding:10px 12px;margin-top:14px}
.lbcap b{display:block;color:var(--cyan);font-family:'Press Start 2P',monospace;font-size:10px;margin-bottom:7px}.lbcap .k{font-family:'Press Start 2P',monospace;font-size:8px;color:var(--pink);margin:8px 0 4px}.lbcap .pr{color:var(--ink);line-height:1.25;white-space:pre-wrap}.lbcap .rq{color:var(--amb);line-height:1.25;white-space:pre-wrap}
</style></head><body><div class="wrap"><div class="topstick">
<div class="bar">
  <div class="brand pix">너무바쁜베짱이 <b>STUDIO</b><small>PING·PONG GALLERY v1.0</small></div>
  <div class="stat pix"><span id="hbox"></span><span><span class="dot" id="dot"></span> <span id="hstate">…</span></span></div>
</div>
<div class="genbar">
  <select id="mode" class="msel" onchange="modeChg()"><option value="image">이미지</option><option value="video">영상</option><option value="song">음악</option><option value="klein">인물합성</option><option value="faceswap">페이스스왑</option></select>
  <input id="prompt" class="gi" placeholder="무엇을 만들까요? 예: 노을 지는 바닷가">
  <button class="up" id="up" style="display:none" onclick="document.getElementById('files').click()">📷 사진</button>
  <input type="file" id="files" accept="image/*" multiple style="display:none" onchange="filePick()">
  <button class="genb pix" onclick="gen()">생성 ▸</button>
  <button class="folderb" id="stopbtn" onclick="stopComfy()" disabled title="큐가 돌 때 활성화돼요">■ 정지</button>
  <button class="folderb" onclick="openGallery()">📁 폴더</button>
  <button class="folderb" onclick="restartDash()">↻ 재시작</button>
</div>
<div class="modehint" id="modehint"></div>
<div class="llmbar">
  <span class="lk pix">LLM</span>
  <select id="llm" onchange="saveLlmModel()"><option value="">모델 목록 불러오는 중...</option></select>
  <button onclick="loadLlmModels()">새로고침</button>
  <span class="llmstat" id="llmstat"></span>
</div>
<div class="optbar" id="optbar">
  <span class="ok pix">OPTIONS</span>
  <div class="optgrp" id="imgopts">
    <label>RATIO <select id="iratio"><option>1:1</option><option selected>3:4</option><option>4:3</option><option>2:3</option><option>3:2</option><option>9:16</option><option>16:9</option><option>21:9</option></select></label>
    <label>UPSCALE <select id="zup"><option value="1" selected>ON</option><option value="0">OFF</option></select></label>
    <label>SCALE <select id="zscale"><option value="0.5" selected>0.5</option><option value="0.6">0.6</option><option value="0.75">0.75</option><option value="1">1.0</option></select></label>
  </div>
  <div class="optgrp" id="editopts">
    <label>SIZE <select id="emp"><option value="0.5">0.5MP</option><option value="1" selected>1MP</option><option value="1.5">1.5MP</option><option value="2">2MP</option><option value="3">3MP</option></select></label>
  </div>
  <div class="optgrp" id="vidopts">
    <label>SECONDS <input id="vsec" type="number" min="1" max="20" value="5"></label>
    <label>WIDTH <select id="vwidth"><option value="0" selected>AUTO</option><option value="512">512</option><option value="640">640</option><option value="768">768</option><option value="960">960</option><option value="1024">1024</option></select></label>
    <label>FPS <select id="vfps"><option value="16">16</option><option value="24" selected>24</option><option value="30">30</option></select></label>
  </div>
</div>
<div class="modelPanel" id="modelopts"></div>
<div class="assets" id="assets"></div>
<div class="director" id="director">
  <div class="dhead pix"><span>LTX DIRECTOR BUILDER</span><button onclick="clearDirector()">비우기</button></div>
  <div class="dtbar"><button onclick="openPicker('image')">+ 이미지</button><button onclick="addTextSegment()">+ 텍스트</button><button onclick="openPicker('video')">+ 모션</button><button onclick="openPicker('audio')">+ 오디오</button><span class="hint" id="dthint">블록을 드래그하고 양끝을 잡아 길이를 조절해요</span></div>
  <div class="dtwrap">
    <div class="dtruler" id="dtruler"></div>
    <div class="dtlane"><div class="dtlabel pix">MAIN</div><div class="dttrack" id="track-main" data-track="main"></div></div>
    <div class="dtlane"><div class="dtlabel pix">MOTION</div><div class="dttrack" id="track-motion" data-track="motion"></div></div>
    <div class="dtlane"><div class="dtlabel pix">AUDIO</div><div class="dttrack" id="track-audio" data-track="audio"></div></div>
  </div>
  <div class="dtedit" id="dtedit"><span class="pix">TEXT</span><textarea id="dttext" placeholder="이 구간에서 일어날 일을 적어주세요"></textarea><button onclick="applyTextSegment()">적용</button></div>
</div>
<div class="refboard" id="refboard">
  <div class="dhead pix"><span>TOOBUSY REFERENCE BOARD</span><span><button onclick="applyReferenceBoard()">APPLY</button> <button onclick="closeReferenceBoard()">CLOSE</button></span></div>
  <div class="rbody">
    <div class="rphoto" id="rphoto">CHARACTER A</div>
    <div class="rphoto off" id="rface">FACE A</div>
    <div class="rmeta">
      <div><div class="rk pix">REFERENCE</div><div class="rv" id="rname">인물 사진을 넣어주세요</div></div>
      <div><div class="rk pix">GOAL</div><div class="rv" id="rgoal">위 프롬프트 입력창의 장면 설명이 목표 장면으로 들어가요.</div></div>
      <div class="racts" id="racts"><button onclick="openPicker('image')">참조 이미지 선택</button><button onclick="clearReference()">이미지 제거</button></div>
    </div>
  </div>
</div>
<div class="sysbar">
  <div class="meter"><div class="mt pix"><span>GPU VRAM</span><span id="sgpu">--</span></div><div class="mb"><div class="mf" id="bgpu"></div></div></div>
  <div class="meter"><div class="mt pix"><span>GPU LOAD</span><span id="sgpuload">--</span></div><div class="mb"><div class="mf" id="bgpuload"></div></div></div>
  <div class="meter"><div class="mt pix"><span>RAM</span><span id="sram">--</span></div><div class="mb"><div class="mf" id="bram"></div></div></div>
  <div class="meter"><div class="mt pix"><span>COMFY</span><span id="scomfy">--</span></div><div class="mb"><div class="mf" id="bcomfy"></div></div></div>
</div>
<button class="videoFoldbar pix" id="vtog" onclick="toggleVideo()" type="button" aria-expanded="false" title="비디오 플레이어 열기/접기">
  <span class="vtitle"><span class="vchev">▶</span><span>VIDEO PLAYER</span></span><span class="vstate" id="vstate">접힘 · 펼치기</span>
</button>
<div class="mon">
  <div class="crt"><video id="mon" controls playsinline></video><div class="now pix" id="now">NOW PLAYING — 없음</div></div>
  <div class="vlist"><div class="vhead pix"><span>VIDEO LIST</span></div><div id="vrows" style="display:flex;flex-direction:column;gap:6px"></div></div>
</div>
</div>
<div class="content"><main class="maincol">
<div class="lab pix"><span>■ GENERATED IMAGES <span class="chip off" id="hid" onclick="unhideAll()"></span></span><span id="upinfo" style="color:var(--mut)"></span></div>
<div class="grid" id="grid"></div>
<div class="pager pix" id="pager"><button onclick="goPage(1)">≪</button><button onclick="goPage(IMGPAGE-1)">‹</button><span class="pinfo" id="pinfo"></span><button onclick="goPage(IMGPAGE+1)">›</button><button onclick="goPage(IMGPAGES)">≫</button></div>
</main><aside class="audioDock">
  <div class="plistHead pix"><span>♪ PLAYLIST</span><span id="acount">0</span></div>
  <div class="plist" id="plist"></div>
  <div class="player">
    <button class="pbtn" id="shuf" onclick="toggleShuffle()" title="셔플">🔀</button><button class="pbtn" onclick="atrk(-1)">⏮</button><button class="pbtn" id="pp" onclick="aplay()">▶</button><button class="pbtn" onclick="atrk(1)">⏭</button>
    <div class="ptrack pix" id="ptrack">BGM — 음악 없음</div>
    <input class="vol" id="vol" type="range" min="0" max="1" step="0.01" value="0.8" title="볼륨">
    <audio id="aud"></audio>
  </div>
  <div class="lyrics" id="lyrics"><button class="lyhead pix" onclick="toggleLyrics()"><span>LYRICS</span><span class="has" id="lyrstate">없음</span></button><pre class="lybody" id="lyrbody"></pre></div>
  <div class="logbox" id="logbox"><button class="loghead pix" onclick="toggleLog()"><span>▸ COMFY LOG</span><span id="logstate">접힘</span></button><div class="logbody" id="logbody"></div></div>
  <a class="ytban" href="https://youtube.com/channel/UC4xLnbcb7AxfJ8wdkiobaKQ?si=favM__m0syIADuZ2" target="_blank" rel="noopener">
    <div class="k pix">TOOBUSY STUDIO</div>
    <div class="t pix">YOUTUBE CHANNEL</div>
    <div class="s">바로가기 ▶</div>
  </a>
  <a class="ytlatest" id="ytlatest" target="_blank" rel="noopener"><img id="ytthumb"><div class="ytxt"><div class="yk pix">LATEST UPLOAD</div><div class="yn" id="yttitle"></div></div></a>
</aside></div></div>
<div class="lb" id="lb"><div class="lbtools"><button onclick="lbHide()">👁</button><button onclick="lbDel()">🗑</button><button onclick="closeLb()">✕</button></div><button class="nav prev" onclick="step(-1)">‹</button><img id="lbimg" onclick="toggleZoom()"><button class="nav next" onclick="step(1)">›</button><div class="lbcap" id="lbcap"></div></div>
<script>
var IMGS=[],VIDS=[],AUDS=[],CUSTOMS=[],LORAS=[],BOARD_PRESETS=[],AUDDUR={},MODE_STATUS={},SHUFFLE=false,MODELPANEL=false,DTDRAG=null,DTEDIT=-1,ai=0,cur=0,curAudioRel='',IMGKEY='',VIDKEY='',AUDKEY='',DURATION_FRAMES=120,IMGPAGE=1,IMGPAGES=1,IMGTOTAL=0,IMGPER=48;
function api(p,b){return fetch(p,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)})}
function el(t,c){var e=document.createElement(t);if(c)e.className=c;return e}
function esc(s){return String(s||'').replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]})}
function openGallery(){api('/api/open_gallery',{}).then(function(r){return r.json()}).then(function(j){if(!j.ok)alert(j.err||'폴더를 열 수 없어요')})}
function setStopEnabled(on){var b=document.getElementById('stopbtn');if(!b)return;b.disabled=!on;b.title=on?'현재 ComfyUI 생성을 정지합니다':'큐가 돌 때 활성화돼요'}
function stopComfy(){var b=document.getElementById('stopbtn');if(b&&b.disabled)return;if(!confirm('현재 ComfyUI 생성을 정지할까요?'))return;api('/api/interrupt',{}).then(function(r){return r.json()}).then(function(j){if(!j.ok)alert(j.err||'정지 요청 실패');else{document.getElementById('upinfo').textContent='ComfyUI 정지 요청 보냄';setStopEnabled(false)}})}
function restartDash(){if(!confirm('대시보드를 다시 시작할까요?'))return;api('/api/restart_dashboard',{}).then(function(){setTimeout(function(){location.reload()},1800)})}
function setLlmStatus(t){document.getElementById('llmstat').textContent=t||''}
function loadLlmModels(){var sel=document.getElementById('llm');setLlmStatus('LM Studio 확인 중...');
  fetch('/api/llm_models').then(function(r){return r.json()}).then(function(d){
    sel.innerHTML='';
    var list=d.models||[];
    if(!list.length){var o=document.createElement('option');o.value='';o.textContent='모델을 찾지 못했어요';sel.appendChild(o);setLlmStatus('LM Studio 확인 필요');return}
    list.forEach(function(m){var o=document.createElement('option');o.value=m.id;o.textContent=(m.label||m.id)+(m.source?' · '+m.source:'');sel.appendChild(o)});
    sel.value=d.current||list[0].id;
    setLlmStatus(d.current?('현재 '+d.current):'선택 필요')
  }).catch(function(){setLlmStatus('목록 로드 실패')})
}
function saveLlmModel(){var sel=document.getElementById('llm'),model=sel.value;if(!model)return;setLlmStatus('저장 중...');
  api('/api/llm_model',{model:model}).then(function(r){return r.json()}).then(function(j){
    if(!j.ok){setLlmStatus(j.err||'저장 실패');return}
    setLlmStatus('저장됨 · 다음 생성부터 적용')
  }).catch(function(){setLlmStatus('저장 실패')})
}
function loadLoras(){fetch('/api/loras').then(function(r){return r.json()}).then(function(d){LORAS=d.loras||[];renderAssets()}).catch(function(){LORAS=[]})}
function shortModelName(s){s=String(s||'');return s.length>42?'...'+s.slice(-39):s}
function toggleModelPanel(){MODELPANEL=!MODELPANEL;loadModelFields()}
function loadModelFields(){var m=document.getElementById('mode').value,box=document.getElementById('modelopts');if(!box)return;box.classList.remove('on');box.innerHTML='';
  fetch('/api/model_fields?mode='+encodeURIComponent(m)).then(function(r){return r.json()}).then(function(d){
    var fields=(d&&d.fields)||[];if(!fields.length){box.classList.remove('on');return}
    if(!MODELPANEL)return;
    box.classList.add('on');
    box.innerHTML='<span class="ok pix">MODELS</span>';
    fields.forEach(function(f){
      var lab=document.createElement('label'),sel=document.createElement('select');
      lab.textContent=(f.class||'Model')+' '+f.field+' ';
      (f.options||[]).forEach(function(o){var op=document.createElement('option');op.value=o;op.textContent=shortModelName(o);sel.appendChild(op)});
      if(f.current)sel.value=f.current;
      sel.title=(f.original||'')+' -> '+(f.current||'');
      sel.onchange=function(){api('/api/model_override',{mode:m,node:f.node,field:f.field,value:sel.value}).then(function(r){return r.json()}).then(function(j){if(!j.ok)alert(j.err||'model save failed');else{MODE_STATUS={};loadModes()}})};
      lab.appendChild(sel);box.appendChild(lab)
    })
  }).catch(function(){box.classList.remove('on');box.innerHTML=''})
}
function loadBoardPresets(){fetch('/api/reference_presets').then(function(r){return r.json()}).then(function(d){BOARD_PRESETS=d.presets||[];fillPresetSelect()}).catch(function(){BOARD_PRESETS=[];fillPresetSelect()})}
function fillPresetSelect(){var s=document.getElementById('presetSel');if(!s)return;var cur=s.value;s.innerHTML='<option value="">Load Preset</option>'+BOARD_PRESETS.map(function(p){return '<option value="'+esc(p.id)+'"'+(p.id===cur?' selected':'')+'>'+esc(p.name||p.id)+' · '+(p.count||0)+'</option>'}).join('')}
function boardPresetAssets(){return picked.filter(function(a){return a&&(a.kind==='image'||a.kind==='audio'||a.kind==='lora'||a.type==='lora')}).map(function(a){return JSON.parse(JSON.stringify(a))})}
function saveBoardPreset(){var inp=document.getElementById('presetName'),name=(inp&&inp.value.trim())||('board_'+new Date().toISOString().slice(5,16).replace(/[-T:]/g,'')),assets=boardPresetAssets();if(!assets.length){alert('저장할 카드가 없어요');return}api('/api/reference_preset_save',{name:name,assets:assets}).then(function(r){return r.json()}).then(function(j){if(!j.ok){alert(j.err||'프리셋 저장 실패');return}if(inp)inp.value='';loadBoardPresets();document.getElementById('upinfo').textContent='Reference preset 저장됨 ✓'})}
function loadBoardPreset(){var s=document.getElementById('presetSel'),id=s&&s.value;if(!id)return;fetch('/api/reference_preset?id='+encodeURIComponent(id)).then(function(r){return r.json()}).then(function(j){if(!j.ok||!j.preset){alert(j.err||'프리셋 로드 실패');return}picked=(j.preset.assets||[]).map(function(a){if(a.kind==='lora'||a.type==='lora'){a.kind='lora';a.type='lora'}else{a.kind='image'}return a});renderAssets();document.getElementById('upinfo').textContent='Reference preset 불러옴 ✓'})}
function optVal(id){var e=document.getElementById(id);return e?e.value:''}
function optNum(id,def){var v=parseFloat(optVal(id));return isNaN(v)?def:v}
function loadGenOptions(){
  ['iratio','zup','zscale','emp','vsec','vwidth','vfps'].forEach(function(id){var v=localStorage.getItem('pingpong_'+id),e=document.getElementById(id);if(e&&v!==null)e.value=v});
  updateDurationFrames()
}
function saveGenOptions(){
  ['iratio','zup','zscale','emp','vsec','vwidth','vfps'].forEach(function(id){var e=document.getElementById(id);if(e)localStorage.setItem('pingpong_'+id,e.value)})
}
function updateDurationFrames(){
  var sec=Math.max(1,Math.min(20,parseInt(optVal('vsec')||'5',10)||5));
  var fps=Math.max(8,Math.min(30,parseInt(optVal('vfps')||'24',10)||24));
  DURATION_FRAMES=sec*fps;
  picked.forEach(function(a){if(a&&a.start!==undefined){a.start=clampFrame(a.start,0,DURATION_FRAMES-1);a.length=clampFrame(a.length||DURATION_FRAMES,1,DURATION_FRAMES-a.start)}});
  renderDirectorTimeline()
}
function gatherSettings(){
  var m=document.getElementById('mode').value,custom=CUSTOMS.find(function(x){return x.mode===m});
  var s={};
  if(m==='image'){s.image_ratio=optVal('iratio');s.zit_upscale=optVal('zup')==='1';s.zit_scale_by=optNum('zscale',0.5)}
  else if(m==='video'||(custom&&custom.type==='video')){s.video_seconds=optNum('vsec',5);s.video_width=optNum('vwidth',0);s.video_fps=optNum('vfps',24)}
  else if(custom&&custom.type==='image'){s.image_megapixels=optNum('emp',1);if(custom.ratio)s.image_ratio=optVal('iratio')}
  return s
}
['iratio','zup','zscale','emp','vsec','vwidth','vfps'].forEach(function(id){setTimeout(function(){var e=document.getElementById(id);if(e)e.onchange=function(){saveGenOptions();updateDurationFrames();renderAssets()}},0)});
function setVideoFold(fold){var m=document.querySelector('.mon'),b=document.getElementById('vtog'),s=document.getElementById('vstate');m.classList.toggle('collapsed',fold);b.classList.toggle('open',!fold);b.setAttribute('aria-expanded',fold?'false':'true');b.title=fold?'비디오 플레이어 펼치기':'비디오 플레이어 접기';if(s)s.textContent=fold?'접힘 · 펼치기':'열림 · 접기';localStorage.setItem('pingpong_video_fold',fold?'1':'0')}
function toggleVideo(){setVideoFold(!document.querySelector('.mon').classList.contains('collapsed'))}
function initVideoFold(){setVideoFold(localStorage.getItem('pingpong_video_fold')!=='0')}
function meter(bar,label,pct,text){document.getElementById(bar).style.width=Math.max(0,Math.min(100,pct||0))+'%';document.getElementById(label).textContent=text}
function pollSystem(){fetch('/api/system').then(function(r){return r.json()}).then(function(s){
  meter('bgpu','sgpu',s.gpu.pct,s.gpu.used?((s.gpu.used/1024).toFixed(1)+'/'+(s.gpu.total/1024).toFixed(1)+'G'):'--');
  meter('bgpuload','sgpuload',s.gpu.util,s.gpu.used?(s.gpu.util+'%'):'--');
  meter('bram','sram',s.ram.pct,s.ram.used?(s.ram.used+'/'+s.ram.total+'G'):'--');
  var cq=(s.comfy.pending||0),lq=(s.comfy.local_queue||0),run=(s.comfy.running||0);
  var pr=s.comfy.progress||{};
  setStopEnabled(!!(run||cq||lq||(pr&&pr.active)));
  if(pr.active){meter('bcomfy','scomfy',pr.pct||0,(pr.pct||0)+'% '+(pr.status||''))}
  else{meter('bcomfy','scomfy',(run||cq||lq)?35:(s.comfy.ok?18:0),s.comfy.ok?('RUN '+run+' / C '+cq+' / Q '+lq):'OFF')}
})}
function toggleLog(){var box=document.getElementById('logbox');box.classList.toggle('open');document.getElementById('logstate').textContent=box.classList.contains('open')?'열림':'접힘';if(box.classList.contains('open'))pollLog()}
function pollLog(){if(!document.getElementById('logbox').classList.contains('open'))return;fetch('/api/comfy_log').then(function(r){return r.json()}).then(function(d){document.getElementById('logbody').textContent=(d.lines||[]).join('\n')})}
function loadYoutube(){fetch('/api/youtube_latest').then(function(r){return r.json()}).then(function(y){
  if(!y.ok||!y.thumb)return;
  var a=document.getElementById('ytlatest');a.href=y.url;document.getElementById('ytthumb').src=y.thumb;document.getElementById('yttitle').textContent=y.title;a.classList.add('on')
})}

function listKey(xs){return (xs||[]).map(function(x){return x.rel+':'+Math.floor(x.mtime||0)}).join('|')}
function load(){fetch('/api/list?page='+IMGPAGE+'&per='+IMGPER).then(function(r){return r.json()}).then(function(d){
  var ik=listKey(d.images),vk=listKey(d.videos),ak=listKey(d.audios);
  IMGPAGE=d.imagePage||1;IMGPAGES=d.imagePages||1;IMGTOTAL=d.imageTotal||0;
  if(ik!==IMGKEY){IMGKEY=ik;IMGS=d.images;renderImgs()}
  if(vk!==VIDKEY){VIDKEY=vk;VIDS=d.videos;renderVids()}
  if(ak!==AUDKEY){AUDKEY=ak;AUDS=d.audios;renderAuds()}
  renderPager();
  var h=document.getElementById('hid');h.textContent='↺ 숨김 복구 '+d.hiddenCount;h.className='chip'+(d.hiddenCount?'':' off');
})}
function goPage(p){p=Math.max(1,Math.min(IMGPAGES,p||1));if(p===IMGPAGE)return;IMGPAGE=p;IMGKEY='';load();window.scrollTo({top:document.getElementById('grid').offsetTop-180,behavior:'smooth'})}
function renderPager(){var p=document.getElementById('pager'),info=document.getElementById('pinfo');if(!p)return;p.classList.toggle('on',IMGPAGES>1);info.textContent=IMGPAGE+' / '+IMGPAGES+'  ·  '+IMGTOTAL+' imgs';var b=p.querySelectorAll('button');b[0].disabled=b[1].disabled=IMGPAGE<=1;b[2].disabled=b[3].disabled=IMGPAGE>=IMGPAGES}
function loadModes(){fetch('/api/modes').then(function(r){return r.json()}).then(function(d){
  CUSTOMS=d.custom||[];MODE_STATUS=d.status||{};var sel=document.getElementById('mode');
  CUSTOMS.forEach(function(c){if(sel.querySelector('option[value="'+c.mode+'"]'))return;
    var o=document.createElement('option');o.value=c.mode;o.textContent=c.label;sel.appendChild(o)
  });
  modeChg();
})}
function renderModeHint(m){
  var box=document.getElementById('modehint'),st=MODE_STATUS[m]||{};
  if(!box)return;
  var missing=st.missing||[],needs=st.needs||[];
  box.className='modehint on '+(missing.length?'bad':'ok');
  if(!st.label&&!missing.length&&!needs.length){box.className='modehint';box.innerHTML='';return}
  var modelWarn=missing.some(function(x){var s=String(x);return s.indexOf('모델')>=0||s.toLowerCase().indexOf('model')>=0});
  var btn='<button class="modelToggle'+(modelWarn?' warn':'')+'" onclick="toggleModelPanel()">모델 설정</button>';
  if(missing.length){
    box.innerHTML='<b>READY CHECK</b><span class="miss">필요함: '+esc(missing.join(' · '))+'</span>'+btn;
  }else{
    var tail=needs.length?' · 확인됨: '+esc(needs.slice(0,4).join(' · '))+(needs.length>4?' · ...':''):'';
    box.innerHTML='<b>READY CHECK</b>준비됨'+tail+btn;
  }
}
function renderImgs(){var g=document.getElementById('grid');g.innerHTML='';
  if(!IMGS.length){g.innerHTML='<div class="empty">아직 생성된 이미지가 없어요. 위에서 생성해보세요!</div>';return}
  IMGS.forEach(function(it,i){var c=el('div','card');
    c.innerHTML='<img loading="lazy" src="'+it.url+'"><div class="cap">'+it.name+'</div><div class="tools"><button class="tbtn">👁</button><button class="tbtn del">🗑</button></div>';
    c.draggable=true;
    c.ondragstart=function(e){e.dataTransfer.setData('application/x-pingpong-gallery',JSON.stringify({kind:'image',name:it.name,url:it.url,rel:it.rel}));e.dataTransfer.effectAllowed='copy'};
    c.onclick=function(){openLb(i)};
    var b=c.querySelectorAll('.tbtn');
    b[0].onclick=function(e){e.stopPropagation();api('/api/hide',{rel:it.rel}).then(load)};
    b[1].onclick=function(e){e.stopPropagation();if(confirm('삭제할까요? (.trash 폴더로 이동)'))api('/api/delete',{rel:it.rel}).then(load)};
    g.appendChild(c)})}
function renderVids(){var v=document.getElementById('vrows');v.innerHTML='';
  VIDS.forEach(function(it){var r=el('div','vrow pix');
    r.innerHTML='<span class="tg">MP4</span><span class="nm">'+it.name+'</span><button class="tbtn">🗑</button>';
    r.querySelector('.nm').onclick=function(){var m=document.getElementById('mon');m.src=it.url;m.play();document.getElementById('now').textContent='NOW PLAYING — '+it.name};
    r.querySelector('.tbtn').onclick=function(){if(confirm('삭제할까요?'))api('/api/delete',{rel:it.rel}).then(load)};
    v.appendChild(r)});
  if(VIDS.length){var m=document.getElementById('mon');if(!m.src){m.src=VIDS[0].url;document.getElementById('now').textContent='NOW PLAYING — '+VIDS[0].name}}}
function renderAuds(){var a=document.getElementById('aud');
  renderPlaylist();
  if(!AUDS.length){curAudioRel='';a.removeAttribute('src');document.getElementById('ptrack').textContent='BGM — 음악 없음';document.getElementById('pp').textContent='▶';updateLyrics(null);return}
  var keep=curAudioRel&&AUDS.some(function(x){return x.rel===curAudioRel});
  if(!keep)setTrack(Math.min(ai,AUDS.length-1),false);
}

function renderPlaylist(){var p=document.getElementById('plist'),c=document.getElementById('acount');p.innerHTML='';c.textContent=AUDS.length;
  if(!AUDS.length){p.innerHTML='<div class="empty">아직 음악이 없어요.</div>';return}
  AUDS.forEach(function(it,i){var r=el('div','arow'+(it.rel===curAudioRel?' on':''));r.innerHTML='<span class="anm">'+esc(it.name)+'</span><span class="adur">'+fmtDur(AUDDUR[it.rel])+'</span><button class="adel">×</button>';
    r.draggable=true;
    r.ondragstart=function(e){e.dataTransfer.setData('application/x-pingpong-gallery',JSON.stringify({kind:'audio',name:it.name,url:it.url,rel:it.rel,duration:AUDDUR[it.rel]||0}));e.dataTransfer.effectAllowed='copy'};
    r.querySelector('.anm').onclick=function(){setTrack(i,true)};
    r.querySelector('.adel').onclick=function(e){e.stopPropagation();if(confirm('삭제할까요?'))api('/api/delete',{rel:it.rel}).then(load)};
    p.appendChild(r);loadAudDur(it)
  })
}
function fmtDur(v){if(!v||!isFinite(v))return '--:--';v=Math.round(v);return Math.floor(v/60)+':'+String(v%60).padStart(2,'0')}
function loadAudDur(it){if(AUDDUR[it.rel])return;var a=new Audio();a.preload='metadata';a.src=it.url;a.onloadedmetadata=function(){AUDDUR[it.rel]=a.duration;renderPlaylist()}}
function updateShuffleButton(){var b=document.getElementById('shuf');if(b)b.classList.toggle('on',SHUFFLE)}
function toggleShuffle(){SHUFFLE=!SHUFFLE;localStorage.setItem('pingpong_shuffle',SHUFFLE?'1':'0');updateShuffleButton()}
function nextAudioIndex(d){if(!AUDS.length)return 0;if(SHUFFLE&&d>0&&AUDS.length>1){var n=ai;while(n===ai)n=Math.floor(Math.random()*AUDS.length);return n}return (ai+d+AUDS.length)%AUDS.length}
function toggleLyrics(){var box=document.getElementById('lyrics');if(box&&box.classList.contains('on'))box.classList.toggle('open')}
function updateLyrics(it){var box=document.getElementById('lyrics'),state=document.getElementById('lyrstate'),body=document.getElementById('lyrbody');if(!box||!state||!body)return;var txt=(it&&it.lyrics||'').trim();box.classList.toggle('on',!!txt);if(txt){state.textContent='있음';body.textContent=txt}else{state.textContent='없음';body.textContent=''}}
function setTrack(i,autoplay){if(!AUDS.length)return;ai=(i+AUDS.length)%AUDS.length;var a=document.getElementById('aud');curAudioRel=AUDS[ai].rel;
  if(!a.src||!a.src.endsWith(AUDS[ai].url)){a.src=AUDS[ai].url}
  document.getElementById('ptrack').textContent='BGM ♪ '+AUDS[ai].name;
  updateLyrics(AUDS[ai]);
  renderPlaylist();
  if(autoplay){a.play().then(function(){document.getElementById('pp').textContent='⏸'}).catch(function(){document.getElementById('pp').textContent='▶'})}
}
function aplay(){var a=document.getElementById('aud');if(!AUDS.length)return;if(!a.src)setTrack(0,false);if(a.paused){a.play().then(function(){document.getElementById('pp').textContent='⏸'}).catch(function(){document.getElementById('pp').textContent='▶'})}else{a.pause();document.getElementById('pp').textContent='▶'}}
function atrk(d){setTrack(nextAudioIndex(d),true)}
document.getElementById('aud').addEventListener('ended',function(){atrk(1)});
document.getElementById('aud').addEventListener('pause',function(){document.getElementById('pp').textContent='▶'});
document.getElementById('aud').addEventListener('play',function(){document.getElementById('pp').textContent='⏸'});
var vol=document.getElementById('vol'),savedVol=localStorage.getItem('pingpong_volume'),aud=document.getElementById('aud');
if(savedVol!==null)vol.value=savedVol;aud.volume=parseFloat(vol.value);
vol.oninput=function(){aud.volume=parseFloat(vol.value);localStorage.setItem('pingpong_volume',vol.value)};
SHUFFLE=localStorage.getItem('pingpong_shuffle')==='1';updateShuffleButton();
function typingTarget(e){var t=e.target,tag=(t&&t.tagName||'').toLowerCase();return tag==='input'||tag==='textarea'||tag==='select'||(t&&t.isContentEditable)}

function openLb(i){cur=i;showLb();document.getElementById('lb').classList.add('open');document.body.classList.add('modalopen')}
function showLb(){if(!IMGS.length){closeLb();return}document.getElementById('lb').classList.remove('zoomed');cur=(cur+IMGS.length)%IMGS.length;var it=IMGS[cur];document.getElementById('lbimg').src=it.url;
  var html='<b>'+esc(it.name)+'</b>';
  html+='<div class="k">REQUEST</div><div class="rq">'+esc(it.request||'요청 원문 기록 없음 - 새 생성부터 저장돼요')+'</div>';
  html+='<div class="k">GENERATED PROMPT</div><div class="pr">'+esc(it.prompt||'프롬프트 메타데이터 없음')+'</div>';
  document.getElementById('lbcap').innerHTML=html
}
function toggleZoom(){document.getElementById('lb').classList.toggle('zoomed')}
function step(d){cur+=d;showLb()}
function closeLb(){document.getElementById('lb').classList.remove('open','zoomed');document.body.classList.remove('modalopen')}
function lbHide(){var it=IMGS[cur];if(it)api('/api/hide',{rel:it.rel}).then(function(){IMGS.splice(cur,1);renderImgs();IMGS.length?showLb():closeLb();updHidQuick()})}
function lbDel(){var it=IMGS[cur];if(it&&confirm('삭제할까요?'))api('/api/delete',{rel:it.rel}).then(function(){IMGS.splice(cur,1);renderImgs();IMGS.length?showLb():closeLb()})}
function updHidQuick(){load()}
function unhideAll(){api('/api/unhide_all',{}).then(load)}
document.addEventListener('keydown',function(e){
  if(e.code==='Space'&&!typingTarget(e)){e.preventDefault();aplay();return}
  if(e.key==='Escape'&&document.getElementById('refboard').classList.contains('on')){closeReferenceBoard();return}
  if(!document.getElementById('lb').classList.contains('open'))return;
  if(e.key==='ArrowRight')step(1);if(e.key==='ArrowLeft')step(-1);if(e.key==='Escape')closeLb()
});
function openReferenceBoard(){document.getElementById('refboard').classList.add('on');document.body.classList.add('modalopen');renderReferenceBoard()}
function closeReferenceBoard(){document.getElementById('refboard').classList.remove('on');document.body.classList.remove('modalopen')}
function applyReferenceBoard(){closeReferenceBoard();renderAssets();document.getElementById('upinfo').textContent=picked.length?'보드 카드 '+picked.length+'개 적용됨':'보드 비어 있음'}
function needsReferenceBoard(m,c){return m==='klein'||m==='faceswap'||!!(c&&c.image_inputs)}

function modeChg(){var m=document.getElementById('mode').value,p=document.getElementById('prompt'),u=document.getElementById('up'),d=document.getElementById('director'),rb=document.getElementById('refboard'),ob=document.getElementById('optbar'),io=document.getElementById('imgopts'),eo=document.getElementById('editopts'),vo=document.getElementById('vidopts');
  document.body.classList.toggle('videoMode',m==='video');
  MODELPANEL=false;
  var ph={image:'무엇을 그릴까요? 예: 노을 지는 바닷가',video:'어떤 영상? 대사는 "따옴표"',song:'어떤 음악? 예: 신나는 EDM',klein:'바꿀 장면 설명 (+ 사진 1장)',faceswap:'장면(선택) + 사진 2장(몸→얼굴)'};
  var c=CUSTOMS.find(function(x){return x.mode===m});
  p.placeholder=c?('무엇을 만들까요? '+c.trigger+' 프롬프트'):ph[m];
  d.classList.toggle('on',m==='video');
  rb.classList.remove('on');
  rb.classList.remove('swap');
  document.body.classList.remove('modalopen');
  ob.classList.toggle('on',m==='image'||m==='video'||(c&&(c.type==='image'||c.type==='video')));
  io.classList.toggle('on',m==='image'||!!(c&&c.type==='image'&&c.ratio));
  ['zup','zscale'].forEach(function(id){var e=document.getElementById(id),l=e&&e.closest('label');if(l)l.style.display=(m==='image')?'flex':'none'});
  eo.classList.toggle('on',!!(c&&c.type==='image'));
  vo.classList.toggle('on',m==='video'||!!(c&&c.type==='video'));
  renderModeHint(m);
  loadModelFields();
  pickRole='';
  var useBoard=needsReferenceBoard(m,c);
  u.style.display=useBoard?'block':'none';u.textContent='📌 보드';u.onclick=openReferenceBoard;
  document.getElementById('files').accept=m==='video'?'image/*,video/*,audio/*':'image/*,audio/*';
  renderAssets();document.getElementById('upinfo').textContent=''}
var picked=[];
function kindOf(f){return f.type.indexOf('image/')===0?'image':(f.type.indexOf('video/')===0?'video':(f.type.indexOf('audio/')===0?'audio':'file'))}
function nextDirectorStart(kind){var tr=(kind==='video')?'motion':(kind==='audio'?'audio':'main'),end=0;picked.forEach(function(a){if(directorTrackFor(a)===tr)end=Math.max(end,(a.start||0)+(a.length||1))});return clampFrame(end,0,DURATION_FRAMES-1)}
var pickRole='';
function addPickedFiles(files, role){files=Array.prototype.slice.call(files||[]);if(!files.length){renderAssets();return}
  var m=document.getElementById('mode').value,c=CUSTOMS.find(function(x){return x.mode===m}),done=0,append=(m==='video'||needsReferenceBoard(m,c));if(append)picked=picked.slice();else picked=[];
  var cursors={main:nextDirectorStart('image'),motion:nextDirectorStart('video'),audio:nextDirectorStart('audio')};
  files.forEach(function(f){var k=kindOf(f);if((role==='image'||role==='swap-body'||role==='swap-face')&&k!=='image'){done++;if(done===files.length)renderAssets();return}if(role&&role!=='image'&&role!=='swap-body'&&role!=='swap-face'&&k!==role){done++;if(done===files.length)renderAssets();return}var tr=(k==='video')?'motion':(k==='audio'?'audio':'main'),st=(m==='video')?cursors[tr]:0,ln=(m==='video'&&k==='image'?1:DURATION_FRAMES);if(m==='video')cursors[tr]=clampFrame(st+ln,0,DURATION_FRAMES-1);var fr=new FileReader();fr.onload=function(){var item={data:fr.result,name:f.name,type:f.type,kind:k,start:st,length:ln,trimStart:0,isEndFrame:false};if(m==='faceswap'&&role==='swap-face'){item.role='face_a';picked[1]=item}else if(m==='faceswap'&&role==='swap-body'){item.role='character_a';picked[0]=item}else{if(m==='klein'&&k==='image'&&!item.role){var roles=['character_a','face_a','background_a','pose_a','style_a','prop_a'];item.role=roles[Math.min(picked.filter(function(x){return x&&x.kind==='image'}).length,roles.length-1)]}picked.push(item)}done++;if(done===files.length)renderAssets()};fr.readAsDataURL(f)})}
function openPicker(role){pickRole=role||'';var f=document.getElementById('files');
  f.accept=role==='image'?'image/*':(role==='video'?'video/*':(role==='audio'?'audio/*':'image/*,audio/*'));
  f.value='';
  f.click()
}
function clearDirector(){picked=picked.filter(function(x){return x&&x.kind!=='image'&&x.kind!=='video'&&x.kind!=='audio'});document.getElementById('files').value='';renderAssets()}
function clearReference(){picked=[];document.getElementById('files').value='';renderAssets()}
function mediaThumb(a){if(a.kind==='image')return '<img class="thumb" src="'+a.data+'">';return '<div class="thumb">'+(a.kind==='video'?'MP4':'♪')+'</div>'}
function imageAt(n){return picked.filter(function(x){return x&&x.kind==='image'})[n]}
function clampFrame(v,min,max){v=parseInt(v,10);if(isNaN(v))v=min;return Math.max(min,Math.min(max,v))}
function fpsVal(){return Math.max(1,parseInt(optVal('vfps')||'24',10)||24)}
function frameSec(f){return (Math.round((f/fpsVal())*10)/10).toFixed(1).replace('.0','')+'s'}
function updateAssetTime(idx,key,val){var a=picked[idx];if(!a)return;a[key]=clampFrame(val,key==='length'?1:0,DURATION_FRAMES);if(a.start+a.length>DURATION_FRAMES)a.length=Math.max(1,DURATION_FRAMES-a.start);renderDirectorTimeline()}
function directorTrackFor(a){if(!a)return null;if(a.kind==='video')return'motion';if(a.kind==='audio')return'audio';if(a.kind==='image'||a.kind==='text')return'main';return null}
function frameFromDrop(track,e){var r=track.getBoundingClientRect();return clampFrame(Math.round((e.clientX-r.left)/Math.max(1,r.width)*DURATION_FRAMES),0,DURATION_FRAMES-1)}
function directorDropAsset(track,e){e.preventDefault();track.classList.remove('drop');var raw=e.dataTransfer.getData('application/x-pingpong-gallery');if(!raw)return;var it;try{it=JSON.parse(raw)}catch(_){return}var tr=track.dataset.track,frame=frameFromDrop(track,e);if(tr==='main'&&it.kind==='image'){picked.push({kind:'image',name:it.name||'gallery image',data:it.url,gallery_rel:it.rel,start:frame,length:1,trimStart:0,isEndFrame:false});renderAssets();return}if(tr==='audio'&&it.kind==='audio'){var fps=fpsVal(),dur=Math.max(fps,Math.round((it.duration||4)*fps));picked.push({kind:'audio',name:it.name||'audio',data:it.url,gallery_rel:it.rel,start:frame,length:Math.min(dur,DURATION_FRAMES-frame),trimStart:0});renderAssets();return}}
function directorDragOver(track,e){var types=Array.prototype.slice.call(e.dataTransfer.types||[]);if(types.indexOf('application/x-pingpong-gallery')<0)return;e.preventDefault();track.classList.add('drop');e.dataTransfer.dropEffect='copy'}
function saveTextDraft(){var a=picked[DTEDIT],t=document.getElementById('dttext');if(!a||!t||a.kind!=='text')return;a.prompt=t.value.trim();a.name=(a.prompt||'Text Segment').slice(0,28)}
function addTextSegment(){saveTextDraft();var fps=fpsVal(),start=nextDirectorStart('text'),len=Math.min(DURATION_FRAMES-start,Math.max(fps,Math.round(DURATION_FRAMES/3))),item={kind:'text',name:'Text Segment',prompt:'',start:start,length:Math.max(1,len),trimStart:0,id:'text_'+Date.now()};picked.push(item);renderAssets();editTextSegment(picked.length-1)}
function applyTextSegment(){saveTextDraft();document.getElementById('dtedit').classList.remove('on');DTEDIT=-1;renderDirectorTimeline()}
function editTextSegment(i){saveTextDraft();DTEDIT=i;var a=picked[i],box=document.getElementById('dtedit'),t=document.getElementById('dttext');if(!a||!box||!t)return;t.value=a.prompt||'';box.classList.add('on');t.focus()}
function renderDirectorTimeline(){var tracks={main:document.getElementById('track-main'),motion:document.getElementById('track-motion'),audio:document.getElementById('track-audio')},r=document.getElementById('dtruler');if(!tracks.main)return;Object.keys(tracks).forEach(function(k){tracks[k].innerHTML=''});if(r){var secs=Math.max(1,Math.round(DURATION_FRAMES/fpsVal())),html='';for(var i=0;i<=secs;i++){html+='<span style="left:'+(i/secs*100)+'%">'+i+'s</span>'}r.innerHTML=html}
  picked.forEach(function(a,i){var tr=directorTrackFor(a);if(!tr||!tracks[tr])return;if(a.start===undefined)a.start=0;if(a.length===undefined)a.length=(a.kind==='image'?1:DURATION_FRAMES);a.start=clampFrame(a.start,0,DURATION_FRAMES-1);a.length=clampFrame(a.length,1,DURATION_FRAMES-a.start);
    var b=el('div','dtblock '+a.kind);b.style.left=(a.start/DURATION_FRAMES*100)+'%';b.style.width=(a.length/DURATION_FRAMES*100)+'%';b.dataset.i=i;
    var label=a.kind==='text'?(a.prompt||a.name||'Text'):(a.name||a.kind);b.title=label+' · '+frameSec(a.start)+' - '+frameSec(a.start+a.length);
    var thumb=(a.kind==='image'&&a.data)?'<img class="dtthumb" src="'+esc(a.data)+'">':'';
    b.innerHTML=thumb+'<span class="dth l" data-edge="l"></span><span class="dtname">'+esc(label)+'</span><span class="dttime">'+frameSec(a.start)+'-'+frameSec(a.start+a.length)+'</span><button class="dtx" onclick="event.stopPropagation();picked.splice('+i+',1);renderAssets()">×</button><span class="dth r" data-edge="r"></span>';
    b.onpointerdown=function(e){if(e.target.closest('button'))return;directorDragStart(i,e)};if(a.kind==='text')b.ondblclick=function(e){e.stopPropagation();editTextSegment(i)};tracks[tr].appendChild(b)
  });
  var hint=document.getElementById('dthint');if(hint)hint.textContent=picked.filter(function(a){return directorTrackFor(a)}).length?'블록 이동/양끝 조절 · 더블클릭으로 텍스트 수정':'자료를 추가하면 타임라인 블록이 생겨요'
}
function directorDragStart(i,e){var a=picked[i],block=e.currentTarget,track=block.parentElement,rect=track.getBoundingClientRect(),edge=e.target.dataset.edge||'move';e.preventDefault();DTDRAG={i:i,edge:edge,rect:rect,sx:e.clientX,start:a.start||0,length:a.length||1,block:block};block.classList.add('dragging');block.setPointerCapture&&block.setPointerCapture(e.pointerId)}
document.addEventListener('pointermove',function(e){if(!DTDRAG)return;var dx=e.clientX-DTDRAG.sx,df=Math.round(dx/Math.max(1,DTDRAG.rect.width)*DURATION_FRAMES),a=picked[DTDRAG.i];if(!a)return;if(DTDRAG.edge==='l'){var ns=clampFrame(DTDRAG.start+df,0,DTDRAG.start+DTDRAG.length-1);a.start=ns;a.length=DTDRAG.start+DTDRAG.length-ns}else if(DTDRAG.edge==='r'){a.length=clampFrame(DTDRAG.length+df,1,DURATION_FRAMES-a.start)}else{a.start=clampFrame(DTDRAG.start+df,0,DURATION_FRAMES-DTDRAG.length)}renderDirectorTimeline();DTDRAG.block=document.querySelector('.dtblock[data-i="'+DTDRAG.i+'"]');if(DTDRAG.block)DTDRAG.block.classList.add('dragging')});
document.addEventListener('pointerup',function(){if(DTDRAG&&DTDRAG.block)DTDRAG.block.classList.remove('dragging');DTDRAG=null});
var BOARD_ROLES=[['character_a','Character A'],['character_b','Character B'],['character_c','Character C'],['character_d','Character D'],['face_a','Face A'],['face_b','Face B'],['outfit_a','Outfit A'],['outfit_b','Outfit B'],['pose_a','Pose A'],['background_a','Background A'],['style_a','Style A'],['prop_a','Prop A'],['main_character','Main A'],['secondary_character','Character B'],['pose','Pose'],['outfit','Outfit'],['background','Background'],['style','Style'],['product','Product'],['ignore','Extra / Ignore']];
function typeForRoleDash(role,fallback){if(['character_a','character_b','character_c','character_d','main_character','secondary_character'].indexOf(role)>=0)return'character';if(['face_a','face_b'].indexOf(role)>=0)return'face';if(['outfit_a','outfit_b','outfit'].indexOf(role)>=0)return'outfit';if(['pose_a','pose'].indexOf(role)>=0)return'pose';if(['background_a','background'].indexOf(role)>=0)return'background';if(['style_a','style'].indexOf(role)>=0)return'style';if(['prop_a','product'].indexOf(role)>=0)return'prop';return fallback||'image'}
function roleOptions(selected){var h='';BOARD_ROLES.forEach(function(r){h+='<option value="'+r[0]+'"'+(selected===r[0]?' selected':'')+'>'+r[1]+'</option>'});return h}
function ensureBoardPos(a,order){if(a.board_x===undefined)a.board_x=18+(order%5)*222;if(a.board_y===undefined)a.board_y=18+Math.floor(order/5)*306;if(!a.role&&a.kind==='image'){var roles=['character_a','face_a','background_a','pose_a','style_a','prop_a'];a.role=roles[Math.min(order,roles.length-1)]}}
function boardSet(i,k,v,rerender){if(!picked[i])return;picked[i][k]=v;if(rerender)renderAssets();else renderReferenceBoard()}
function boardDelete(i){picked.splice(i,1);renderAssets()}
var boardDrag=null;
function boardDragStart(i,e){if(!picked[i]||(e.target&&e.target.closest('button,select,input,textarea')))return;e.preventDefault();var c=e.currentTarget.closest('.bcard'),r=document.getElementById('rphoto').getBoundingClientRect();boardDrag={i:i,card:c,rect:r,sx:e.clientX,sy:e.clientY,ox:Number(picked[i].board_x)||0,oy:Number(picked[i].board_y)||0};c.classList.add('dragging');c.setPointerCapture&&c.setPointerCapture(e.pointerId)}
document.addEventListener('pointermove',function(e){if(!boardDrag)return;var a=picked[boardDrag.i];if(!a)return;var nx=Math.max(0,boardDrag.ox+e.clientX-boardDrag.sx),ny=Math.max(0,boardDrag.oy+e.clientY-boardDrag.sy);a.board_x=nx;a.board_y=ny;boardDrag.card.style.left=nx+'px';boardDrag.card.style.top=ny+'px'});
document.addEventListener('pointerup',function(){if(boardDrag&&boardDrag.card)boardDrag.card.classList.remove('dragging');boardDrag=null});
function replaceBoardImage(i,file){if(!picked[i]||!file||file.type.indexOf('image/')!==0)return;var fr=new FileReader();fr.onload=function(){picked[i].data=fr.result;picked[i].name=file.name;picked[i].type=file.type;picked[i].kind='image';renderReferenceBoard()};fr.readAsDataURL(file)}
function openReplacePicker(i,e){if(e){e.stopPropagation();e.preventDefault()}var inp=document.createElement('input');inp.type='file';inp.accept='image/*';inp.style.display='none';inp.onchange=function(){replaceBoardImage(i,inp.files&&inp.files[0]);inp.remove()};document.body.appendChild(inp);inp.click()}
function boardCardDragOver(e){e.preventDefault();e.stopPropagation();var c=e.currentTarget;if(c)c.classList.add('drop')}
function boardCardDrop(i,e){e.preventDefault();e.stopPropagation();var c=e.currentTarget;if(c)c.classList.remove('drop');var f=Array.prototype.find.call(e.dataTransfer.files||[],function(x){return x.type.indexOf('image/')===0});if(f)replaceBoardImage(i,f)}
function faceMode(a){return a.face_keep_enabled?'keep':(a.face_erase_enabled?'erase':'')}
function setBoardFaceMode(i,mode){if(!picked[i])return;picked[i].face_erase_enabled=mode==='erase';picked[i].face_keep_enabled=mode==='keep';renderAssets()}
function boardImageCard(a,i,order){ensureBoardPos(a,order);if(a.enabled===undefined)a.enabled=true;var t=typeForRoleDash(a.role,'image'),fm=faceMode(a),faceLike=(t==='character'||t==='face');var h='<div class="bcard'+(a.enabled===false?' off':'')+'" style="left:'+a.board_x+'px;top:'+a.board_y+'px" ondragover="boardCardDragOver(event)" ondragleave="this.classList.remove(&quot;drop&quot;)" ondrop="boardCardDrop('+i+',event)"><div class="bdrag" onpointerdown="boardDragStart('+i+',event)"><span>'+esc(a.role||'reference')+'</span><div class="btools"><button class="bdel" title="이미지 교체" onclick="openReplacePicker('+i+',event)">↺</button><button class="bdel" title="삭제" onclick="event.stopPropagation();event.preventDefault();boardDelete('+i+')">×</button></div></div><img src="'+a.data+'" title="'+esc(a.name||'')+'"><div class="bm"><div class="bn">'+esc(a.name||'image')+'</div><div class="brow"><label><input type="checkbox" '+(a.enabled!==false?'checked':'')+' onchange="boardSet('+i+',&quot;enabled&quot;,this.checked,false)">USE</label><select onchange="picked['+i+'].role=this.value;picked['+i+'].type=typeForRoleDash(this.value,&quot;image&quot;);renderAssets()">'+roleOptions(a.role||'ignore')+'</select></div>';
  h+='<div class="brow"><label><input type="checkbox" '+(a.bg_remove_enabled?'checked':'')+' onchange="boardSet('+i+',&quot;bg_remove_enabled&quot;,this.checked,true)">Remove BG</label>';
  if(a.bg_remove_enabled){h+='<select onchange="boardSet('+i+',&quot;bg_remove_model&quot;,this.value,false)"><option value="u2net"'+((a.bg_remove_model||'u2net')==='u2net'?' selected':'')+'>u2net</option><option value="u2netp"'+(a.bg_remove_model==='u2netp'?' selected':'')+'>u2netp</option><option value="isnet-general-use"'+(a.bg_remove_model==='isnet-general-use'?' selected':'')+'>isnet</option><option value="isnet-anime"'+(a.bg_remove_model==='isnet-anime'?' selected':'')+'>anime</option><option value="birefnet-general"'+(a.bg_remove_model==='birefnet-general'?' selected':'')+'>biref</option></select><select onchange="boardSet('+i+',&quot;bg_remove_background&quot;,this.value,false)"><option value="white"'+((a.bg_remove_background||'white')==='white'?' selected':'')+'>white</option><option value="black"'+(a.bg_remove_background==='black'?' selected':'')+'>black</option><option value="green"'+(a.bg_remove_background==='green'?' selected':'')+'>green</option><option value="gray"'+(a.bg_remove_background==='gray'?' selected':'')+'>gray</option><option value="magenta"'+(a.bg_remove_background==='magenta'?' selected':'')+'>magenta</option></select>'}
  h+='</div>';
  if(faceLike){h+='<div class="brow"><select onchange="setBoardFaceMode('+i+',this.value)"><option value="">Face Module</option><option value="erase"'+(fm==='erase'?' selected':'')+'>Erase Face</option><option value="keep"'+(fm==='keep'?' selected':'')+'>Keep Face Only</option></select>';
    if(fm){h+='<select onchange="boardSet('+i+',&quot;face_erase_fill&quot;,this.value,false)"><option value="gray"'+((a.face_erase_fill||'gray')==='gray'?' selected':'')+'>gray</option><option value="black"'+(a.face_erase_fill==='black'?' selected':'')+'>black</option><option value="white"'+(a.face_erase_fill==='white'?' selected':'')+'>white</option></select><input class="short" title="expand" type="number" value="'+(a.face_erase_expand||8)+'" onchange="boardSet('+i+',&quot;face_erase_expand&quot;,parseInt(this.value||8),false)"><input class="short" title="feather" type="number" value="'+(a.face_erase_feather||6)+'" onchange="boardSet('+i+',&quot;face_erase_feather&quot;,parseInt(this.value||6),false)">'}
    h+='</div>'}
  if(t==='face'){h+='<div class="brow"><label><input type="checkbox" '+(a.face_lora_enabled?'checked':'')+' onchange="boardSet('+i+',&quot;face_lora_enabled&quot;,this.checked,false)">Face LoRA</label>'+loraSelect('',a.face_lora_name||'','boardSet('+i+',&quot;face_lora_name&quot;,this.value,false)')+'<input class="short" type="number" step="0.05" value="'+(a.face_lora_strength||1)+'" onchange="boardSet('+i+',&quot;face_lora_strength&quot;,parseFloat(this.value||1),false)"></div>'}
  h+='<textarea placeholder="note..." onchange="boardSet('+i+',&quot;note&quot;,this.value,false)">'+esc(a.note||'')+'</textarea></div></div>';return h}
function boardLoraCard(a,i,order){ensureBoardPos(a,order);if(a.enabled===undefined)a.enabled=true;if(a.lora_enabled===undefined)a.lora_enabled=a.enabled;var h='<div class="bcard'+(a.lora_enabled===false?' off':'')+'" style="left:'+a.board_x+'px;top:'+a.board_y+'px"><div class="bdrag" onpointerdown="boardDragStart('+i+',event)"><span>LoRA</span><button class="bdel" onclick="event.stopPropagation();boardDelete('+i+')">×</button></div><div class="lora-tile">LoRA</div><div class="bm"><div class="bn">'+esc(a.lora_name||a.name||'LoRA')+'</div><div class="brow"><label><input type="checkbox" '+(a.lora_enabled!==false?'checked':'')+' onchange="picked['+i+'].lora_enabled=this.checked;picked['+i+'].enabled=this.checked;renderReferenceBoard()">Enabled</label></div>'+loraSelect('',a.lora_name||a.name||'','picked['+i+'].lora_name=this.value;picked['+i+'].name=this.value;renderReferenceBoard()')+'<div class="brow"><span>Strength</span><input class="short" type="number" step="0.05" value="'+(a.lora_strength||1)+'" onchange="picked['+i+'].lora_strength=parseFloat(this.value||1);renderReferenceBoard()"></div></div></div>';return h}
function boardAudioCard(a,i,order){ensureBoardPos(a,order);if(a.enabled===undefined)a.enabled=true;return '<div class="bcard'+(a.enabled===false?' off':'')+'" style="left:'+a.board_x+'px;top:'+a.board_y+'px"><div class="bdrag" onpointerdown="boardDragStart('+i+',event)"><span>AUDIO</span><div class="btools"><button class="bdel" title="삭제" onclick="event.stopPropagation();event.preventDefault();boardDelete('+i+')">×</button></div></div><div class="lora-tile">♪</div><div class="bm"><div class="bn">'+esc(a.name||'audio')+'</div><div class="brow"><label><input type="checkbox" '+(a.enabled!==false?'checked':'')+' onchange="boardSet('+i+',&quot;enabled&quot;,this.checked,false)">USE</label></div><textarea placeholder="note..." onchange="boardSet('+i+',&quot;note&quot;,this.value,false)">'+esc(a.note||'')+'</textarea></div></div>'}
function renderReferenceBoard(){var photo=document.getElementById('rphoto'),face=document.getElementById('rface'),name=document.getElementById('rname'),goal=document.getElementById('rgoal'),acts=document.getElementById('racts'),m=document.getElementById('mode').value,c=CUSTOMS.find(function(x){return x.mode===m}),img=imageAt(0),faceImg=imageAt(1),text=document.getElementById('prompt').value.trim();
  if(!photo)return;
  if(needsReferenceBoard(m,c)){
    var imgs=picked.filter(function(x){return x&&x.kind==='image'}),loras=picked.filter(function(x){return x&&(x.kind==='lora'||x.type==='lora')}),audios=picked.filter(function(x){return x&&x.kind==='audio'});
    face.classList.add('off');photo.classList.toggle('multi',false);photo.classList.toggle('empty',!(imgs.length||loras.length||audios.length));
    if(imgs.length||loras.length||audios.length){var order=0;photo.innerHTML=picked.map(function(a,i){if(!a)return'';if(a.kind==='image')return boardImageCard(a,i,order++);if(a.kind==='audio')return boardAudioCard(a,i,order++);if(a.kind==='lora'||a.type==='lora')return boardLoraCard(a,i,order++);return''}).join('')}else{photo.textContent='이미지나 오디오를 이 보드에 드래그하거나 Add files를 눌러주세요'}
    name.textContent=imgs.length+' images · '+loras.length+' loras · '+audios.length+' audio';
    goal.textContent=text||'카드를 움직이고 각 카드의 역할/모듈을 고르면 Reference Board 번들로 들어가요.';
    acts.innerHTML='<button onclick="openPicker(\'\')">Add files</button><button onclick="addLoraCard()">Add LoRA</button><input id="presetName" placeholder="Preset name"><button onclick="saveBoardPreset()">Save Preset</button><select id="presetSel"></select><button onclick="loadBoardPreset()">Load</button><button onclick="clearReference()">Clear cards</button>';fillPresetSelect();
    return
  }
  if(m==='faceswap'){
    face.classList.remove('off');
    photo.innerHTML=img?'<img src="'+img.data+'">':'BODY / SCENE';
    face.innerHTML=faceImg?'<img src="'+faceImg.data+'">':'FACE IDENTITY';
    name.textContent=(img?img.name:'몸/장면 사진 필요')+'  →  '+(faceImg?faceImg.name:'얼굴 사진 필요');
    goal.textContent=text||'프롬프트는 선택 사항이에요. 비우면 원본 장면을 최대한 유지해요.';
    acts.innerHTML="<button onclick=\"openPicker('swap-body')\">몸/장면 선택</button><button onclick=\"openPicker('swap-face')\">얼굴 선택</button><button onclick=\"clearReference()\">비우기</button>";
  }else{
    face.classList.add('off');
    if(img){photo.innerHTML='<img src="'+img.data+'">';name.textContent=img.name}else{photo.textContent='CHARACTER A';name.textContent='인물 사진을 넣어주세요'}
    goal.textContent=text||'위 프롬프트 입력창의 장면 설명이 목표 장면으로 들어가요.';
    acts.innerHTML="<button onclick=\"openPicker('image')\">참조 이미지 선택</button><button onclick=\"clearReference()\">이미지 제거</button>";
  }
}
function renderDirectorLane(kind,label){var lane=document.getElementById('lane-'+kind),items=picked.filter(function(x){return x&&x.kind===kind});lane.innerHTML='';
  if(!items.length){lane.innerHTML='<div class="dhint">'+label+'를 넣으면 Director 노드에 같이 전달돼요.</div>';return}
  items.forEach(function(a){var idx=picked.indexOf(a),row=el('div','ditem');a.start=clampFrame(a.start||0,0,DURATION_FRAMES-1);a.length=clampFrame(a.length||DURATION_FRAMES,1,DURATION_FRAMES-a.start);a.trimStart=clampFrame(a.trimStart||0,0,9999);
    row.innerHTML='<div class="dmain">'+mediaThumb(a)+'<span>'+esc(a.name)+'</span><button>×</button></div><div class="dtime"><label>START<input type="number" min="0" max="'+(DURATION_FRAMES-1)+'" value="'+a.start+'" data-k="start"></label><label>LEN<input type="number" min="1" max="'+DURATION_FRAMES+'" value="'+a.length+'" data-k="length"></label><label>TRIM<input type="number" min="0" value="'+a.trimStart+'" data-k="trimStart"></label></div>';
    row.querySelector('button').onclick=function(){picked.splice(idx,1);renderAssets()};
    row.querySelectorAll('input').forEach(function(inp){inp.onchange=function(){updateAssetTime(idx,inp.getAttribute('data-k'),inp.value)}});
    lane.appendChild(row)
  })
}
function roleSelect(a,i){var roles=['character_a','character_b','character_c','character_d','face_a','face_b','outfit_a','outfit_b','background_a','pose_a','style_a','prop_a'];var h='<select onchange="picked['+i+'].role=this.value;renderAssets()">';roles.forEach(function(r){h+='<option value="'+r+'"'+((a.role||'')===r?' selected':'')+'>'+r+'</option>'});return h+'</select>'}
function setPickedBool(i,k,v){picked[i][k]=v;renderReferenceBoard()}
function setPickedVal(i,k,v){picked[i][k]=v;renderReferenceBoard()}
function setFaceTool(i,mode){picked[i].face_erase_enabled=mode==='erase';picked[i].face_keep_enabled=mode==='keep';renderAssets()}
function loraOptions(selected){var vals=LORAS.slice();if(selected&&vals.indexOf(selected)<0)vals.unshift(selected);var h='<option value="">LoRA 선택</option>';vals.forEach(function(n){h+='<option value="'+esc(n)+'"'+(n===selected?' selected':'')+'>'+esc(n)+'</option>'});return h}
function loraSelect(id,selected,onchange){return '<select class="lora-name" id="'+id+'" onchange="'+onchange+'">'+loraOptions(selected)+'</select>'}
function boardTools(a,i){var role=a.role||'',face=role.indexOf('face_')===0||role.indexOf('character_')===0,mode=a.face_keep_enabled?'keep':(a.face_erase_enabled?'erase':'');var h='<label class="use"><input type="checkbox" '+(a.bg_remove_enabled?'checked':'')+' onchange="setPickedBool('+i+',&quot;bg_remove_enabled&quot;,this.checked)">BG</label>';
  if(face){h+='<select title="face mask" onchange="setFaceTool('+i+',this.value)"><option value="">FACE</option><option value="erase"'+(mode==='erase'?' selected':'')+'>ERASE</option><option value="keep"'+(mode==='keep'?' selected':'')+'>KEEP</option></select>'+loraSelect('',a.face_lora_name||'','setPickedVal('+i+',&quot;face_lora_name&quot;,this.value);setPickedBool('+i+',&quot;face_lora_enabled&quot;,!!this.value)')+'<input class="short" title="face LoRA strength" type="number" step="0.05" value="'+(a.face_lora_strength||1)+'" onchange="setPickedVal('+i+',&quot;face_lora_strength&quot;,parseFloat(this.value||1))">'}
  return h
}
function addLoraCard(){var sel=document.getElementById('loraAddSel'),n=sel?sel.value:'';picked.push({kind:'lora',type:'lora',role:'lora_a',name:n||'LoRA',lora_name:n||'',lora_strength:1,lora_enabled:true,enabled:true});renderAssets()}
function renderAssets(){var box=document.getElementById('assets'),m=document.getElementById('mode').value,c=CUSTOMS.find(function(x){return x.mode===m});box.innerHTML='';box.className='assets'+(picked.length&&m!=='video'?' on':'');
  var boardMode=needsReferenceBoard(m,c);
  picked.forEach(function(a,i){if(!a)return;if(a.enabled===undefined)a.enabled=true;var row=el('div','asset'+(a.enabled===false?' off':''));var board=(a.kind==='image'&&boardMode),lora=((a.kind==='lora'||a.type==='lora')&&boardMode);var role=board?roleSelect(a,i):'';var use=(board||lora)?'<label class="use"><input type="checkbox" '+(a.enabled!==false?'checked':'')+' onchange="picked['+i+'].enabled=this.checked;picked['+i+'].lora_enabled=this.checked;renderAssets()">USE</label>':'';var tools=board?boardTools(a,i):'';if(lora){tools=loraSelect('',a.lora_name||a.name||'','picked['+i+'].lora_name=this.value;picked['+i+'].name=this.value;renderAssets()')+'<input class="short" type="number" step="0.05" value="'+(a.lora_strength||1)+'" onchange="picked['+i+'].lora_strength=parseFloat(this.value||1);renderReferenceBoard()">'}row.innerHTML='<b>'+esc((lora?'LORA':a.kind).toUpperCase())+'</b>'+use+role+tools+'<span>'+esc(a.name||a.lora_name||'')+'</span><button>×</button>';row.querySelector('button').onclick=function(){picked.splice(i,1);renderAssets()};box.appendChild(row)});
  if(boardMode){var add=el('div','asset');add.innerHTML='<b>LORA</b>'+loraSelect('loraAddSel','','')+'<button onclick="addLoraCard()">+ ADD</button>';box.appendChild(add);box.classList.add('on')}
  renderDirectorTimeline();
  renderReferenceBoard();
  document.getElementById('upinfo').textContent=picked.length?(m==='video'?'Director 자료 '+picked.length+'개':'사진 '+picked.length+'장 첨부됨'):''
}
function filePick(){var fs=document.getElementById('files').files,done=0;
  addPickedFiles(fs,pickRole);
  pickRole='';
}
function gen(){var m=document.getElementById('mode').value,p=document.getElementById('prompt'),t=p.value.trim();
  if((m==='image'||m==='video'||m==='song'||m.indexOf('custom:')===0)&&!t){p.classList.add('shake');setTimeout(function(){p.classList.remove('shake')},300);return}
  var imageData=picked.filter(function(x){return x&&x.kind==='image'}).map(function(x){return x.data});
  if(m==='klein'&&imageData.length<1){alert('사진 1장을 첨부하세요');return}
  if(m==='faceswap'&&imageData.length<2){alert('사진 2장(몸→얼굴)을 첨부하세요');return}
  var custom=CUSTOMS.find(function(x){return x.mode===m});
  if(custom&&custom.image_inputs&&imageData.length<custom.image_inputs){alert('이미지 '+custom.image_inputs+'장을 첨부하세요');return}
  saveGenOptions();
  api('/api/generate',{mode:m,text:t,images:imageData,assets:picked,settings:gatherSettings()}).then(function(r){return r.json()}).then(function(j){
    if(!j.ok){alert(j.err||'실패');return}
    p.value='';if(!(needsReferenceBoard(m,custom)||m==='video'))picked=[];document.getElementById('files').value='';renderAssets();document.getElementById('upinfo').textContent=(needsReferenceBoard(m,custom)||m==='video')?'큐에 추가됨 ✓ 작업 보드는 유지돼요':'큐에 추가됨 ✓ 봇이 곧 처리해요';
  })}
document.getElementById('prompt').addEventListener('keydown',function(e){if(e.key==='Enter')gen()});
document.getElementById('prompt').addEventListener('input',renderReferenceBoard);
document.getElementById('dttext').addEventListener('input',saveTextDraft);
var boardDrop=document.getElementById('rphoto');
if(boardDrop){
  boardDrop.addEventListener('dragover',function(e){var m=document.getElementById('mode').value,c=CUSTOMS.find(function(x){return x.mode===m});if(!needsReferenceBoard(m,c))return;e.preventDefault();boardDrop.classList.add('drag')});
  boardDrop.addEventListener('dragleave',function(){boardDrop.classList.remove('drag')});
  boardDrop.addEventListener('drop',function(e){var m=document.getElementById('mode').value,c=CUSTOMS.find(function(x){return x.mode===m});if(!needsReferenceBoard(m,c))return;e.preventDefault();boardDrop.classList.remove('drag');addPickedFiles(Array.prototype.filter.call(e.dataTransfer.files||[],function(f){return f.type.indexOf('image/')===0||f.type.indexOf('audio/')===0}), '')});
}
['track-main','track-audio'].forEach(function(id){var t=document.getElementById(id);if(!t)return;t.addEventListener('dragover',function(e){directorDragOver(t,e)});t.addEventListener('dragleave',function(){t.classList.remove('drop')});t.addEventListener('drop',function(e){directorDropAsset(t,e)})});

function poll(){fetch('/api/status').then(function(r){return r.json()}).then(function(s){
  var hb=document.getElementById('hbox'),dot=document.getElementById('dot'),st=document.getElementById('hstate');
  hb.firstChild&&hb.firstChild.classList&&hb.firstChild.classList.toggle('on',s.alive);
  dot.className='dot'+(s.alive?' a':'');
  st.textContent=!s.alive?'OFFLINE':(s.generating||s.queued?'GENERATING'+(s.queued?(' ('+s.queued+')'):''):'ONLINE')
})}
var rows=["0110110","1111111","1111111","0111110","0011100","0001000"],hs='';
for(var y=0;y<6;y++)for(var x=0;x<7;x++)if(rows[y][x]==='1')hs+='<rect x='+(x*6.5)+' y='+(y*6.5)+' width=6.5 height=6.5 fill="#ff5d8f"/>';
document.getElementById('hbox').innerHTML='<svg class="heart on" viewBox="0 0 46 40">'+hs+'</svg>';
var faceOpt=document.querySelector('#mode option[value="faceswap"]');if(faceOpt)faceOpt.remove();
var kleinOpt=document.querySelector('#mode option[value="klein"]');if(kleinOpt)kleinOpt.textContent='Flux2 Klein';
loadGenOptions();initVideoFold();loadModes();loadLlmModels();loadLoras();loadBoardPresets();load();loadYoutube();poll();pollSystem();setInterval(load,5000);setInterval(poll,2000);setInterval(pollSystem,3000);setInterval(pollLog,3000);setInterval(loadYoutube,1800000);
</script></body></html>'''


def main():
    os.makedirs(QUEUE, exist_ok=True)
    srv = ReusableThreadingHTTPServer(("127.0.0.1", PORT), H)
    url = "http://127.0.0.1:%d" % PORT
    print("=" * 52)
    print("  너무바쁜베짱이 핑퐁 갤러리 대시보드")
    print("  " + url)
    print("  생성 폴더:", GALLERY)
    print("=" * 52, flush=True)
    try:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    except Exception:
        pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
