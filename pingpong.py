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
import os, sys, json, copy, time, random, subprocess, traceback, re
import requests

try:  # 윈도우 cp949 콘솔에서 이모지/한글 출력 시 크래시 방지
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(HERE, "config.json")

def auto_comfy_dirs():
    """ComfyUI Desktop 기본 공유 폴더 자동 감지 (%LOCALAPPDATA%)."""
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

# VRAM 급에 맞게 갈아끼울 수 있는 모델 파일명 (config "models"로 덮어쓰기, 없으면 기본값)
DEFAULT_MODELS = {
    "zit":      "ZIT\\zImageTurbo_turbo.safetensors",
    "ltx_gguf": "LTX23\\ltx23DEVGGUFUnsloth_q4km.gguf",
    "klein":    "FLUX2\\flux-2-klein-9b-kv-fp8.safetensors",
    "ace":      "aceStepAudioGen_v15XLTurbo.safetensors",
}
MODELS = CFG.get("models", {}) or {}
def model_of(key):
    return MODELS.get(key) or DEFAULT_MODELS[key]

ALIVE_FILE = os.path.join(OUTDIR, "pingpong", ".alive")
def beat_alive():
    try:
        os.makedirs(os.path.dirname(ALIVE_FILE), exist_ok=True)
        with open(ALIVE_FILE, "w") as f:
            f.write(str(time.time()))
    except Exception:
        pass

# 공유 작업 큐 (대시보드가 job json을 떨궈두면 봇이 순서대로 처리 → GPU 충돌 방지)
QUEUE_DIR = os.path.join(HERE, "queue")
def run_job(job):
    m = job.get("mode")
    txt = (job.get("text") or "").strip()
    if m in ("image", "video", "song"):
        do_text(m, txt or "a creative, beautiful scene")
    elif m == "klein":
        _klein_core(job["char_rel"], txt)
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
    "You are an expert text-to-image prompt engineer for a photorealistic diffusion model. "
    "Convert the user's request (any language) into ONE vivid, concrete English image prompt. "
    "Describe subject, setting, lighting, mood, composition, and style. Keep it under 80 words. "
    "Write it as ONE plain sentence. Do NOT number the words, do NOT add counters or any (1)(2) markers. "
    "Output ONLY the prompt text, no quotes, no preamble, no notes."
)
VIDEO_SYS = (
    "You are an expert video prompt engineer for a text-to-video model. "
    "Convert the user's request (any language) into ONE vivid English video prompt describing "
    "the subject, scene, action/motion, and camera movement. If the user wants spoken dialogue, "
    "keep that line in its ORIGINAL language inside double quotes. Keep it under 70 words. "
    "Write it as plain prose. Do NOT number the words or add any (1)(2) counters. "
    "Output ONLY the prompt text, no preamble, no notes."
)
SONG_SYS = (
    "You are a professional songwriter and music producer. Given a theme (any language), respond "
    "in EXACTLY this format and nothing else:\n"
    "TAGS: <comma-separated English genre/mood/instrument/vocal tags>\n"
    "LYRICS:\n<full song lyrics with [Verse]/[Chorus]/[Bridge] section tags>"
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

def log(*a): print("[pingpong]", *a, flush=True)
def tag_now(): return time.strftime("%m%d_%H%M%S")
def rseed(): return random.randint(0, 2**40)

# ---------- Telegram ----------
MENU_KB = {"keyboard": [["1️⃣ 이미지", "2️⃣ 영상"],
                        ["3️⃣ 음악", "4️⃣ 인물합성"],
                        ["5️⃣ 페이스스왑", "❓ 도움말"]],
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

PHOTO_KB = {"keyboard": [["🎭 인물합성/편집", "🔀 페이스스왑"], ["❎ 취소"]],
            "resize_keyboard": True}

def tg_send_file(kind, path, caption=""):
    method = {"photo": "sendPhoto", "video": "sendVideo", "audio": "sendAudio"}[kind]
    field  = kind
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
    lms("server", "start")
    log("lms load", MODEL)
    r = lms("load", MODEL, "-y", "--gpu", "max")
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
    lines = [l.strip().strip('"').strip("*").strip() for l in out.splitlines() if l.strip()]
    if lines:
        out = max(lines, key=len)
    out = re.sub(r'^[\*\s\-]*(?:attempt|draft|final|version|option|prompt|here(?:\s+is)?)\s*\d*\s*[:\*\-]+\s*',
                 '', out, flags=re.I)
    out = re.sub(r'\s*\(\s*\d+\s*\)', '', out)   # 단어 카운터 "(1) (2)..." 제거
    out = re.sub(r'\s{2,}', ' ', out)            # 중복 공백 정리
    return out.strip().strip('"').strip("*").strip()

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

def comfy_run(wf, save_node):
    data = json.dumps({"prompt": wf}).encode("utf-8")
    r = requests.post(f"{COMFY}/prompt", data=data,
                      headers={"Content-Type": "application/json"}, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"comfy /prompt {r.status_code}: {r.text[:400]}")
    pid = r.json()["prompt_id"]
    log("queued", pid)
    while True:
        q = requests.get(f"{COMFY}/queue", timeout=10).json()
        if not q["queue_running"] and not q["queue_pending"]:
            break
        time.sleep(5)
    outputs = requests.get(f"{COMFY}/history/{pid}", timeout=15).json()[pid]["outputs"]
    # save_node 우선, 없으면 전체에서 탐색
    files = _files_from(outputs.get(save_node, {}))
    if not files:
        for node_out in outputs.values():
            files += _files_from(node_out)
    if not files:
        raise RuntimeError("결과 파일을 찾지 못함")
    return files

def _files_from(node_out):
    out = []
    for v in (node_out or {}).values():
        if isinstance(v, list):
            for it in v:
                if isinstance(it, dict) and "filename" in it:
                    out.append(os.path.join(OUTDIR, it.get("subfolder", ""), it["filename"]))
    return out

# ---------- 워크플로 주입 ----------
def load_wf(name):
    return json.load(open(os.path.join(HERE, "workflows", name), encoding="utf-8"))

def inject_zit(prompt):
    wf = load_wf("toobusy_zimgt.json"); tag = tag_now()
    for n in ("1", "4"):
        wf[n]["inputs"]["positive"] = prompt
        wf[n]["inputs"]["seed"] = rseed()
    if not CFG.get("zit_lora", False):
        wf["1"]["inputs"]["lora_slots"] = 0
        wf["1"]["inputs"]["lora_1_enable"] = False
    if MODELS.get("zit"):
        wf["1"]["inputs"]["model_name"] = wf["4"]["inputs"]["model_name"] = MODELS["zit"]
    wf["2"]["inputs"]["filename_prefix"] = f"pingpong\\img_{tag}"
    wf["5"]["inputs"]["filename_prefix"] = f"pingpong\\img_{tag}_UPS"
    save = "5" if CFG.get("send_upscaled", True) else "2"
    return wf, save

def inject_ltx(prompt):
    wf = load_wf("LTX_Director_2_Workflow_ggufdis_API.json"); tag = tag_now()
    td = json.loads(wf["131"]["inputs"]["timeline_data"])
    td["global_prompt"] = prompt
    wf["131"]["inputs"]["timeline_data"] = json.dumps(td, ensure_ascii=False)
    w = CFG.get("video_width", 0)
    if w:
        wf["131"]["inputs"]["custom_width"] = w
        wf["131"]["inputs"]["custom_height"] = 0
    if MODELS.get("ltx_gguf"):
        wf["137"]["inputs"]["unet_name"] = MODELS["ltx_gguf"]
    wf["30"]["inputs"]["noise_seed"] = rseed()
    wf["37"]["inputs"]["filename_prefix"] = f"pingpong/vid_{tag}"
    return wf, "37"

def inject_ace(tags, lyrics):
    wf = load_wf("audio_ace_step1_5_xl_turbo_API.json"); tag = tag_now()
    wf["94"]["inputs"]["tags"] = tags
    wf["94"]["inputs"]["lyrics"] = lyrics
    if MODELS.get("ace"):
        wf["104"]["inputs"]["unet_name"] = MODELS["ace"]
    wf["109"]["inputs"]["value"] = rseed()
    wf["107"]["inputs"]["filename_prefix"] = f"pingpong/song_{tag}"
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
    wf["2"]["inputs"]["seed"] = rseed()
    wf["3"]["inputs"]["seed"] = rseed()
    wf["4"]["inputs"]["filename_prefix"] = f"pingpong/swap_{tag}"
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
    return "image", text

# ---------- 핸들러 ----------
def do_text(mode, body):
    if mode == "help" or not body:
        tg_send(HELP); return
    if mode == "klein":
        tg_send("🎭 Klein 인물합성은 사진을 첨부해서 보내주세요 (캡션에 원하는 장면).")
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
        wf, save = inject_ace(tags, lyrics); kind = "audio"
        tg_send(f"🎼▸ 작곡 중 ░▒▓█▓▒░ ♪♬\n{payload_desc}")
    elif mode == "video":
        wf, save = inject_ltx(prompt); kind = "video"
        tg_send(f"🎬▸ 필름 감는 중 📼 (수 분 소요) ░▒▓\n{prompt}")
    else:
        wf, save = inject_zit(prompt); kind = "photo"
        tg_send(f"🎨▸ 그리는 중 ░▒▓█▓▒░ ✧\n{prompt}")
    files = comfy_run(wf, save)
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
    tg_send_file("photo", files[0], caption=caption or "✦ 합성 완료 ✦")
    comfy_free()

def do_klein(file_id, caption):
    _klein_core(download_ref(file_id), caption)

def do_faceswap(char_rel, face_rel, goal):
    tg_send(f"🔀▸ 얼굴 교체 중 ░▒▓█▓▒░ 👤↔👤\n장면: {goal or '(원본 장면 유지)'}")
    comfy_free()
    wf, save = inject_klein_faceswap(char_rel, face_rel, goal)
    files = comfy_run(wf, save)
    tg_send_file("photo", files[0], caption="✦ 페이스 스왑 완료 ✦ 👤↔👤")
    comfy_free()

# 단계별 대화 상태 (단일 사용자 가정)
STATE = {"flow": None, "goal": "", "char_rel": None, "pending_photo": None, "pending_cap": ""}
def reset_state():
    STATE.update({"flow": None, "goal": "", "char_rel": None, "pending_photo": None, "pending_cap": ""})

SWAP_KW = ("/페이스스왑", "/faceswap", "/얼굴바꾸기", "/얼굴")

# 메뉴 버튼/숫자 → 모드
def menu_pick(text):
    t = text.strip()
    if t and t[0] in "12345" and len(t) <= 2:   # "1" ~ "5" (이모지 숫자 키 제외)
        return {"1": "image", "2": "video", "3": "song", "4": "klein", "5": "faceswap"}[t[0]]
    for kw, m in (("이미지", "image"), ("영상", "video"), ("음악", "song"),
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
        send_menu("🏓 핑퐁 봇\n" + HELP); return

    # 사진을 받고 '뭘 할지' 고르는 단계 (메뉴매칭보다 먼저 처리)
    if f == "photo_action":
        if "페이스스왑" in text:
            STATE["char_rel"] = download_ref(STATE["pending_photo"])
            STATE["flow"] = "swap_face"; STATE["pending_photo"] = None
            tg_send("✅ 이 사진을 '몸'으로 쓸게요. 이제 '얼굴' 담당 사진을 보내주세요 (2/2)"); return
        if ("인물합성" in text) or ("편집" in text):
            cap = STATE["pending_cap"]; fid = STATE["pending_photo"]
            if cap:
                reset_state(); do_klein(fid, cap); send_menu("✦･ﾟ: 완성! :ﾟ･✦  ヽ(•‿•)ノ  다음은?"); return
            STATE["flow"] = "await_klein_scene"
            tg_send("✏️ 어떤 장면/편집을 원하세요? 글로 보내주세요.\n(예: 눈 내리는 파리 거리에서)"); return
        tg_send("👆 위 버튼 중 하나를 눌러주세요. (취소: /취소)"); return

    # 인물합성 장면 입력 대기
    if f == "await_klein_scene":
        if not text:
            tg_send("✏️ 원하는 장면을 글로 보내주세요. (취소: /취소)"); return
        fid = STATE["pending_photo"]; reset_state()
        do_klein(fid, text); send_menu("✦･ﾟ: 완성! :ﾟ･✦  ヽ(•‿•)ノ  다음은?"); return

    # 메뉴 버튼/숫자 선택 (사진 없는 순수 텍스트)
    if not photo:
        picked = menu_pick(text)
        if picked == "help":
            send_menu("🏓 핑퐁 봇\n" + HELP); return
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
    import shutil
    lines = []
    try:
        requests.get(f"{COMFY}/system_stats", timeout=5); comfy_ok = True
    except Exception:
        comfy_ok = False
    if not comfy_ok:
        lines.append("❌ ComfyUI 연결 안 됨 → Comfy Desktop을 먼저 켜세요 (8188)")
    else:
        lines.append("✅ ComfyUI 연결됨")
        for feat, nodes, loader, field, key in FEATURE_CHECKS:
            missing = [n for n in nodes if not _node_info(n)]
            if missing:
                lines.append(f"⚠️ {feat}  ← 노드 없음: {', '.join(missing)}")
                continue
            exp = model_of(key)
            if not _has(exp, _model_list(loader, field)):
                lines.append(f"⚠️ {feat}  ← 모델 없음: {exp}  (config의 models.{key} 확인)")
            else:
                lines.append(f"✅ {feat}")
    lines.append("✅ LM Studio CLI" if shutil.which("lms") else
                 "⚠️ lms(LM Studio CLI) 없음 → 텍스트 자동 프롬프트 불가")
    return comfy_ok, lines

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
    print("  +--------------------[ 사전점검 ]--------------------+", flush=True)
    comfy_ok, lines = preflight()
    for ln in lines:
        print("   " + ln, flush=True)
    print("  +---------------------------------------------------+", flush=True)
    print("  >> INSERT COIN... 폰에서 봇에게 메시지를 보내세요 <<\n", flush=True)
    summary = (TG_BANNER + "\n✦ 가동 완료! ✦\n\n[ 점검 결과 ]\n" +
               "\n".join(lines) + "\n\n" + HELP)
    send_menu(summary)
    init = tg_updates(0)
    offset = (init[-1]["update_id"] + 1) if init else 0
    beat = 0
    while True:
        try:
            beat_alive()
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
