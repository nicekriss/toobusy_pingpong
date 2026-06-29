# -*- coding: utf-8 -*-
"""
VRAM 핑퐁 오케스트레이터 (멀티모드)
텔레그램 -> (필요시)로컬 LLM이 프롬프트 작성 -> LLM 언로드 -> ComfyUI 생성 -> 텔레그램 전송

모드:
  (그냥 텍스트)        -> ZIT 이미지   (qwen이 영문 프롬프트 작성)
  /영상 또는 /video    -> LTX 영상     (qwen이 영상 프롬프트 작성, 대사는 따옴표)
  /음악 /노래 /song    -> ACE 음악     (qwen이 태그+가사 작성)
  사진 첨부(+캡션)     -> Flux2 Klein  인물합성 (그래프 내 gemma 디렉터 사용, 외부 LLM 불필요)

핵심: 24GB 단일 GPU에서 LLM과 ComfyUI가 번갈아 VRAM 점유.
 LLM 올리기 전 ComfyUI /free 필수(동시 상주 시 OOM). LLM은 '생각하는 순간'만 VRAM.
"""
import os, sys, json, copy, time, random, subprocess, traceback, re, threading, unicodedata, base64, mimetypes, uuid, urllib.parse, webbrowser
import requests
try:
    import websocket
except Exception:
    websocket = None

try:  # 윈도우 cp949 콘솔에서 이모지/한글 출력 시 크래시 방지
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(HERE, "config.json")

def auto_comfy_dirs():
    """Detect the user-selected shared input/output folders when available."""
    settings_path = os.path.join(os.environ.get("APPDATA", ""), "Comfy Desktop", "settings.json")
    try:
        data = json.load(open(settings_path, encoding="utf-8"))
        out_dir = data.get("outputDir")
        in_dir = data.get("inputDir")
        if out_dir and in_dir:
            return (
                os.path.expandvars(os.path.expanduser(str(out_dir))),
                os.path.expandvars(os.path.expanduser(str(in_dir))),
            )
    except Exception:
        pass
    base = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Comfy-Desktop", "ComfyUI-Shared")
    return os.path.join(base, "output"), os.path.join(base, "input")


if not os.path.exists(CFG_PATH):
    print("=" * 52)
    print("  ⚠️  config.json 이 없습니다.")
    print("  먼저 '설치.bat' 을 실행해 초기 설정을 해주세요.")
    print("  (텔레그램 토큰 입력 → 자동 설정)")
    print("=" * 52, flush=True)
    sys.exit(1)

CFG = json.load(open(CFG_PATH, encoding="utf-8"))
_auto_out, _auto_in = auto_comfy_dirs()

TG       = f"https://api.telegram.org/bot{CFG['telegram_token']}"
TOKEN    = CFG["telegram_token"]
CHAT     = CFG["telegram_chat_id"]
COMFY    = CFG.get("comfy_api", "http://127.0.0.1:8188").rstrip("/")
LMAPI    = CFG.get("lmstudio_api", "http://127.0.0.1:1234").rstrip("/")
MODEL    = CFG["llm_model"]
OUTDIR   = CFG.get("comfy_output_dir") or _auto_out
INPUTDIR = CFG.get("comfy_input_dir") or _auto_in
DASHBOARD_PORT = int(CFG.get("dashboard_port", 8910))
DASHBOARD_HOST = CFG.get("dashboard_host", "127.0.0.1")
DASHBOARD_OPEN_HOST = "127.0.0.1" if DASHBOARD_HOST in ("0.0.0.0", "::", "") else DASHBOARD_HOST

# VRAM 급에 맞게 갈아끼울 수 있는 모델 파일명 (config "models"로 덮어쓰기, 없으면 기본값)
DEFAULT_MODELS = {
    "zit":      "ZIT\\zImageTurbo_turbo.safetensors",
    "ltx_gguf": "LTX23\\ltx23DEVGGUFUnsloth_q4km.gguf",
    "klein":    "FLUX2\\flux-2-klein-9b-kv-fp8.safetensors",
    "ace":      "aceStepAudioGen_v15XLTurbo.safetensors",
}
MODELS = CFG.get("models", {}) or {}
CUSTOM = CFG.get("custom_workflows", {}) or {}
def model_of(key):
    return MODELS.get(key) or DEFAULT_MODELS[key]

def reload_runtime_config():
    global CFG, CUSTOM, MODELS
    try:
        latest = json.load(open(CFG_PATH, encoding="utf-8"))
        CFG = latest
        CUSTOM = latest.get("custom_workflows", {}) or {}
        MODELS = latest.get("models", {}) or {}
    except Exception as e:
        log("config reload failed:", e)
    return CFG

def model_override_key(mode, node, field):
    return "|".join([str(mode or ""), str(node or ""), str(field or "")])

def apply_model_overrides(wf, mode, settings=None):
    cfg = reload_runtime_config()
    overrides = dict(cfg.get("model_overrides", {}) or {})
    overrides.update((settings or {}).get("model_overrides", {}) or {})
    for node_id, node in wf.items():
        inputs = node.get("inputs") if isinstance(node, dict) else None
        if not isinstance(inputs, dict):
            continue
        for field in list(inputs.keys()):
            value = overrides.get(model_override_key(mode, node_id, field))
            if value:
                inputs[field] = value
    return wf

def reload_custom_workflows():
    global CFG, CUSTOM, MODELS
    try:
        latest = json.load(open(CFG_PATH, encoding="utf-8"))
        CFG = latest
        CUSTOM = latest.get("custom_workflows", {}) or {}
        MODELS = latest.get("models", {}) or {}
    except Exception as e:
        log("custom workflow reload failed:", e)
    return CUSTOM

def _custom_key(s):
    return unicodedata.normalize("NFC", str(s or "").strip()).lower()

def resolve_custom_workflow(name):
    wanted = _custom_key(name)
    for workflows in (CUSTOM, reload_custom_workflows()):
        if not workflows:
            continue
        for key, spec in workflows.items():
            aliases = [key, spec.get("trigger", ""), spec.get("trigger", "").lstrip("/")]
            if wanted in {_custom_key(a) for a in aliases}:
                return key, spec
    available = ", ".join((s.get("trigger") or k) for k, s in CUSTOM.items()) or "none"
    raise KeyError(f"{name} (available: {available})")

def current_llm_model():
    global MODEL
    try:
        latest = json.load(open(CFG_PATH, encoding="utf-8"))
        MODEL = latest.get("llm_model") or MODEL
    except Exception as e:
        log("config reload failed:", e)
    return MODEL

ALIVE_FILE = os.path.join(OUTDIR, "pingpong", ".alive")
def beat_alive():
    try:
        os.makedirs(os.path.dirname(ALIVE_FILE), exist_ok=True)
        with open(ALIVE_FILE, "w") as f:
            f.write(str(time.time()))
    except Exception:
        pass

def start_heartbeat():
    def loop():
        while True:
            beat_alive()
            time.sleep(15)
    threading.Thread(target=loop, daemon=True).start()

# 공유 작업 큐 (대시보드가 job json을 떨궈두면 봇이 순서대로 처리 → GPU 충돌 방지)
QUEUE_DIR = os.path.join(HERE, "queue")
PROGRESS_PATH = os.path.join(HERE, "dashboard_comfy_progress.json")
def run_job(job):
    m = job.get("mode")
    txt = (job.get("text") or "").strip()
    settings = job.get("settings") or {}
    if isinstance(m, str) and m.startswith("custom:"):
        do_custom_text(m, txt, job.get("image_refs") or [], settings)
    elif m == "image_fanout":
        fanout_image_jobs(txt, int(job.get("count") or requested_image_count(txt)), settings)
    elif m == "image_direct":
        do_image_direct(txt, job.get("request") or txt, settings)
    elif m == "compare":
        do_compare(job)
    elif m in ("image", "video", "song"):
        do_text(m, txt or "a creative, beautiful scene", job.get("director_assets") or [], settings)
    elif m == "klein_board":
        do_klein_board(job.get("reference_assets") or [], txt, settings)
    elif m == "klein":
        _klein_core(job["char_rel"], txt)
    elif m == "faceswap_board":
        do_klein_board(job.get("reference_assets") or [], txt, settings, flags=["face_swap"])
    elif m == "faceswap":
        do_faceswap(job["char_rel"], job["face_rel"], txt)

def process_queue():
    try:
        files = sorted(f for f in os.listdir(QUEUE_DIR) if f.endswith(".json"))
    except FileNotFoundError:
        return
    for fn in files:
        fp = os.path.join(QUEUE_DIR, fn)
        try:
            job = json.load(open(fp, encoding="utf-8"))
        except Exception:
            try: os.remove(fp)
            except Exception: pass
            continue
        try: os.remove(fp)   # 먼저 제거(중복 처리 방지)
        except Exception: pass
        try:
            log("queue job:", job.get("mode"))
            run_job(job)
        except Exception as e:
            tg_send(f"⚠️ 대시보드 작업 오류: {e}")
            log("job error:", traceback.format_exc())

IMAGE_SYS = (
    "You convert the user's request into ONE production-ready image prompt. "
    "Output only the final prompt sentence, with no analysis, no self-talk, no labels, no word counts, no markdown. "
    "Preserve the requested subject, medium, mood, and language intent. Do not invent unrelated subjects. "
    "If the user asks for photo, keep it photographic; if illustration/comic/anime, keep that medium. "
    "Use concrete visual details: subject, setting, composition, lighting, texture, camera/style. Keep under 85 words."
)
VIDEO_SYS = (
    "You convert the user's request into ONE production-ready video prompt. "
    "Output only the final prompt sentence, with no analysis, no self-talk, no labels, no word counts, no markdown. "
    "Describe subject, scene, action, temporal motion, camera motion, lighting, and atmosphere. "
    "If the user requests spoken dialogue, keep the spoken line in the user's original language inside quotes. Keep under 80 words."
)
REFSHEET_VIDEO_SYS = (
    "You are writing a prompt for an LTX 2.3 image-to-video workflow that receives a reference sheet image. "
    "Study the reference sheet image and describe the character, outfit, props, palette, style, and relevant environment first. "
    "Then combine it with the user's video request into one production-ready video prompt in English. "
    "The user only writes the desired video content; do not ask them to separately describe the sheet. "
    "If the user's request includes Korean spoken dialogue, keep that dialogue exactly in Korean inside quotes. "
    "Never translate spoken Korean dialogue into English. Other non-dialogue prompt text may be English. "
    "Output only the final prompt, no analysis, no markdown fences, no alternatives."
)
SONG_SYS = (
    "You are a professional songwriter and music producer. Given a theme (any language), respond "
    "in EXACTLY this format and nothing else:\n"
    "TAGS: <comma-separated English genre/mood/instrument/vocal tags>\n"
    "LYRICS:\n<full song lyrics with [Verse]/[Chorus]/[Bridge] section tags>"
)
MULTI_IMAGE_SYS = (
    "Return ONLY a JSON array of final image prompt strings. "
    "No markdown, no commentary, no labels, no reasoning. "
    "Each string must stay faithful to the user's request but vary composition, setting, lighting, camera, and details. "
    "Respect the requested medium instead of forcing photorealism. Keep each under 85 words."
)
COMPARE_PROMPT_SYS = (
    "You expand each given keyword/idea into ONE vivid, richly detailed English image generation prompt. "
    "Return ONLY a JSON array of objects, the SAME length and order as the input list (i-th object expands i-th keyword). "
    'Each object = {"prompt": <full English image prompt>, "intent": <SHORT Korean note, 3~10 단어>}. '
    "The 'intent' says what this image is meant to test and what to focus on when comparing two models "
    "(예: '털 질감과 수염 디테일', '복잡한 손 해부학', '금속 반사와 조명 일관성', '작은 글자 가독성'). "
    "Each prompt stays faithful to its keyword while adding concrete detail: setting, composition, lighting, mood, texture, palette, camera/style. "
    "Respect the implied medium. No commentary, no markdown. Keep each prompt under 85 words."
)
RANDOM_PROMPT_SYS = (
    "You are an art director building a diverse test set of image prompts to compare image-generation models. "
    "Return ONLY a JSON array of objects. "
    'Each object = {"prompt": <full English image prompt>, "intent": <SHORT Korean note, 3~10 단어>}. '
    "The 'intent' says what this test image stresses and what to focus on when comparing two models "
    "(예: '털 질감과 수염 디테일', '복잡한 군중 묘사', '역광 실루엣', '반투명 유리 표현'). "
    "Make the prompts diverse across composition, framing, lighting, mood, color palette, and art style/medium "
    "(photo, oil painting, anime, 3D render, watercolor, pixel art). "
    "Each prompt concrete and vivid with setting, lighting, color, texture, camera/lens or art-style cue. "
    "No commentary, no markdown. Keep each prompt under 85 words."
)

TG_BANNER = ("🦗▸ 너무바쁜베짱이 STUDIO ◂🦗\n"
             "🕹️ P I N G · P O N G  B O T 🕹️\n"
             "░▒▓ made by 코다 & 크룩스 ▓▒░")

HELP = (
    "👾 아래 버튼/번호로 골라요 👾\n"
    "▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n"
    "1️⃣ 이미지   2️⃣ 영상   3️⃣ 음악\n"
    "4️⃣ 인물합성(사진1장)\n"
    "5️⃣ 페이스스왑(사진2장)\n"
    "▰▰▰▰▰▰▰▰▰▰▰▰▰▰\n"
    "✧ 버튼 누르면 뭐 필요한지 물어봐요\n"
    "✧ 그냥 글만 보내도 → 이미지 ✏️\n"
    "✧ 언제든 취소: /취소\n"
    "🦗 너무바쁜베짱이 · 코다 & 크룩스"
)

def help_text():
    if not CUSTOM:
        return HELP
    triggers = []
    for spec in CUSTOM.values():
        trigger = spec.get("trigger")
        if trigger:
            triggers.append(trigger)
    if not triggers:
        return HELP
    return HELP + "\n커스텀: " + "  ".join(triggers)

def log(*a): print("[pingpong]", *a, flush=True)
def tag_now(): return time.strftime("%m%d_%H%M%S")
def rseed(): return random.randint(0, 2**40)

def requested_image_count(text):
    t = text or ""
    m = re.search(r"(?<!\d)(10|[2-9])\s*(?:장|개|컷|枚|images?|pics?|pictures?)", t, flags=re.I)
    if m:
        return max(1, min(10, int(m.group(1))))
    kor = {"두": 2, "세": 3, "네": 4, "다섯": 5, "여섯": 6, "일곱": 7, "여덟": 8, "아홉": 9, "열": 10}
    for word, n in kor.items():
        if re.search(word + r"\s*(?:장|개|컷)", t):
            return n
    return 1

def enqueue_job(job):
    os.makedirs(QUEUE_DIR, exist_ok=True)
    job["source"] = job.get("source", "pingpong")
    seq = int(job.get("idx") or 0)
    fn = "%d_%03d_%04d.json" % (int(time.time() * 1000), seq, int.from_bytes(os.urandom(2), "big"))
    json.dump(job, open(os.path.join(QUEUE_DIR, fn), "w", encoding="utf-8"), ensure_ascii=False)

def start_dashboard():
    page_url = f"http://{DASHBOARD_OPEN_HOST}:{DASHBOARD_PORT}"
    status_url = page_url + "/api/status"
    try:
        requests.get(status_url, timeout=1)
        log("dashboard already running")
        threading.Timer(0.5, lambda: webbrowser.open(page_url)).start()
        return
    except Exception:
        pass
    try:
        flags = 0
        if os.name == "nt":
            flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        subprocess.Popen([sys.executable, os.path.join(HERE, "dashboard.py")],
                         cwd=HERE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=flags)
        threading.Timer(1.5, lambda: webbrowser.open(page_url)).start()
        log("dashboard started", page_url)
    except Exception as e:
        log("dashboard start fail:", e)

# ---------- Telegram ----------
MENU_KB = {"keyboard": [["1️⃣ 이미지", "2️⃣ 영상"],
                        ["3️⃣ 음악", "4️⃣ 인물합성"],
                        ["5️⃣ 페이스스왑", "❓ 도움말"],
                        ["⚔️ 모델대결"]],
           "resize_keyboard": True}

def tg_send(text, kb=None):
    try:
        data = {"chat_id": CHAT, "text": text}
        if kb is not None:
            data["reply_markup"] = json.dumps(kb)
        requests.post(f"{TG}/sendMessage", json=data, timeout=20)
    except Exception as e:
        log("tg_send fail:", e)

def send_menu(msg="👇 아래 버튼으로 골라주세요"):
    tg_send(msg, MENU_KB)

PHOTO_KB = {"keyboard": [["🎭 인물합성/편집", "🔀 페이스스왑"],
                         ["🎬 사진으로 영상", "❎ 취소"]],
            "resize_keyboard": True}
def tg_send_file(kind, path, caption=""):
    method = {"photo": "sendPhoto", "video": "sendVideo", "audio": "sendAudio"}[kind]
    field  = kind
    path = _resolve_existing_media_path(path)
    if not os.path.isfile(path):
        raise FileNotFoundError("generated file not found: %s (OUTDIR=%s)" % (path, OUTDIR))
    with open(path, "rb") as f:
        requests.post(f"{TG}/{method}", data={"chat_id": CHAT, "caption": caption[:1000]},
                      files={field: f}, timeout=300)

def tg_updates(offset):
    r = requests.get(f"{TG}/getUpdates", params={"offset": offset, "timeout": 30}, timeout=40)
    return r.json().get("result", [])

def tg_download(file_id, dest):
    fp = requests.get(f"{TG}/getFile", params={"file_id": file_id}, timeout=20).json()["result"]["file_path"]
    data = requests.get(f"https://api.telegram.org/file/bot{TOKEN}/{fp}", timeout=120).content
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as f:
        f.write(data)

# ---------- LM Studio (LLM) ----------
def lms(*args):
    return subprocess.run(["lms", *args], capture_output=True, text=True, encoding="utf-8")

def llm_up():
    model = current_llm_model()
    lms("server", "start")
    log("lms load", model)
    r = lms("load", model, "-y", "--gpu", "max")
    if r.returncode != 0:
        raise RuntimeError(f"lms load failed: {r.stderr or r.stdout}")
    for _ in range(30):
        try:
            ids = [m["id"] for m in requests.get(f"{LMAPI}/v1/models", timeout=5).json()["data"]]
            if ids:
                return ids[0]
        except Exception:
            pass
        time.sleep(2)
    raise RuntimeError("LM Studio API did not come up after load")

def llm_down():
    lms("unload", "--all")
    log("lms unloaded")

def _chat(model_id, system, user, max_tokens=800, temp=0.5, no_think=True):
    content = user + (" /no_think" if no_think else "")
    body = {"model": model_id,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": content}],
            "temperature": temp, "max_tokens": max_tokens}
    r = requests.post(f"{LMAPI}/v1/chat/completions", json=body, timeout=300)
    r.raise_for_status()
    msg = r.json()["choices"][0]["message"]
    out = (msg.get("content") or "").strip() or (msg.get("reasoning_content") or "").strip()
    if "</think>" in out:
        out = out.split("</think>")[-1].strip()
    return out

def llm_prompt(model_id, system, user):
    out = _chat(model_id, system, user)
    return clean_llm_prompt(out)

def _input_image_data_url(relpath):
    rel = str(relpath or "").replace("\\", "/").lstrip("/")
    full = os.path.abspath(os.path.join(INPUTDIR, rel))
    root = os.path.abspath(INPUTDIR)
    if not (full == root or full.startswith(root + os.sep)):
        raise ValueError("image path outside ComfyUI input dir")
    mime = mimetypes.guess_type(full)[0] or "image/png"
    with open(full, "rb") as f:
        payload = base64.b64encode(f.read()).decode("ascii")
    return f"data:{mime};base64,{payload}"

def clean_refsheet_prompt(out):
    text = (out or "").replace("\r", "\n").strip()
    text = re.sub(r"<think>[\s\S]*?</think>", " ", text, flags=re.I)
    text = re.sub(r"```(?:json|text)?|```", " ", text, flags=re.I)
    text = re.sub(r'^[\*\s\-]*(?:final\s+prompt|prompt|output|answer|here(?:\s+is)?)\s*[:：\*\-]+\s*', '', text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip().strip('"').strip("*").strip()
    if len(text) > 2600:
        text = text[:2600].rsplit(" ", 1)[0].strip()
    return text

def llm_refsheet_video_prompt(model_id, user, image_ref):
    image_url = _input_image_data_url(image_ref)
    content = [
        {"type": "text", "text": (user or "").strip() + " /no_think"},
        {"type": "image_url", "image_url": {"url": image_url}},
    ]
    body = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": REFSHEET_VIDEO_SYS},
            {"role": "user", "content": content},
        ],
        "temperature": 0.45,
        "max_tokens": 1400,
    }
    r = requests.post(f"{LMAPI}/v1/chat/completions", json=body, timeout=300)
    r.raise_for_status()
    msg = r.json()["choices"][0]["message"]
    out = (msg.get("content") or "").strip() or (msg.get("reasoning_content") or "").strip()
    if "</think>" in out:
        out = out.split("</think>")[-1].strip()
    return clean_refsheet_prompt(out)

def clean_llm_prompt(out):
    text = (out or "").replace("\r", "\n").strip()
    text = re.sub(r"<think>[\s\S]*?</think>", " ", text, flags=re.I)
    text = re.sub(r"```(?:json|text)?|```", " ", text, flags=re.I)
    option = re.search(
        r"(?:^|\n|\s)option\s*1\s*(?:\([^)]*\))?\s*[:：]\s*(.+?)(?=(?:\s|\n)option\s*2\s*(?:\([^)]*\))?\s*[:：]|$)",
        text,
        flags=re.I | re.S,
    )
    if option:
        text = option.group(1).strip()
    text = re.sub(r"^\s*(?:under\s+\d+\s+words?\??\s*)?(?:let'?s\s+count|word\s+count)\s*[:\-]\s*", "", text, flags=re.I)
    text = re.sub(r"\s*\(\s*\d+\s+words?\s*\)\s*\.?\s*$", "", text, flags=re.I)
    text = re.sub(r'^[\*\s\-]*(?:attempt|draft|final|version|option|prompt|answer|output|here(?:\s+is)?)\s*\d*(?:\s*\([^)]*\))?\s*[:：\*\-]+\s*',
                  '', text, flags=re.I)
    markers = [
        r"\bfinal\s+prompt\s*[:\-]",
        r"\bprompt\s*[:\-]",
        r"\boutput\s*[:\-]",
        r"\banswer\s*[:\-]",
    ]
    for pat in markers:
        hits = list(re.finditer(pat, text, flags=re.I))
        if hits:
            text = text[hits[-1].end():].strip()
            break
    bad = re.compile(
        r"(?:\b(wait|actually|however|therefore|usually|given|instruction|system prompt|the input|the user|"
        r"i should|i will|i need|let'?s|this means|looking at|since it|if i|we need|missing elements|under \d+ words?|word count)\b"
        r"|^\s*(style|missing elements)\s*:)",
        re.I,
    )
    parts = re.split(r"(?<=[.!?])\s+", text.replace("\n", " "))
    good = [p.strip().strip('"').strip("*").strip() for p in parts if p.strip() and not bad.search(p)]
    if good:
        text = " ".join(good[:3])
    else:
        lines = [l.strip().strip('"').strip("*").strip() for l in text.splitlines() if l.strip()]
        usable = [l for l in lines if not bad.search(l)] or lines
        text = usable[-1] if usable else text
    text = re.sub(r"\s*\(\s*\d+\s*\)", "", text)
    text = re.sub(r"\s*\(\s*\d+\s+words?\s*\)\s*\.?\s*$", "", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip().strip('"').strip("*").strip()
    if len(text) > 900:
        text = text[:900].rsplit(" ", 1)[0].strip()
    return text

def llm_image_variations(model_id, user, count):
    out = _chat(model_id, MULTI_IMAGE_SYS, f"Create exactly {count} prompts for: {user}", max_tokens=1800, temp=0.8)
    try:
        data = json.loads(out)
    except Exception:
        m = re.search(r"\[[\s\S]*\]", out)
        data = json.loads(m.group(0)) if m else []
    prompts = []
    for item in data:
        if isinstance(item, str):
            p = clean_llm_prompt(item)
            if p:
                prompts.append(p)
        if len(prompts) >= count:
            break
    if len(prompts) < count:
        base = llm_prompt(model_id, IMAGE_SYS, user)
        while len(prompts) < count:
            prompts.append(base + f", variation {len(prompts) + 1}, unique composition and details")
    return prompts[:count]

def _parse_prompt_intent(out):
    """LLM 출력(JSON 객체 배열)을 [{'prompt','intent'}]로 파싱. 문자열만 와도 흡수."""
    try:
        data = json.loads(out)
    except Exception:
        m = re.search(r"\[[\s\S]*\]", out)
        data = json.loads(m.group(0)) if m else []
    items = []
    for d in data:
        if isinstance(d, dict):
            p = clean_llm_prompt(d.get("prompt") or d.get("text") or "")
            it = str(d.get("intent") or d.get("note") or "").strip()
        elif isinstance(d, str):
            p, it = clean_llm_prompt(d), ""
        else:
            continue
        if p:
            items.append({"prompt": p, "intent": it})
    return items

def llm_compare_prompts(model_id, keywords):
    """키워드 리스트 → [{'prompt','intent'}] (같은 길이/순서, 1:1 매핑)."""
    n = len(keywords)
    numbered = "\n".join(f"{i + 1}. {k}" for i, k in enumerate(keywords))
    out = _chat(model_id, COMPARE_PROMPT_SYS,
                f"Expand these {n} keywords into exactly {n} objects as a JSON array, same order:\n{numbered}",
                max_tokens=2800, temp=0.85)
    items = _parse_prompt_intent(out)
    if len(items) == n:
        return items
    # 개수가 안 맞으면 키워드별 개별 확장으로 정렬 보장(의도엔 키워드 사용)
    fixed = []
    for k in keywords:
        try:
            fixed.append({"prompt": llm_prompt(model_id, IMAGE_SYS, k), "intent": k})
        except Exception:
            fixed.append({"prompt": k, "intent": k})
    return fixed

def llm_random_prompts(model_id, count, theme=""):
    """입력 없이 다양한 무작위 프롬프트 count개 생성. theme이 있으면 그 주제 안에서 다양하게."""
    count = max(1, min(20, int(count or 10)))
    theme = (theme or "").strip()
    if theme:
        user = (f'Generate exactly {count} image prompts as a JSON array, ALL on the theme: "{theme}". '
                f'Keep every prompt clearly about "{theme}", but vary the angle, setting, mood, lighting, '
                f'composition, and art style across them.')
    else:
        user = (f"Generate exactly {count} wildly diverse image prompts as a JSON array, "
                f"spanning many different subjects (portrait, landscape, animal, food, object, sci-fi, "
                f"fantasy, architecture, street, abstract).")
    out = _chat(model_id, RANDOM_PROMPT_SYS, user, max_tokens=2800, temp=1.0)
    items = _parse_prompt_intent(out)[:count]
    while len(items) < count:
        idea = (f"a creative, original, fully detailed image about {theme}" if theme
                else "a surprising, highly creative and original image idea, fully detailed")
        try:
            p = llm_prompt(model_id, IMAGE_SYS, idea)
        except Exception:
            p = f"{theme}, highly detailed, cinematic lighting" if theme else "a creative, beautiful, highly detailed scene, cinematic lighting"
        items.append({"prompt": p, "intent": (theme or "무작위")})
    return items[:count]

def fanout_image_jobs(body, count, settings=None):
    count = max(2, min(10, int(count or 2)))
    tg_send(f"🧠▸ 이미지 {count}장용 프롬프트 분해 중... 한 장당 한 큐로 보낼게요.")
    comfy_free()
    mid = llm_up()
    try:
        prompts = llm_image_variations(mid, body, count)
    finally:
        llm_down()
    for i, prompt in enumerate(prompts, 1):
        enqueue_job({"mode": "image_direct", "text": prompt, "request": body, "settings": settings or {}, "source": "fanout", "idx": i, "total": count})
    tg_send("📦▸ 독립 이미지 큐 " + str(len(prompts)) + "개 추가 완료\n" + "\n".join(f"{i}. {p[:80]}" for i, p in enumerate(prompts, 1)))

def write_generation_meta(files, mode, request_text="", generated_prompt="", note=""):
    meta = {
        "mode": mode,
        "request": request_text or "",
        "generated": generated_prompt or "",
        "note": note or "",
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    for path in files or []:
        try:
            with open(path + ".pingpong.json", "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log("meta write fail:", e)

def do_image_direct(prompt, request_text=None, settings=None):
    prompt = (prompt or "a creative, beautiful scene").strip()
    tg_send(f"🎨▸ 큐 이미지 생성 중 ░▒▓█▓▒░ ✧\n{prompt}")
    comfy_free()
    wf, save = inject_zit(prompt, settings)
    files = comfy_run(wf, save)
    write_generation_meta(files, "image", request_text or prompt, prompt)
    tg_send_file("photo", files[0], caption=prompt[:1000])
    comfy_free()

def llm_song(model_id, user):
    # 구조화 작업이라 추론 허용(no_think=False) + 넉넉한 토큰
    out = _chat(model_id, SONG_SYS, user, max_tokens=2000, temp=0.7, no_think=False)
    m = re.search(r'LYRICS:\s*', out, flags=re.I)
    tags, lyrics = "", ""
    if m:
        head = out[:m.start()]
        lyrics = out[m.end():].strip()
        tm = re.search(r'TAGS:\s*(.+)', head, flags=re.I)
        tags = tm.group(1).strip() if tm else head.strip()
    # 플레이스홀더 echo/실패 가드
    if (not tags) or ("<" in tags) or ("comma-separated" in tags.lower()):
        tags = "pop, upbeat, catchy, emotional vocals, modern production"
    if (not lyrics) or len(lyrics) < 20 or "<" in lyrics[:40]:
        lyrics = "[Verse]\n" + user + "\n[Chorus]\n" + user
    return tags[:600], lyrics

# ---------- ComfyUI ----------
def comfy_free():
    try:
        requests.post(f"{COMFY}/free", json={"unload_models": True, "free_memory": True}, timeout=30)
        log("comfy /free")
    except Exception as e:
        log("comfy free fail:", e)

def write_comfy_progress(status, pct=0, text="", prompt_id="", node=""):
    data = {
        "t": time.time(),
        "status": status,
        "pct": max(0, min(100, int(pct or 0))),
        "text": str(text or "")[:120],
        "prompt_id": str(prompt_id or ""),
        "node": str(node or ""),
    }
    tmp = PROGRESS_PATH + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, PROGRESS_PATH)
    except Exception:
        pass

def _comfy_ws_url(client_id):
    u = urllib.parse.urlparse(COMFY)
    scheme = "wss" if u.scheme == "https" else "ws"
    netloc = u.netloc
    path = (u.path.rstrip("/") if u.path else "") + "/ws"
    return urllib.parse.urlunparse((scheme, netloc, path, "", urllib.parse.urlencode({"clientId": client_id}), ""))

def _wait_comfy_ws(ws, pid):
    write_comfy_progress("queued", 1, "queued", pid)
    last_check = 0
    while True:
        try:
            raw = ws.recv()
            if not isinstance(raw, str):
                continue
            msg = json.loads(raw)
        except Exception:
            if time.time() - last_check > 5:
                last_check = time.time()
                q = requests.get(f"{COMFY}/queue", timeout=10).json()
                if not q.get("queue_running") and not q.get("queue_pending"):
                    break
            continue
        typ = msg.get("type")
        data = msg.get("data") or {}
        mpid = data.get("prompt_id") or data.get("promptId")
        if mpid and mpid != pid:
            continue
        if typ == "progress":
            value = float(data.get("value") or 0)
            maxv = float(data.get("max") or 0)
            pct = int(value * 100 / maxv) if maxv else 0
            write_comfy_progress("sampling", pct, f"{int(value)}/{int(maxv)}", pid, data.get("node", ""))
        elif typ == "executing":
            node = data.get("node")
            if node is None:
                write_comfy_progress("finalizing", 100, "finalizing", pid)
                break
            write_comfy_progress("executing", 2, "node " + str(node), pid, node)
        elif typ == "execution_error":
            write_comfy_progress("error", 0, data.get("exception_message", "error"), pid)
            break

def comfy_run(wf, save_node):
    client_id = str(uuid.uuid4())
    ws = None
    if websocket:
        try:
            ws = websocket.create_connection(_comfy_ws_url(client_id), timeout=8)
            ws.settimeout(5)
        except Exception as e:
            log("comfy ws fail:", e)
            ws = None
    write_comfy_progress("submitting", 0, "submitting")
    r = requests.post(f"{COMFY}/prompt", json={"prompt": wf, "client_id": client_id}, timeout=30)
    if r.status_code != 200:
        write_comfy_progress("error", 0, f"/prompt {r.status_code}")
        raise RuntimeError(f"comfy /prompt {r.status_code}: {r.text[:400]}")
    pid = r.json()["prompt_id"]
    log("queued", pid)
    try:
        if ws:
            _wait_comfy_ws(ws, pid)
        else:
            write_comfy_progress("queued", 1, "queued", pid)
            while True:
                q = requests.get(f"{COMFY}/queue", timeout=10).json()
                if not q["queue_running"] and not q["queue_pending"]:
                    break
                time.sleep(5)
    finally:
        try:
            if ws:
                ws.close()
        except Exception:
            pass
    outputs = requests.get(f"{COMFY}/history/{pid}", timeout=15).json()[pid]["outputs"]
    # save_node 우선, 없으면 전체에서 탐색
    files = _files_from(outputs.get(save_node, {}))
    if not files:
        for node_out in outputs.values():
            files += _files_from(node_out)
    if not files:
        raise RuntimeError("결과 파일을 찾지 못함")
    files = [_resolve_existing_media_path(f) for f in files]
    write_comfy_progress("done", 100, "done", pid)
    return files

def _files_from(node_out):
    out = []
    for v in (node_out or {}).values():
        if isinstance(v, list):
            for it in v:
                if isinstance(it, dict) and "filename" in it:
                    out.append(_comfy_result_path(it))
    return out

def _comfy_result_path(item):
    filename = str(item.get("filename") or "")
    subfolder = str(item.get("subfolder") or "")
    folder_type = str(item.get("type") or item.get("folder_type") or "output").lower()
    if os.path.isabs(filename):
        if os.path.exists(filename):
            return filename
        recovered = _resolve_existing_media_path(filename)
        if os.path.exists(recovered):
            return recovered
        downloaded = _download_comfy_result(item)
        return downloaded or recovered
    roots = []
    if folder_type == "input":
        roots.append(INPUTDIR)
    else:
        roots.append(OUTDIR)
        if folder_type == "temp":
            roots.append(os.path.join(os.path.dirname(OUTDIR), "temp"))
    roots.extend([OUTDIR, INPUTDIR])
    seen = set()
    for root in roots:
        full = os.path.abspath(os.path.join(root, subfolder, filename))
        key = os.path.normcase(full)
        if key in seen:
            continue
        seen.add(key)
        if os.path.exists(full):
            return full
    for root in (OUTDIR, INPUTDIR):
        try:
            for cur, _, files in os.walk(root):
                if filename in files:
                    return os.path.join(cur, filename)
        except Exception:
            pass
    downloaded = _download_comfy_result(item)
    return downloaded or os.path.abspath(os.path.join(roots[0], subfolder, filename))

# ---------- 워크플로 주입 ----------
def _resolve_existing_media_path(path):
    path = os.path.abspath(str(path or ""))
    if os.path.isfile(path):
        return path
    base = os.path.basename(path)
    if not base:
        return path
    normalized = path.replace("\\", "/")
    candidates = []
    for marker, root in (("/output/", OUTDIR), ("/input/", INPUTDIR)):
        idx = normalized.lower().rfind(marker)
        if idx >= 0:
            rel = normalized[idx + len(marker):]
            candidates.append(os.path.join(root, *rel.split("/")))
    for root in (OUTDIR, INPUTDIR):
        candidates.append(os.path.join(root, base))
        candidates.append(os.path.join(root, "pingpong", base))
    seen = set()
    for candidate in candidates:
        candidate = os.path.abspath(candidate)
        key = os.path.normcase(candidate)
        if key in seen:
            continue
        seen.add(key)
        if os.path.isfile(candidate):
            log("media path recovered:", path, "->", candidate)
            return candidate
    for root in (OUTDIR, INPUTDIR):
        try:
            for cur, _, files in os.walk(root):
                if base in files:
                    found = os.path.join(cur, base)
                    log("media path found by scan:", path, "->", found)
                    return found
        except Exception as e:
            log("media path scan fail:", root, e)
    return path

def _download_comfy_result(item):
    filename = str(item.get("filename") or "")
    base = os.path.basename(filename)
    if not base:
        return None
    subfolder = str(item.get("subfolder") or "")
    folder_type = str(item.get("type") or item.get("folder_type") or "output").lower()
    normalized = filename.replace("\\", "/")
    idx = normalized.lower().rfind("/output/")
    if idx >= 0:
        rel = normalized[idx + len("/output/"):]
        rel_dir = os.path.dirname(rel).replace("\\", "/")
        if rel_dir and not subfolder:
            subfolder = rel_dir
    attempts = [
        {"filename": base, "subfolder": subfolder, "type": folder_type},
        {"filename": base, "subfolder": "pingpong", "type": "output"},
        {"filename": base, "type": "output"},
    ]
    for params in attempts:
        try:
            r = requests.get(f"{COMFY}/view", params=params, timeout=60)
            if r.status_code != 200 or not r.content:
                continue
            dest = os.path.join(OUTDIR, "pingpong", base)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as f:
                f.write(r.content)
            log("downloaded comfy result:", params, "->", dest)
            return dest
        except Exception as e:
            log("comfy view fallback fail:", params, e)
    return None

def load_wf(name):
    return json.load(open(os.path.join(HERE, "workflows", name), encoding="utf-8"))

def _float_setting(settings, key, default, lo, hi):
    try:
        val = float((settings or {}).get(key, default))
    except Exception:
        val = default
    return max(lo, min(hi, val))

def _int_setting(settings, key, default, lo, hi):
    try:
        val = int(float((settings or {}).get(key, default)))
    except Exception:
        val = default
    return max(lo, min(hi, val))

def _ratio_setting(settings, default="3:4"):
    val = str((settings or {}).get("image_ratio") or default).strip()
    allowed = {"1:1", "3:4", "4:3", "2:3", "3:2", "9:16", "16:9", "21:9"}
    return val if val in allowed else default

def _custom_ratio_value(spec, ratio):
    mapping = spec.get("ratio_map") or {}
    if ratio in mapping:
        return mapping[ratio]
    return ratio

def _bool_setting(settings, key, default=False):
    val = (settings or {}).get(key, default)
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in ("1", "true", "yes", "on", "y")

def safe_filename_title(text, fallback="song"):
    s = re.sub(r"\s+", "_", (text or "").strip())
    s = re.sub(r"[^0-9A-Za-z가-힣_.-]+", "", s).strip("._-")
    return (s[:40] or fallback)

def _dims_from_ratio_mp(ratio, mp, multiple=16):
    """비율('3:4')과 목표 메가픽셀로 width/height를 계산(=A·B 동일 해상도 제어용)."""
    try:
        rw, rh = (int(x) for x in str(ratio).split(":"))
        if rw <= 0 or rh <= 0:
            raise ValueError
    except Exception:
        rw, rh = 3, 4
    target = max(0.25, min(4.0, float(mp or 1.0))) * 1_000_000
    w = (target * rw / rh) ** 0.5
    h = target / w if w else 0
    def snap(v):
        return max(multiple, int(round(v / multiple)) * multiple)
    return snap(w), snap(h)

def _set_any_megapixels(wf, megapixels):
    for node in wf.values():
        inputs = node.get("inputs") if isinstance(node, dict) else None
        if not isinstance(inputs, dict):
            continue
        for key in list(inputs.keys()):
            if key == "megapixels" or key.endswith(".megapixels"):
                inputs[key] = megapixels

def inject_custom(spec, prompt, image_refs=None, settings=None, mode_name=None):
    wf_path = os.path.join(HERE, spec["file"])
    wf = json.load(open(wf_path, encoding="utf-8"))
    tag = tag_now()
    for node, field in spec.get("prompt_nodes", []):
        wf[str(node)]["inputs"][field] = prompt
    for node, field in spec.get("timeline_prompt_nodes", []):
        raw = wf[str(node)]["inputs"].get(field, "{}")
        try:
            data = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
        except Exception:
            data = {}
        data["global_prompt"] = prompt
        wf[str(node)]["inputs"][field] = json.dumps(data, ensure_ascii=False)
    negative_prompt = spec.get("negative_prompt", "")
    for node, field in spec.get("negative_nodes", []):
        wf[str(node)]["inputs"][field] = negative_prompt
    image_refs = image_refs or []
    for i, pair in enumerate(spec.get("image_nodes", [])):
        if i >= len(image_refs):
            break
        node, field = pair
        wf[str(node)]["inputs"][field] = image_refs[i]
    if len(image_refs) > len(spec.get("image_nodes", [])) and spec.get("file", "").endswith("image_boogu_image_0_1_edit_API.json"):
        base_node = "32"
        encode_node = "45:36"
        for i, rel in enumerate(image_refs[1:], 2):
            node_id = f"{base_node}:{i}"
            wf[node_id] = copy.deepcopy(wf[base_node])
            wf[node_id]["inputs"]["image"] = rel
            wf[encode_node]["inputs"][f"images.image_{i}"] = [node_id, 0]
    for node, field in spec.get("seed_nodes", []):
        wf[str(node)]["inputs"][field] = rseed()
    for node, field, value in spec.get("set_nodes", []):
        wf[str(node)]["inputs"][field] = value
    if settings and spec.get("type") == "video" and spec.get("video_length_node"):
        node, field = spec["video_length_node"]
        fps = int(spec.get("fps", 24) or 24)
        extra = int(spec.get("length_extra_frames", 1) or 0)
        seconds = _int_setting(settings, "video_seconds", int(spec.get("default_seconds", 5) or 5), 1, 60)
        wf[str(node)]["inputs"][field] = seconds * fps + extra
    if settings and spec.get("type") == "image":
        mp = _float_setting(settings, "image_megapixels", 1.0, 0.25, 2.0)
        _set_any_megapixels(wf, mp)
        if spec.get("ratio_node"):
            node, field = spec["ratio_node"]
            wf[str(node)]["inputs"][field] = _custom_ratio_value(spec, _ratio_setting(settings))
        elif spec.get("width_nodes") or spec.get("height_nodes"):
            w, h = _dims_from_ratio_mp(_ratio_setting(settings), mp)
            for node, field in spec.get("width_nodes", []):
                wf[str(node)]["inputs"][field] = w
            for node, field in spec.get("height_nodes", []):
                wf[str(node)]["inputs"][field] = h
    if spec.get("prefix_node"):
        node, field = spec["prefix_node"]
        wf[str(node)]["inputs"][field] = spec.get("prefix", "pingpong/custom_") + tag
    apply_model_overrides(wf, mode_name or "custom", settings)
    return wf, str(spec["output_node"])

def inject_zit(prompt, settings=None):
    wf = load_wf("toobusy_zimgt.json"); tag = tag_now()
    ratio = _ratio_setting(settings)
    upscale = _bool_setting(settings, "zit_upscale", CFG.get("send_upscaled", True))
    scale_by = _float_setting(settings, "zit_scale_by", 0.5, 0.25, 1.0)
    for n in ("1", "4"):
        wf[n]["inputs"]["positive"] = prompt
        wf[n]["inputs"]["seed"] = rseed()
        wf[n]["inputs"]["ratio_preset"] = ratio
        wf[n]["inputs"]["width"] = 0
        wf[n]["inputs"]["height"] = 0
    if not CFG.get("zit_lora", False):
        wf["1"]["inputs"]["lora_slots"] = 0
        wf["1"]["inputs"]["lora_1_enable"] = False
    if MODELS.get("zit"):
        wf["1"]["inputs"]["model_name"] = wf["4"]["inputs"]["model_name"] = MODELS["zit"]
    wf["2"]["inputs"]["filename_prefix"] = f"pingpong\\img_{tag}"
    wf["5"]["inputs"]["filename_prefix"] = f"pingpong\\img_{tag}_UPS"
    wf["3"]["inputs"]["scale_by"] = scale_by
    if upscale:
        apply_model_overrides(wf, "image", settings)
        return wf, "5"
    for node in ("3", "4", "5"):
        wf.pop(node, None)
    apply_model_overrides(wf, "image", settings)
    return wf, "2"

# ---------- 모델 비교 (A vs B) ----------
def compare_candidates():
    """대결 가능한 이미지 워크플로(내장 ZIT + 입력사진 불필요한 이미지 커스텀)."""
    out = [{"id": "zit", "label": "Z-Image Turbo"}]
    for name, spec in CUSTOM.items():
        if spec.get("type") == "image" and not spec.get("image_nodes"):
            out.append({"id": "custom:" + name, "label": name})
    return out

def compare_kb():
    rows = [[c["label"]] for c in compare_candidates()]
    rows.append(["❎ 취소"])
    return {"keyboard": rows, "resize_keyboard": True}

def _compare_label(wf_id):
    if wf_id == "zit":
        return "Z-Image Turbo"
    name = wf_id.split(":", 1)[1] if wf_id.startswith("custom:") else wf_id
    try:
        name, _ = resolve_custom_workflow(name)
    except Exception:
        pass
    return name

def _compare_build(wf_id, prompt, seed, settings, prefix):
    """A/B 양쪽 워크플로를 같은 시드/해상도로 주입. 등록된 이미지 워크플로 + 내장 ZIT 지원."""
    if wf_id == "zit":
        wf, out = inject_zit(prompt, settings)
        for n in ("1", "4"):
            if n in wf and isinstance(wf[n].get("inputs"), dict):
                wf[n]["inputs"]["seed"] = seed
        if "2" in wf:
            wf["2"]["inputs"]["filename_prefix"] = prefix
        if "5" in wf:
            wf["5"]["inputs"]["filename_prefix"] = prefix + "_UPS"
        return wf, out
    name = wf_id.split(":", 1)[1] if wf_id.startswith("custom:") else wf_id
    name, spec = resolve_custom_workflow(name)
    if spec.get("type") != "image":
        raise RuntimeError(f"{name} 은(는) 이미지 워크플로가 아니라 비교에 쓸 수 없어요")
    if spec.get("image_nodes"):
        raise RuntimeError(f"{name} 은(는) 입력 사진이 필요한 워크플로라 비교 대결엔 안 맞아요")
    wf, out = inject_custom(spec, prompt, None, settings, "custom:" + name)
    # 같은 시드 고정(inject_custom은 매번 랜덤 시드를 넣으므로 덮어씀)
    for node, field in spec.get("seed_nodes", []):
        wf[str(node)]["inputs"][field] = seed
    if spec.get("prefix_node"):
        node, field = spec["prefix_node"]
        wf[str(node)]["inputs"][field] = prefix
    return wf, str(out)

def do_compare(job):
    a = job.get("a"); b = job.get("b")
    settings = job.get("settings") or {}
    tag = job.get("tag") or tag_now()
    try:
        seed = int(job.get("seed"))
    except (TypeError, ValueError):
        seed = rseed()
    raw = job.get("prompts")
    lines = raw.splitlines() if isinstance(raw, str) else list(raw or [])
    prompts = [p.strip() for p in lines if p and p.strip()][:20]
    intents = [""] * len(prompts)
    if not a or not b:
        tg_send("⚠️ 모델 대결: A/B 워크플로를 둘 다 골라주세요."); return
    is_random = bool(job.get("random"))
    if is_random:
        count = max(1, min(20, int(job.get("count") or 10)))
        theme = (job.get("theme") or "").strip()
        tg_send(f"🎲▸ {'[' + theme + '] ' if theme else ''}무작위 프롬프트 {count}개 생성 중...")
        comfy_free()
        mid = llm_up()
        try:
            items = llm_random_prompts(mid, count, theme)
            prompts = [it["prompt"] for it in items]
            intents = [it["intent"] for it in items]
        except Exception as e:
            tg_send(f"⚠️ 무작위 프롬프트 생성 실패: {e}")
            log("compare random fail:", traceback.format_exc())
            prompts, intents = [], []
        finally:
            llm_down()
        if prompts:
            tg_send("🎲 생성된 무작위 프롬프트:\n" + "\n".join(
                f"{i}. {p[:80]}" + (f"  · 👁 {intents[i-1]}" if intents[i-1] else "") for i, p in enumerate(prompts, 1)))
    if not prompts:
        tg_send("⚠️ 모델 대결: 프롬프트를 한 줄 이상 적어주세요."); return
    if not is_random and job.get("enhance"):
        tg_send(f"🧠▸ 키워드 {len(prompts)}개를 화려한 프롬프트로 확장 중...")
        comfy_free()
        mid = llm_up()
        try:
            items = llm_compare_prompts(mid, prompts)
            prompts = [it["prompt"] for it in items]
            intents = [it["intent"] for it in items]
        except Exception as e:
            tg_send(f"⚠️ 프롬프트 확장 실패, 입력 그대로 사용해요: {e}")
            log("compare enhance fail:", traceback.format_exc())
        finally:
            llm_down()
        tg_send("✅ 확장 완료:\n" + "\n".join(
            f"{i}. {p[:80]}" + (f"  · 👁 {intents[i-1]}" if i - 1 < len(intents) and intents[i-1] else "") for i, p in enumerate(prompts, 1)))
    la, lb = _compare_label(a), _compare_label(b)
    tg_send(f"⚔️ 모델 대결 시작!\nA ▸ {la}\nB ▸ {lb}\n"
            f"프롬프트 {len(prompts)}줄 · 같은 시드 {seed} · 한 줄당 1장 (총 {len(prompts) * 2}장)")
    try:
        for side, wf_id, label in (("A", a, la), ("B", b, lb)):
            comfy_free()  # 라운드 전환 시 1회만 언로드(라운드 안에서는 모델 상주 → 빠름)
            tg_send(f"▸ [{side}] {label} 생성 중... {len(prompts)}장")
            for i, prompt in enumerate(prompts, 1):
                note = intents[i - 1] if i - 1 < len(intents) else ""
                prefix = f"pingpong/cmp_{tag}_{side}{i:02d}"
                wf, out = _compare_build(wf_id, prompt, seed, settings, prefix)
                files = comfy_run(wf, out)  # 줄 사이엔 언로드 없이 연속 제출
                write_generation_meta(files, f"compare:{side}:{label}", f"[{side}] {label} · #{i}", prompt, note=note)
                if job.get("send_files") and files:
                    try:
                        cap = f"[{side}] {label} #{i}"
                        if note:
                            cap += f"\n👁 볼 포인트: {note}"
                        tg_send_file("photo", files[0], caption=cap + f"\n{prompt[:240]}")
                    except Exception as e:
                        log("compare send fail:", e)
            tg_send(f"✅ [{side}] {label} 완료")
        tg_send(f"🏁 모델 대결 끝! 갤러리에서 cmp_{tag} 로 확인하세요.")
    except Exception as e:
        tg_send(f"⚠️ 모델 대결 오류: {e}")
        log("compare error:", traceback.format_exc())
    finally:
        comfy_free()

def _apply_ltx_director_assets(td, assets, duration_frames):
    assets = assets or []
    images = [a for a in assets if a.get("kind") == "image"]
    texts = [a for a in assets if a.get("kind") == "text"]
    videos = [a for a in assets if a.get("kind") == "video"]
    audios = [a for a in assets if a.get("kind") == "audio"]
    def frames(a, fallback_start=0, fallback_length=None):
        fallback_length = duration_frames if fallback_length is None else fallback_length
        try:
            start = int(a.get("start", fallback_start))
        except Exception:
            start = fallback_start
        try:
            length = int(a.get("length", fallback_length))
        except Exception:
            length = fallback_length
        start = max(0, min(duration_frames - 1, start))
        length = max(1, min(duration_frames - start, length))
        try:
            trim = max(0, int(a.get("trimStart", 0)))
        except Exception:
            trim = 0
        return start, length, trim
    main_segments = []
    if images:
        td["segments"] = []
        for i, a in enumerate(images):
            fallback_start = min(duration_frames - 1, int(a.get("start", i)))
            start, length, trim = frames(a, fallback_start, int(a.get("length", 1) or 1))
            main_segments.append({
                "id": a.get("id") or f"img{i}",
                "type": "image",
                "start": start,
                "length": length,
                "imageFile": a["rel"],
                "isEndFrame": bool(a.get("isEndFrame", False)),
                "trimStart": trim,
            })
    if texts:
        for i, a in enumerate(texts):
            start, length, trim = frames(a, 0, max(1, min(duration_frames, int(a.get("length", duration_frames) or duration_frames))))
            main_segments.append({
                "id": a.get("id") or f"text{i}",
                "type": "text",
                "start": start,
                "length": length,
                "prompt": a.get("prompt") or a.get("text") or "",
                "trimStart": trim,
            })
    if main_segments:
        td["segments"] = sorted(main_segments, key=lambda s: int(s.get("start", 0)))
    if videos:
        td["motionSegments"] = []
        for a in videos:
            start, length, trim = frames(a)
            td["motionSegments"].append({
                "videoFile": a["rel"],
                "start": start,
                "length": length,
                "trimStart": trim,
                "videoStrength": float(a.get("videoStrength", 1.0)),
                "videoAttentionStrength": float(a.get("videoAttentionStrength", 0.65)),
                "resampleMode": a.get("resampleMode", "nearest"),
            })
    if audios:
        td["audioSegments"] = []
        for a in audios:
            start, length, trim = frames(a)
            td["audioSegments"].append({
                "audioFile": a["rel"],
                "start": start,
                "length": length,
                "trimStart": trim,
            })
    return td

def _ltx_prompt_chunks(td, global_prompt, duration_frames):
    segments = sorted((td.get("segments") or []), key=lambda s: int(s.get("start", 0)))
    if not any((s.get("prompt") or "").strip() for s in segments):
        return "", ""
    prompts = []
    lengths = []
    cursor = 0
    for seg in segments:
        start = max(0, min(duration_frames, int(seg.get("start", 0))))
        end = max(start, min(duration_frames, start + int(seg.get("length", 1))))
        if start > cursor:
            prompts.append(global_prompt or "video")
            lengths.append(start - cursor)
        if end > start:
            prompts.append((seg.get("prompt") or global_prompt or "video").strip())
            lengths.append(end - start)
        cursor = max(cursor, end)
    if cursor < duration_frames:
        prompts.append(global_prompt or "video")
        lengths.append(duration_frames - cursor)
    return " | ".join(prompts), ", ".join(str(max(1, int(x))) for x in lengths if x > 0)

def inject_ltx(prompt, director_assets=None, settings=None):
    wf = load_wf("LTX_Director_2_Workflow_ggufdis_API.json"); tag = tag_now()
    seconds = _int_setting(settings, "video_seconds", 5, 1, 20)
    frame_rate = _int_setting(settings, "video_fps", int(wf["131"]["inputs"].get("frame_rate", 24)), 8, 30)
    duration_frames = max(1, seconds * frame_rate)
    wf["131"]["inputs"]["duration_seconds"] = seconds
    wf["131"]["inputs"]["frame_rate"] = frame_rate
    wf["131"]["inputs"]["duration_frames"] = duration_frames
    wf["131"]["inputs"]["end_frame"] = duration_frames
    td = json.loads(wf["131"]["inputs"]["timeline_data"])
    td["global_prompt"] = prompt
    td["normalStartFrame"] = 0
    td["normalDurationFrames"] = duration_frames
    td["mainTrackEnabled"] = True
    td["motionTrackEnabled"] = True
    td["audioTrackEnabled"] = True
    _apply_ltx_director_assets(td, director_assets, duration_frames)
    wf["131"]["inputs"]["timeline_data"] = json.dumps(td, ensure_ascii=False)
    local_prompts, segment_lengths = _ltx_prompt_chunks(td, prompt, duration_frames)
    wf["131"]["inputs"]["local_prompts"] = local_prompts
    wf["131"]["inputs"]["segment_lengths"] = segment_lengths
    w = _int_setting(settings, "video_width", CFG.get("video_width", 0), 0, 1920)
    if w:
        wf["131"]["inputs"]["custom_width"] = w
        wf["131"]["inputs"]["custom_height"] = 0
    if MODELS.get("ltx_gguf"):
        wf["137"]["inputs"]["unet_name"] = MODELS["ltx_gguf"]
    apply_model_overrides(wf, "video", settings)
    wf["30"]["inputs"]["noise_seed"] = rseed()
    wf["37"]["inputs"]["filename_prefix"] = f"pingpong/vid_{tag}"
    return wf, "37"

def inject_ace(tags, lyrics, title_text=""):
    wf = load_wf("audio_ace_step1_5_xl_turbo_API.json"); tag = tag_now()
    wf["94"]["inputs"]["tags"] = tags
    wf["94"]["inputs"]["lyrics"] = lyrics
    if MODELS.get("ace"):
        wf["104"]["inputs"]["unet_name"] = MODELS["ace"]
    apply_model_overrides(wf, "song", {})
    wf["109"]["inputs"]["value"] = rseed()
    wf["107"]["inputs"]["filename_prefix"] = f"pingpong/song_{tag}_{safe_filename_title(title_text, 'music')}"
    return wf, "107"

def inject_klein(ref_relpath, goal_text):
    wf = load_wf("toobusy_flux2klein_vram.json"); tag = tag_now()
    board = {"version": 1, "global_note": "", "items": [
        {"id": "charA", "role": "character_a", "name": "Character A",
         "filename": ref_relpath, "note": "preserve identity"},
        {"id": "goal1", "type": "text", "text_category": "goal",
         "text": goal_text or "same person, new cinematic scene, photorealistic"}]}
    sel = {"version": 1, "blocks": [
        {"kind": "reference", "role": "character_a", "category": "character", "label": "Character A"},
        {"kind": "modifier", "category": "lighting", "text": "soft light", "label": "Lighting"},
        {"kind": "modifier", "category": "style", "text": "photoreal", "label": "Style"}]}
    wf["7"]["inputs"]["board_json"] = json.dumps(board, ensure_ascii=False)
    wf["2"]["inputs"]["director_selection_json"] = json.dumps(sel, ensure_ascii=False)
    if MODELS.get("klein"):
        wf["3"]["inputs"]["model_name"] = MODELS["klein"]
    apply_model_overrides(wf, "klein", {})
    wf["2"]["inputs"]["seed"] = rseed()
    wf["3"]["inputs"]["seed"] = rseed()
    wf["4"]["inputs"]["filename_prefix"] = f"pingpong/klein_{tag}"
    return wf, "4"

def inject_klein_faceswap(char_rel, face_rel, goal_text):
    """character(몸/포즈/의상/장면) + face(얼굴 정체성)로 face_swap 합성."""
    wf = load_wf("toobusy_flux2klein_vram.json"); tag = tag_now()
    board = {"version": 1, "global_note": "", "items": [
        {"id": "charA", "role": "character_a", "name": "Character A",
         "filename": char_rel, "note": "body, pose, outfit, scene"},
        {"id": "faceA", "role": "face_a", "name": "Face A",
         "filename": face_rel, "note": "face identity only"},
        {"id": "goal1", "type": "text", "text_category": "goal",
         "text": goal_text or "seamless natural face swap, keep body pose outfit and scene, photorealistic"}]}
    sel = {"version": 1, "blocks": [
        {"kind": "reference", "role": "character_a", "category": "character", "label": "Character A"},
        {"kind": "reference", "role": "face_a", "category": "face", "label": "Face A"},
        {"kind": "flag", "flag": "face_swap"},
        {"kind": "modifier", "category": "style", "text": "photoreal", "label": "Style"}]}
    wf["7"]["inputs"]["board_json"] = json.dumps(board, ensure_ascii=False)
    wf["2"]["inputs"]["director_selection_json"] = json.dumps(sel, ensure_ascii=False)
    if MODELS.get("klein"):
        wf["3"]["inputs"]["model_name"] = MODELS["klein"]
    apply_model_overrides(wf, "faceswap", {})
    wf["2"]["inputs"]["seed"] = rseed()
    wf["3"]["inputs"]["seed"] = rseed()
    wf["4"]["inputs"]["filename_prefix"] = f"pingpong/swap_{tag}"
    return wf, "4"

def inject_klein_board(assets, goal_text, settings=None, flags=None):
    wf = load_wf("toobusy_flux2klein_vram.json"); tag = tag_now()
    assets = assets or []
    items = []
    blocks = []
    image_count = 0
    role_category = {
        "character_a": "character", "character_b": "character", "character_c": "character", "character_d": "character",
        "face_a": "face", "face_b": "face", "outfit_a": "outfit", "outfit_b": "outfit",
        "background_a": "background", "pose_a": "pose", "style_a": "style", "prop_a": "prop",
    }
    card_passthrough = (
        "bg_remove_enabled", "bg_remove_model", "bg_remove_background",
        "face_erase_enabled", "face_keep_enabled", "face_erase_fill",
        "face_erase_expand", "face_erase_feather",
        "face_lora_enabled", "face_lora_name", "face_lora_strength",
    )
    for i, a in enumerate(assets):
        if a.get("type") == "lora" or a.get("kind") == "lora":
            lora_name = (a.get("lora_name") or a.get("name") or "").strip()
            if not lora_name:
                continue
            items.append({
                "id": f"lora{i+1}",
                "type": "lora",
                "role": a.get("role") or "lora_a",
                "name": a.get("name") or lora_name,
                "lora_name": lora_name,
                "lora_strength": a.get("lora_strength", 1.0),
                "lora_enabled": a.get("lora_enabled", a.get("enabled", True)),
            })
            continue
        if not a.get("rel"):
            continue
        role = a.get("role") or ("character_a" if image_count == 0 else "prop_a")
        name = a.get("name") or role.replace("_", " ").title()
        item = {"id": f"ref{i+1}", "role": role, "name": name, "filename": a["rel"], "note": a.get("note", "")}
        for key in card_passthrough:
            if key in a:
                item[key] = a[key]
        items.append(item)
        image_count += 1
        if a.get("enabled", True):
            blocks.append({"kind": "reference", "role": role, "category": role_category.get(role, "reference"), "label": name})
    if goal_text:
        items.append({"id": "goal1", "type": "text", "text_category": "goal", "text": goal_text})
    for text in (settings or {}).get("klein_modifiers", []) or []:
        if isinstance(text, str) and text.strip():
            blocks.append({"kind": "modifier", "category": "custom", "text": text.strip(), "label": text.strip()[:24]})
    for flag in flags or (settings or {}).get("klein_flags", []) or []:
        if flag:
            blocks.append({"kind": "flag", "flag": flag})
    if not blocks:
        blocks.append({"kind": "modifier", "category": "style", "text": "photoreal", "label": "Style"})
    board = {"version": 1, "global_note": (settings or {}).get("klein_note", ""), "items": items}
    sel = {"version": 1, "blocks": blocks}
    wf["7"]["inputs"]["board_json"] = json.dumps(board, ensure_ascii=False)
    wf["2"]["inputs"]["director_selection_json"] = json.dumps(sel, ensure_ascii=False)
    wf["2"]["inputs"]["toobusy_bundle"] = ["7", 0]
    wf["3"]["inputs"]["toobusy_bundle"] = ["8", 0]
    wf["3"]["inputs"]["use_bundle_prompt"] = True
    wf["3"]["inputs"]["use_bundle_loras"] = True
    wf["3"]["inputs"]["reference_slots"] = max(1, min(8, image_count))
    wf["3"]["inputs"]["bundle_reference_order"] = (settings or {}).get("bundle_reference_order", "auto")
    if MODELS.get("klein"):
        wf["3"]["inputs"]["model_name"] = MODELS["klein"]
    apply_model_overrides(wf, "klein", settings)
    wf["2"]["inputs"]["seed"] = rseed()
    wf["3"]["inputs"]["seed"] = rseed()
    wf["4"]["inputs"]["filename_prefix"] = f"pingpong/klein_board_{tag}"
    return wf, "4"

# ---------- 모드 라우팅 ----------
def detect_mode(text):
    low = text.lower()
    if low in ("/help", "/?", "help", "도움말"):
        return "help", ""
    table = [("/video", "video"), ("/영상", "video"),
             ("/song", "song"), ("/음악", "song"), ("/노래", "song"),
             ("/image", "image"), ("/그림", "image"),
             ("/klein", "klein"), ("/합성", "klein"), ("/인물", "klein")]
    for kw, mode in table:
        if low.startswith(kw):
            return mode, text[len(kw):].strip()
    for name, spec in reload_custom_workflows().items():
        trigger = (spec.get("trigger") or "").strip()
        if trigger and low.startswith(trigger.lower()):
            return "custom:" + name, text[len(trigger):].strip()
    return "image", text

# ---------- 핸들러 ----------
def do_custom_text(mode, body, image_refs=None, settings=None):
    name = mode.split(":", 1)[1]
    try:
        name, spec = resolve_custom_workflow(name)
        kind = {"image": "photo", "video": "video", "audio": "audio"}[spec["type"]]
        image_refs = image_refs or []
        required_images = len(spec.get("image_nodes", []))
        if required_images and len(image_refs) < required_images:
            tg_send(f"⚠️ {name} 워크플로는 이미지 {required_images}장이 필요해요. 대시보드에서 이미지를 첨부해 실행해주세요.")
            return
        comfy_free()
        llm_mode = spec.get("llm", "none")
        if llm_mode == "none":
            prompt = body
        else:
            tg_send("🧠▸ 두뇌 가동...")
            mid = llm_up()
            try:
                if llm_mode == "refsheet_video":
                    prompt = llm_refsheet_video_prompt(mid, body, image_refs[0])
                else:
                    sys_p = VIDEO_SYS if llm_mode == "video" else IMAGE_SYS
                    prompt = llm_prompt(mid, sys_p, body)
            finally:
                llm_down()
        tg_send("▸ 생성 중 ░▒▓ : " + prompt[:120])
        wf, out = inject_custom(spec, prompt, image_refs, settings, "custom:" + name)
        files = comfy_run(wf, out)
        write_generation_meta(files, "custom:" + name, body, prompt)
        tg_send_file(kind, files[0], caption=prompt[:1000])
        comfy_free()
    except (KeyError, IndexError, TypeError, json.JSONDecodeError, OSError) as e:
        tg_send(f"⚠️ 커스텀 워크플로 설정 오류: {e}")
        log("custom workflow config error:", traceback.format_exc())

def do_text(mode, body, director_assets=None, settings=None):
    if mode == "help" or not body:
        tg_send(help_text()); return
    if mode.startswith("custom:"):
        do_custom_text(mode, body)
        return
    if mode == "klein":
        tg_send("🎭 Klein 인물합성은 사진을 첨부해서 보내주세요 (캡션에 원하는 장면).")
        return
    if mode == "image":
        count = requested_image_count(body)
        if count > 1:
            fanout_image_jobs(body, count, settings)
            return
    tg_send(f"🧠▸ 두뇌 가동... ░▒▓ ({mode})\n> {body}")
    comfy_free()
    mid = llm_up()
    try:
        if mode == "song":
            tags, lyrics = llm_song(mid, body)
            payload_desc = f"🎵 태그: {tags}"
        elif mode == "video":
            prompt = llm_prompt(mid, VIDEO_SYS, body); payload_desc = prompt
        else:
            prompt = llm_prompt(mid, IMAGE_SYS, body); payload_desc = prompt
    finally:
        llm_down()
    log("PAYLOAD:", payload_desc[:200])
    if mode == "song":
        wf, save = inject_ace(tags, lyrics, body); kind = "audio"
        tg_send(f"🎼▸ 작곡 중 ░▒▓█▓▒░ ♪♬\n{payload_desc}")
    elif mode == "video":
        wf, save = inject_ltx(prompt, director_assets, settings); kind = "video"
        tg_send(f"🎬▸ 필름 감는 중 📼 (수 분 소요) ░▒▓\n{prompt}")
    else:
        wf, save = inject_zit(prompt, settings); kind = "photo"
        tg_send(f"🎨▸ 그리는 중 ░▒▓█▓▒░ ✧\n{prompt}")
    files = comfy_run(wf, save)
    if mode == "song":
        write_generation_meta(files, mode, body, f"TAGS:\n{tags}\n\nLYRICS:\n{lyrics}")
    else:
        write_generation_meta(files, mode, body, prompt)
    tg_send_file(kind, files[0], caption=payload_desc)
    comfy_free()

def download_ref(file_id):
    """텔레그램 사진을 ComfyUI input 폴더에 저장하고 상대경로 반환."""
    tag = tag_now() + f"_{random.randint(100,999)}"
    rel = f"toobusy_reference_board/images/pp_{tag}.jpg"
    dest = os.path.join(INPUTDIR, "toobusy_reference_board", "images", f"pp_{tag}.jpg")
    tg_download(file_id, dest)
    return rel

def _klein_core(rel, caption):
    tg_send(f"🎭▸ 합성 중 ░▒▓█▓▒░ (같은 얼굴, 새 장면)\n장면: {caption or '(자동)'}")
    comfy_free()
    wf, save = inject_klein(rel, caption)
    files = comfy_run(wf, save)
    write_generation_meta(files, "klein", caption or "", caption or "")
    tg_send_file("photo", files[0], caption=caption or "✦ 합성 완료 ✦")
    comfy_free()

def do_klein(file_id, caption):
    _klein_core(download_ref(file_id), caption)

def do_video_from_photo(file_id, caption):
    rel = download_ref(file_id)
    prompt = caption or "animate this image into a short cinematic video with natural motion"
    do_text("video", prompt, [{"kind": "image", "rel": rel}])

def do_faceswap(char_rel, face_rel, goal):
    tg_send(f"🔀▸ 얼굴 교체 중 ░▒▓█▓▒░ 👤↔👤\n장면: {goal or '(원본 장면 유지)'}")
    comfy_free()
    wf, save = inject_klein_faceswap(char_rel, face_rel, goal)
    files = comfy_run(wf, save)
    write_generation_meta(files, "faceswap", goal or "", goal or "")
    tg_send_file("photo", files[0], caption="✦ 페이스 스왑 완료 ✦ 👤↔👤")
    comfy_free()

def do_klein_board(reference_assets, goal, settings=None, flags=None):
    if not reference_assets:
        tg_send("⚠️ Klein 레퍼런스 보드에는 이미지가 최소 1장 필요해요.")
        return
    tg_send(f"🎭▸ 레퍼런스 보드 생성 중 ░▒▓█▓▒░\n{goal or '(보드 기반 자동 생성)'}")
    comfy_free()
    wf, save = inject_klein_board(reference_assets, goal, settings, flags)
    files = comfy_run(wf, save)
    write_generation_meta(files, "klein_board", goal or "", goal or "")
    tg_send_file("photo", files[0], caption=goal or "✦ 레퍼런스 보드 생성 완료 ✦")
    comfy_free()

# 단계별 대화 상태 (단일 사용자 가정)
STATE = {"flow": None, "goal": "", "char_rel": None, "pending_photo": None, "pending_cap": "",
         "cmp_a": None, "cmp_b": None, "cmp_enhance": False}
def reset_state():
    STATE.update({"flow": None, "goal": "", "char_rel": None, "pending_photo": None, "pending_cap": "",
                  "cmp_a": None, "cmp_b": None, "cmp_enhance": False})

SWAP_KW = ("/페이스스왑", "/faceswap", "/얼굴바꾸기", "/얼굴")

# 메뉴 버튼/숫자 → 모드
def menu_pick(text):
    t = text.strip()
    if t and t[0] in "12345" and len(t) <= 2:   # "1" ~ "5" (이모지 숫자 키 제외)
        return {"1": "image", "2": "video", "3": "song", "4": "klein", "5": "faceswap"}[t[0]]
    for kw, m in (("모델대결", "compare"), ("이미지", "image"), ("영상", "video"), ("음악", "song"),
                  ("인물합성", "klein"), ("페이스스왑", "faceswap"), ("도움말", "help")):
        if kw in t:
            return m
    return None

def start_mode(mode):
    reset_state()
    if mode == "image":
        STATE["flow"] = "await_image"; tg_send("🎨 무엇을 그릴까요? 설명을 보내주세요.")
    elif mode == "video":
        STATE["flow"] = "await_video"; tg_send("🎬 어떤 영상? 설명을 보내주세요. (대사는 \"따옴표\")")
    elif mode == "song":
        STATE["flow"] = "await_song"; tg_send("🎵 어떤 음악? 주제/분위기를 보내주세요.")
    elif mode == "klein":
        STATE["flow"] = "await_klein"; tg_send("🎭 인물 사진 1장을 보내주세요. (캡션에 원하는 장면)")
    elif mode == "faceswap":
        STATE["flow"] = "swap_char"
        tg_send("🔀 페이스 스왑 시작!\n① '몸/포즈/의상' 담당 사진을 보내주세요 (1/2)\n(취소: /취소)")
    elif mode == "compare":
        if len(compare_candidates()) < 2:
            send_menu("⚔️ 대결하려면 이미지 워크플로가 2개 이상 필요해요. (워크플로우등록.bat으로 등록)")
            return
        STATE["flow"] = "compare_a"
        tg_send("⚔️ 모델 대결!\n① A 워크플로를 골라주세요 👇", compare_kb())

def handle_message(msg):
    text = (msg.get("caption") or msg.get("text") or "").strip()
    low = text.lower()
    photo = msg.get("photo")
    f = STATE["flow"]

    # 취소
    if low in ("/취소", "/cancel", "취소") or text == "❎ 취소":
        reset_state(); send_menu("❎ 취소했어요."); return

    # 메뉴/도움말
    if low in ("/start", "/메뉴", "/menu", "메뉴"):
        send_menu("🏓 핑퐁 봇\n" + help_text()); return

    # 사진을 받고 '뭘 할지' 고르는 단계 (메뉴매칭보다 먼저 처리)
    if f == "photo_action":
        if "페이스스왑" in text or "faceswap" in low:
            STATE["char_rel"] = download_ref(STATE["pending_photo"])
            STATE["flow"] = "swap_face"; STATE["pending_photo"] = None
            tg_send("🔀 몸/장면 사진 접수! 이제 얼굴 사진을 보내주세요. (2/2)"); return
        if ("인물합성" in text) or ("편집" in text):
            cap = STATE["pending_cap"]; fid = STATE["pending_photo"]
            if cap:
                reset_state(); do_klein(fid, cap); send_menu("✦･ﾟ: 완성! :ﾟ･✦  ヽ(•‿•)ノ  다음은?"); return
            STATE["flow"] = "await_klein_scene"
            tg_send("🎨 어떤 장면/편집을 원하세요? 글로 보내주세요."); return
        if ("영상" in text) or ("video" in low):
            cap = STATE["pending_cap"]; fid = STATE["pending_photo"]
            if cap:
                reset_state(); do_video_from_photo(fid, cap); send_menu("✦･ﾟ: 완성! :ﾟ･✦  ヽ(•‿•)ノ  다음은?"); return
            STATE["flow"] = "await_photo_video_scene"
            tg_send("🎬 이 사진을 어떤 영상으로 만들까요? 움직임/분위기를 글로 보내주세요.\n(취소: /cancel)")
            return
        tg_send("버튼 중 하나를 골라주세요. (취소: /cancel)"); return

    if f == "await_klein_scene":
        if not text:
            tg_send("✏️ 원하는 장면을 글로 보내주세요. (취소: /취소)"); return
        fid = STATE["pending_photo"]; reset_state()
        do_klein(fid, text); send_menu("✦･ﾟ: 완성! :ﾟ･✦  ヽ(•‿•)ノ  다음은?"); return
    if f == "await_photo_video_scene":
        if not text:
            tg_send("🎬 영상 설명을 글로 보내주세요. 예: 카메라가 천천히 다가가고 배경이 부드럽게 움직임")
            return
        fid = STATE["pending_photo"]; reset_state()
        do_video_from_photo(fid, text); send_menu("✦･ﾟ: 완성! :ﾟ･✦  ヽ(•‿•)ノ  다음은?"); return

    # 모델 대결 플로우 (A 선택 → B 선택 → 프롬프트)
    if f == "compare_a":
        sel = next((c for c in compare_candidates() if c["label"] == text), None)
        if not sel:
            tg_send("👇 버튼에서 A 워크플로를 골라주세요. (취소: /취소)", compare_kb()); return
        STATE["cmp_a"] = sel["id"]; STATE["flow"] = "compare_b"
        tg_send(f"🅰 A = {sel['label']}\n② 이제 B 워크플로를 골라주세요 👇", compare_kb()); return
    if f == "compare_b":
        sel = next((c for c in compare_candidates() if c["label"] == text), None)
        if not sel:
            tg_send("👇 버튼에서 B 워크플로를 골라주세요. (취소: /취소)", compare_kb()); return
        STATE["cmp_b"] = sel["id"]; STATE["flow"] = "compare_mode"
        mode_kb = {"keyboard": [["✨ 키워드(LLM 확장)"], ["✍️ 직접 프롬프트"], ["🎲 무작위 (LLM이 알아서)"], ["❎ 취소"]], "resize_keyboard": True}
        tg_send(f"🅱 B = {sel['label']}\n③ 프롬프트 방식을 골라주세요 👇\n✨ 키워드 = 단어만 줘도 LLM이 화려하게 써줌\n✍️ 직접 = 내가 쓴 프롬프트 그대로\n🎲 무작위 = 아무것도 안 줘도 LLM이 다양하게 10장 생성", mode_kb); return
    if f == "compare_mode":
        if "무작위" in text or "🎲" in text:
            STATE["flow"] = "compare_random_theme"
            tg_send("🎲 무작위 모드!\n④ 주제가 있으면 보내주세요 (예: 고양이, 사이버펑크 도시, 음식).\n완전 무작위로 하려면 '무작위' 또는 '-' 를 보내세요."); return
        if "키워드" in text or "✨" in text:
            STATE["cmp_enhance"] = True; STATE["flow"] = "compare_prompts"
            tg_send("✨ 키워드 모드!\n④ 키워드를 한 줄에 하나씩 보내주세요 — 줄마다 LLM이 화려하게 확장해서 양쪽에서 생성해요.\n예:\n용\n사이버펑크 도시\n눈 내리는 오두막"); return
        if "직접" in text or "✍" in text:
            STATE["cmp_enhance"] = False; STATE["flow"] = "compare_prompts"
            tg_send("✍️ 직접 모드!\n④ 프롬프트를 한 줄에 하나씩(줄바꿈) 보내주세요.\n예:\ncyberpunk city at night, rain\nsnowy cabin in pine forest, golden hour"); return
        tg_send("👇 ✨키워드 · ✍️직접 · 🎲무작위 중 하나를 골라주세요. (취소: /취소)"); return
    if f == "compare_random_theme":
        theme = text.strip()
        if theme in ("-", "무작위", "없음", "랜덤", "x", "X"):
            theme = ""
        a, b = STATE["cmp_a"], STATE["cmp_b"]; la, lb = _compare_label(a), _compare_label(b)
        reset_state()
        enqueue_job({"mode": "compare", "a": a, "b": b, "random": True, "count": 10, "theme": theme,
                     "seed": rseed(), "tag": tag_now(),
                     "settings": {"image_ratio": "3:4", "image_megapixels": 1.0, "zit_upscale": False},
                     "send_files": True, "source": "telegram"})
        tlabel = f"[{theme}] " if theme else ""
        send_menu(f"🎲 {tlabel}무작위 대결 큐에 넣었어요!\n🅰 {la} vs 🅱 {lb}\nLLM이 알아서 10장 × 2 = 20장 · 비율 3:4 · 같은 시드\n프롬프트부터 뽑고 생성되는 대로 사진으로 보내드릴게요."); return
    if f == "compare_prompts":
        P = [ln.strip() for ln in text.splitlines() if ln.strip()][:20]
        if not P:
            tg_send("✏️ 한 줄 이상 보내주세요. (취소: /취소)"); return
        a, b = STATE["cmp_a"], STATE["cmp_b"]; enhance = STATE["cmp_enhance"]
        la, lb = _compare_label(a), _compare_label(b)
        reset_state()
        enqueue_job({"mode": "compare", "a": a, "b": b, "prompts": P, "seed": rseed(), "tag": tag_now(),
                     "settings": {"image_ratio": "3:4", "image_megapixels": 1.0, "zit_upscale": False},
                     "enhance": enhance, "send_files": True, "source": "telegram"})
        kind = "키워드(LLM 확장)" if enhance else "직접 프롬프트"
        send_menu(f"⚔️ 대결 큐에 넣었어요!\n🅰 {la} vs 🅱 {lb}\n{kind} {len(P)}줄 × 2 = {len(P) * 2}장 · 비율 3:4 · 같은 시드\n생성되는 대로 사진으로 보내드릴게요. (시간 좀 걸려요)")
        return

    # 메뉴 버튼/숫자 선택 (사진 없는 순수 텍스트)
    if not photo:
        picked = menu_pick(text)
        if picked == "help":
            send_menu("🏓 핑퐁 봇\n" + help_text()); return
        if picked:
            start_mode(picked); return

    # 진행 중인 플로우 (텍스트/사진 입력형)
    if f in ("await_image", "await_video", "await_song"):
        if not text:
            tg_send("✏️ 설명을 글로 보내주세요. (취소: /취소)"); return
        mode = f.replace("await_", ""); reset_state()
        do_text(mode, text); send_menu("✦･ﾟ: 완성! :ﾟ･✦  ヽ(•‿•)ノ  다음은?"); return
    if f == "await_klein":
        if not photo:
            tg_send("📷 인물 사진을 보내주세요. (취소: /취소)"); return
        cap = text; reset_state()
        do_klein(photo[-1]["file_id"], cap); send_menu("✦･ﾟ: 완성! :ﾟ･✦  ヽ(•‿•)ノ  다음은?"); return
    if f == "swap_char":
        if not photo:
            tg_send("📷 몸/포즈 사진을 보내주세요. (취소: /취소)"); return
        STATE["char_rel"] = download_ref(photo[-1]["file_id"]); STATE["flow"] = "swap_face"
        tg_send("✅ 몸 사진 접수! ② 이제 '얼굴' 담당 사진을 보내주세요 (2/2)"); return
    if f == "swap_face":
        if not photo:
            tg_send("📷 얼굴 사진을 보내주세요. (취소: /취소)"); return
        face_rel = download_ref(photo[-1]["file_id"]); char_rel = STATE["char_rel"]; goal = STATE["goal"]
        reset_state()
        do_faceswap(char_rel, face_rel, goal); send_menu("✦･ﾟ: 완성! :ﾟ･✦  ヽ(•‿•)ノ  다음은?"); return

    # 프리픽스 직접 입력 (하위 호환: /페이스스왑 등)
    if any(low.startswith(k) for k in SWAP_KW):
        for k in SWAP_KW:
            if low.startswith(k):
                STATE["goal"] = text[len(k):].strip(); break
        STATE["flow"] = "swap_char"
        tg_send("🔀 페이스 스왑 시작!\n① '몸/포즈/의상' 담당 사진을 보내주세요 (1/2)"); return

    # 기본: 사진은 '뭘 할지' 물어보고, 텍스트는 이미지 생성
    if photo:
        STATE["flow"] = "photo_action"
        STATE["pending_photo"] = photo[-1]["file_id"]; STATE["pending_cap"] = text
        tg_send("📷✨ 사진 접수! 이걸로 뭘 할까요? ✨", PHOTO_KB); return
    mode, body = detect_mode(text)
    do_text(mode, body)

# ---------- 사전점검 ----------
def _node_info(cls):
    try:
        r = requests.get(f"{COMFY}/object_info/{cls}", timeout=5)
        if r.status_code == 200:
            return r.json().get(cls)
    except Exception:
        pass
    return None

def _model_list(cls, field):
    """로더 노드가 인식 중인 모델 파일 목록 (드롭다운 옵션)."""
    info = _node_info(cls)
    if not info:
        return None
    for sect in ("required", "optional"):
        try:
            opts = info["input"][sect][field][0]
            if isinstance(opts, list):
                return opts
        except Exception:
            continue
    return None

def _has(filename, available):
    if available is None:
        return True  # 목록을 못 읽으면 통과(거짓경보 방지)
    norm = lambda s: s.replace("\\", "/").lower()
    return norm(filename) in [norm(x) for x in available]

# (표시명, 필요노드들, 모델로더클래스, 필드, 모델키)
FEATURE_CHECKS = [
    ("이미지(ZIT)", ["ToobusyZImageTurbo", "ToobusyHiresUpscale"], "ToobusyZImageTurbo", "model_name", "zit"),
    ("영상(LTX)", ["LTXDirector", "UnetLoaderGGUF", "VAEDecodeTiled"], "UnetLoaderGGUF", "unet_name", "ltx_gguf"),
    ("음악(ACE)", ["TextEncodeAceStepAudio1.5"], "UNETLoader", "unet_name", "ace"),
    ("인물합성/스왑(Klein)", ["ToobusyFlux2Klein", "ToobusyReferenceBoard"], "ToobusyFlux2Klein", "model_name", "klein"),
]

def preflight():
    try:
        import healthcheck
        rep = healthcheck.run_checks()
        lines = [f"{healthcheck.icon(level)} {text}" for level, text in rep.rows]
        comfy_ok = not any(level == "fail" and "ComfyUI 연결" in text for level, text in rep.rows)
        return comfy_ok, lines[:24]
    except Exception as e:
        lines = [f"⚠️ 새 점검 도구 실행 실패: {e}"]
        try:
            requests.get(f"{COMFY}/system_stats", timeout=5)
            lines.append("✅ ComfyUI 연결됨")
            return True, lines
        except Exception:
            lines.append("❌ ComfyUI 연결 안 됨")
            return False, lines

CONSOLE_BANNER = r"""
   ____  _____ _   _  ____    ____  _____ _   _  ____
  |  _ \|_   _| \ | |/ ___|  |  _ \|_   _| \ | |/ ___|
  | |_) | | | |  \| | |  _   | |_) | | | |  \| | |  _
  |  __/  | | | |\  | |_| |  |  __/  | | | |\  | |_| |
  |_|     |_| |_| \_|\____|  |_|     |_| |_| \_|\____|
     🦗 ░▒▓  너 무 바 쁜 베 짱 이   S T U D I O  ▓▒░ 🦗
           *  .  made by  코다 & 크룩스  .  *
"""

# ---------- main ----------
def main():
    print(CONSOLE_BANNER, flush=True)
    start_heartbeat()
    start_dashboard()
    print("  +--------------------[ 사전점검 ]--------------------+", flush=True)
    comfy_ok, lines = preflight()
    for ln in lines:
        print("   " + ln, flush=True)
    print("  +---------------------------------------------------+", flush=True)
    print("  >> INSERT COIN... 폰에서 봇에게 메시지를 보내세요 <<\n", flush=True)
    summary = (TG_BANNER + "\n✦ 가동 완료! ✦\n\n[ 점검 결과 ]\n" +
               "\n".join(lines) + "\n\n" + help_text())
    send_menu(summary)
    init = tg_updates(0)
    offset = (init[-1]["update_id"] + 1) if init else 0
    beat = 0
    while True:
        try:
            process_queue()
            ups = tg_updates(offset)
            if not ups:  # 대기 중 — 살아있다는 표시
                beat += 1
                print(f"  [{time.strftime('%H:%M:%S')}] 대기 중... (메시지 기다리는 중) #{beat}", flush=True)
            for upd in ups:
                offset = upd["update_id"] + 1
                msg = upd.get("message") or {}
                if msg.get("chat", {}).get("id") != CHAT:
                    continue
                if not (msg.get("text") or msg.get("photo")):
                    continue
                try:
                    handle_message(msg)
                except Exception as e:
                    tg_send(f"⚠️ 오류: {e}")
                    log("handle error:", traceback.format_exc())
        except Exception as e:
            log("loop error:", e)
            time.sleep(5)

if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "once":
        # python pingpong.py once "<요청>"  (기본 image, /영상 /음악 프리픽스 가능)
        mode, body = detect_mode(sys.argv[2])
        do_text(mode, body)
    else:
        main()
