# -*- coding: utf-8 -*-
"""핑퐁 봇 설정 마법사 — 텔레그램 토큰/chat_id/경로/모델을 자동으로 잡아 config.json 작성."""
import os, sys, json, time, subprocess, shutil
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(HERE, "config.json")

def get(url):
    with urllib.request.urlopen(url, timeout=20) as r:
        return json.load(r)

def auto_comfy_dirs():
    base = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Comfy-Desktop", "ComfyUI-Shared")
    return os.path.join(base, "output"), os.path.join(base, "input")

def find_comfy_outputs():
    """설치돼 있을 법한 ComfyUI output 폴더들을 빠르게 탐색(존재하는 것만)."""
    import string
    found, seen = [], set()
    def add(p):
        p = os.path.normpath(p)
        if p.lower() not in seen and os.path.isdir(p):
            seen.add(p.lower()); found.append(p)
    la = os.environ.get("LOCALAPPDATA", "")
    if la:
        add(os.path.join(la, "Comfy-Desktop", "ComfyUI-Shared", "output"))
    rels = [r"ComfyUI_windows_portable\ComfyUI\output",
            r"ComfyUI\output", r"ComfyUI\ComfyUI\output",
            r"AI\ComfyUI_windows_portable\ComfyUI\output", r"comfyui\output"]
    for d in string.ascii_uppercase:
        root = f"{d}:\\"
        if not os.path.exists(root):
            continue
        for sub in ("", "AI", "tools", "apps", "Programs", "Downloads", "Desktop"):
            base = os.path.join(root, sub) if sub else root
            for rel in rels:
                add(os.path.join(base, rel))
    return found

def input_dir_for(out_dir):
    """output 폴더 기준으로 같은 위치의 input 폴더 추정."""
    return os.path.join(os.path.dirname(out_dir.rstrip("\\/")), "input")

def list_lms_models():
    if not shutil.which("lms"):
        return []
    try:
        out = subprocess.run(["lms", "ls"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace").stdout
    except Exception:
        return []
    models, in_llm = [], False
    for line in out.splitlines():
        s = line.strip()
        if s.startswith("LLM"):
            in_llm = True; continue
        if s.startswith("EMBEDDING"):
            break
        if in_llm and s:
            tok = s.split()[0]
            if "/" in tok or "-" in tok or tok.isalnum():
                models.append(tok)
    return models

def main():
    print("  *==========================================*")
    print("  *   🦗 너무바쁜베짱이 STUDIO 🦗            *")
    print("  *   P I N G - P O N G   설정 마법사         *")
    print("  *   ░▒▓ made by 코다 & 크룩스 ▓▒░          *")
    print("  *==========================================*")

    # 1) ComfyUI 선택 (여러 개일 수 있으니 고르게)
    print("\n[1] ComfyUI 선택")
    cands = find_comfy_outputs()
    if cands:
        print("   찾은 ComfyUI output 폴더:")
        for i, c in enumerate(cands, 1):
            print(f"   {i}) {c}")
        print(f"   {len(cands)+1}) 직접 경로 입력")
        sel = input(f"   번호 선택 (1-{len(cands)+1}, 엔터=1): ").strip() or "1"
        try:
            idx = int(sel)
        except Exception:
            idx = 1
        if 1 <= idx <= len(cands):
            out_dir = cands[idx-1]
        else:
            out_dir = input("   output 폴더 경로를 붙여넣으세요: ").strip().strip('"')
    else:
        print("   ⚠️ 자동으로 못 찾았어요.")
        manual = input("   ComfyUI output 폴더 경로를 붙여넣으세요 (없으면 엔터): ").strip().strip('"')
        out_dir = manual or auto_comfy_dirs()[0]
    in_dir = input_dir_for(out_dir)
    print("   ✅ output:", out_dir)
    print("      input :", in_dir)
    if not os.path.isdir(out_dir):
        print("   ⚠️ 그 폴더가 아직 없네요. (진행은 가능, config.json에서 나중에 수정 가능)")

    # 1-2) ComfyUI 포트 (여러 서버를 돌리는 경우)
    port = input("\n   ComfyUI 포트 (엔터=8188): ").strip() or "8188"
    comfy_api = f"http://127.0.0.1:{port}"
    try:
        get(f"{comfy_api}/system_stats")
        print(f"   ✅ ComfyUI 응답 확인 ({comfy_api})")
    except Exception:
        print(f"   ⚠️ {comfy_api} 응답 없음 (꺼져있을 수 있음 — 나중에 켜면 됩니다)")

    # 2) 텔레그램 토큰
    print("\n[2] 텔레그램 봇 토큰")
    print("   @BotFather 에서 /newbot 으로 만든 토큰을 붙여넣으세요.")
    token = input("   토큰: ").strip()
    try:
        me = get(f"https://api.telegram.org/bot{token}/getMe")
        if not me.get("ok"):
            print("   ❌ 토큰이 올바르지 않아요."); return
        bot_name = me["result"]["username"]
        print(f"   ✅ 봇 확인: @{bot_name}")
    except Exception as e:
        print("   ❌ 토큰 확인 실패:", e); return

    # 3) chat_id 자동 추출
    print(f"\n[3] chat_id 잡기 — 지금 텔레그램에서 @{bot_name} 에게")
    print("   아무 메시지나 한 번 보내세요 (예: 안녕). 기다리는 중...")
    chat_id = None
    for _ in range(60):  # 최대 ~2분
        try:
            r = get(f"https://api.telegram.org/bot{token}/getUpdates?offset=-1")
            res = r.get("result", [])
            if res:
                msg = res[-1].get("message") or {}
                cid = msg.get("chat", {}).get("id")
                if cid:
                    chat_id = cid
                    print(f"   ✅ chat_id = {chat_id} ({msg.get('chat',{}).get('first_name','')})")
                    break
        except Exception:
            pass
        time.sleep(2)
    if not chat_id:
        print("   ❌ 메시지를 못 받았어요. 봇에게 메시지 보낸 뒤 다시 실행해주세요."); return

    # 4) 로컬 LLM 선택
    print("\n[4] 프롬프트 작성용 로컬 LLM 선택 (LM Studio)")
    models = list_lms_models()
    model = ""
    if models:
        for i, m in enumerate(models, 1):
            print(f"   {i}) {m}")
        sel = input(f"   번호 선택 (1-{len(models)}, 엔터=1): ").strip() or "1"
        try:
            model = models[int(sel) - 1]
        except Exception:
            model = models[0]
        print("   ✅ 선택:", model)
    else:
        print("   ⚠️ LM Studio 모델을 못 찾았어요(lms 미설치이거나 모델 없음).")
        model = input("   사용할 모델 식별자를 직접 입력(없으면 엔터): ").strip()

    # 5) config.json 작성
    cfg = {
        "telegram_token": token,
        "telegram_chat_id": chat_id,
        "llm_model": model,
        "comfy_api": comfy_api,
        "lmstudio_api": "http://127.0.0.1:1234",
        "comfy_output_dir": out_dir,
        "comfy_input_dir": in_dir,
        "send_upscaled": True,
        "zit_lora": False,
        "video_width": 0,
        "models": {
            "zit": "ZIT\\zImageTurbo_turbo.safetensors",
            "ltx_gguf": "LTX23\\ltx23DEVGGUFUnsloth_q4km.gguf",
            "klein": "FLUX2\\flux-2-klein-9b-kv-fp8.safetensors",
            "ace": "aceStepAudioGen_v15XLTurbo.safetensors",
        },
    }
    with open(CFG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    print("\n" + "=" * 54)
    print("  ✅ 설정 완료! config.json 저장됨")
    print("  이제 '핑퐁시작.bat' 으로 봇을 켜세요.")
    print("=" * 54)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n취소됨.")
    input("\n엔터를 누르면 닫혀요...")
