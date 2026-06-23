# -*- coding: utf-8 -*-
"""
너무바쁜베짱이 핑퐁 갤러리 대시보드 (로컬 서버)
- 생성 폴더를 실시간으로 읽어 이미지/영상/음악을 갤러리로 표시
- 생성 요청을 공유 큐(queue/)에 투입 → 봇(pingpong.py)이 순서대로 처리(GPU 충돌 방지)
- 숨김(복구 가능)/삭제(.trash 이동) 지원
실행: python dashboard.py  (또는 대시보드.bat)
"""
import os, sys, json, time, base64, re, mimetypes, threading, webbrowser
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))
def _auto(k, *parts):
    base = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Comfy-Desktop", "ComfyUI-Shared")
    return CFG.get(k) or os.path.join(base, *parts)
OUTDIR   = _auto("comfy_output_dir", "output")
INPUTDIR = _auto("comfy_input_dir", "input")
COMFY    = CFG.get("comfy_api", "http://127.0.0.1:8188").rstrip("/")
GALLERY  = os.path.join(OUTDIR, "pingpong")
TRASH    = os.path.join(GALLERY, ".trash")
ALIVE    = os.path.join(GALLERY, ".alive")
QUEUE    = os.path.join(HERE, "queue")
HIDDEN   = os.path.join(HERE, "dashboard_hidden.json")
PORT     = int(CFG.get("dashboard_port", 8910))

IMG_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
VID_EXT = {".mp4", ".webm", ".mov", ".mkv"}
AUD_EXT = {".mp3", ".flac", ".wav", ".ogg", ".m4a"}

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

def scan():
    hidden = load_hidden()
    out = {"images": [], "videos": [], "audios": [], "hiddenCount": 0}
    if not os.path.isdir(GALLERY):
        return out
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
            out[kind].append({"name": fn, "rel": rel,
                              "url": "/media/" + urllib.parse.quote(rel), "mtime": mt})
    for k in ("images", "videos", "audios"):
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
    return {"alive": alive, "generating": gen, "queued": queued}

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

def safe_path(rel):
    full = os.path.normpath(os.path.join(GALLERY, rel))
    if not full.startswith(os.path.normpath(GALLERY)):
        return None
    return full


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
        p = urllib.parse.urlparse(self.path).path
        if p == "/" or p == "/index.html":
            b = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
        elif p == "/api/list":
            self._json(scan())
        elif p == "/api/status":
            self._json(status())
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
            if mode in ("klein", "faceswap"):
                rels = [save_dataurl(d, i) for i, d in enumerate(imgs)]
                rels = [r for r in rels if r]
                if mode == "klein":
                    if not rels:
                        return self._json({"ok": False, "err": "사진 필요"}, 400)
                    enqueue({"mode": "klein", "char_rel": rels[0], "text": text})
                else:
                    if len(rels) < 2:
                        return self._json({"ok": False, "err": "사진 2장 필요"}, 400)
                    enqueue({"mode": "faceswap", "char_rel": rels[0], "face_rel": rels[1], "text": text})
            else:
                enqueue({"mode": mode, "text": text})
            return self._json({"ok": True})
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
                try: os.replace(full, dest)
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
                except (BrokenPipeError, ConnectionResetError):
                    break
                remain -= len(chunk)


PAGE = r'''<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>너무바쁜베짱이 · 핑퐁 갤러리</title>
<link href="https://fonts.googleapis.com/css2?family=Press+Start+2P&family=VT323&display=swap" rel="stylesheet">
<style>
:root{--ink:#ece9ff;--mut:#a79fd6;--pink:#ff5d8f;--cyan:#56e1ff;--pur:#9d7bff;--grn:#8dffb0;--amb:#ffd166;--b1:#171228;--b2:#241b3e;--ln:rgba(255,255,255,.12)}
*{box-sizing:border-box}
body{margin:0;background:#0a0814;color:var(--ink);font-family:'VT323',monospace;font-size:18px;padding:18px}
.pix{font-family:'Press Start 2P',monospace}
.wrap{max-width:1100px;margin:0 auto}
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
.shake{animation:sh .3s}@keyframes sh{0%,100%{transform:translateX(0)}25%{transform:translateX(-5px)}75%{transform:translateX(5px)}}
.mon{display:flex;gap:14px;margin-bottom:18px}
.crt{flex:1.7;background:#070611;border:7px solid #2c2350;border-radius:16px;padding:10px}
.crt video{width:100%;aspect-ratio:16/10;border-radius:8px;background:#000;display:block}
.now{font-size:16px;color:var(--amb);margin-top:8px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.vlist{flex:1;display:flex;flex-direction:column;gap:6px}
.vhead{display:flex;justify-content:space-between;align-items:center;font-size:10px;color:var(--mut)}
.vrow{display:flex;align-items:center;gap:6px;background:var(--b1);border:1px solid var(--ln);border-radius:8px;padding:7px 9px;font-size:16px}
.vrow .nm{flex:1;cursor:pointer;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.vrow:hover .nm{color:var(--cyan)}.vrow .tg{font-size:9px;color:var(--pink)}
.lab{font-size:11px;color:var(--mut);letter-spacing:1px;display:flex;justify-content:space-between;align-items:center;margin:8px 0 10px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px}
.card{position:relative;border-radius:10px;overflow:hidden;border:2px solid #2a2150;cursor:pointer;transition:transform .15s,border-color .15s;aspect-ratio:3/4}
.card:hover{transform:scale(1.06);border-color:var(--pink);z-index:2}
.card img{width:100%;height:100%;object-fit:cover;display:block;image-rendering:pixelated}
.card .cap{position:absolute;left:0;right:0;bottom:0;font-size:14px;padding:3px 7px;background:rgba(10,8,20,.78);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tools{position:absolute;top:5px;right:5px;display:none;gap:5px}.card:hover .tools{display:flex}
.tbtn{width:26px;height:26px;border:none;border-radius:6px;background:rgba(13,11,22,.85);color:#fff;cursor:pointer;font-family:'VT323';font-size:14px}.tbtn:hover{background:var(--cyan);color:#0a0814}.tbtn.del:hover{background:var(--amb);color:#0a0814}
.chip{font-size:15px;color:var(--pur);background:var(--b2);border:1px solid var(--pur);border-radius:5px;padding:2px 8px;cursor:pointer}.chip:hover{background:var(--pur);color:#0a0814}.chip.off{display:none}
.empty{color:var(--mut);font-size:18px;padding:30px;text-align:center;border:1px dashed var(--ln);border-radius:10px}
.player{display:flex;align-items:center;gap:10px;margin-top:18px;background:var(--b1);border:1px solid var(--ln);border-radius:10px;padding:10px 14px;position:sticky;bottom:10px}
.pbtn{background:var(--b2);border:1px solid var(--ln);color:var(--ink);border-radius:6px;width:34px;height:34px;cursor:pointer;font-size:16px}.pbtn:hover{color:var(--cyan);border-color:var(--cyan)}
.ptrack{flex:1;font-size:17px;color:var(--amb);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.lb{display:none;position:fixed;inset:0;background:rgba(6,5,14,.94);align-items:center;justify-content:center;z-index:50}.lb.open{display:flex}
.lb img{max-height:84vh;max-width:74vw;border-radius:8px;border:2px solid var(--pink);image-rendering:pixelated}
.nav{background:var(--b2);border:1px solid var(--ln);color:var(--ink);width:46px;height:70px;border-radius:8px;font-size:26px;cursor:pointer;margin:0 12px}.nav:hover{color:var(--pink);border-color:var(--pink)}
.lbtools{position:fixed;top:20px;right:24px;display:flex;gap:8px}.lbtools button{background:rgba(13,11,22,.7);border:1px solid var(--ln);color:var(--ink);border-radius:6px;width:36px;height:36px;cursor:pointer;font-size:15px}.lbtools button:hover{border-color:var(--pink);color:var(--pink)}
.lbcap{position:fixed;bottom:22px;left:0;right:0;text-align:center;font-size:18px;color:var(--amb)}
</style></head><body><div class="wrap">
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
</div>
<div class="mon">
  <div class="crt"><video id="mon" controls playsinline></video><div class="now pix" id="now">NOW PLAYING — 없음</div></div>
  <div class="vlist"><div class="vhead pix"><span>▶ VIDEOS</span></div><div id="vrows" style="display:flex;flex-direction:column;gap:6px"></div></div>
</div>
<div class="lab pix"><span>■ GENERATED IMAGES <span class="chip off" id="hid" onclick="unhideAll()"></span></span><span id="upinfo" style="color:var(--mut)"></span></div>
<div class="grid" id="grid"></div>
<div class="player">
  <button class="pbtn" onclick="atrk(-1)">⏮</button><button class="pbtn" id="pp" onclick="aplay()">▶</button><button class="pbtn" onclick="atrk(1)">⏭</button>
  <div class="ptrack pix" id="ptrack">BGM — 음악 없음</div>
  <audio id="aud"></audio>
</div></div>
<div class="lb" id="lb"><div class="lbtools"><button onclick="lbHide()">👁</button><button onclick="lbDel()">🗑</button><button onclick="closeLb()">✕</button></div><button class="nav" onclick="step(-1)">‹</button><img id="lbimg"><button class="nav" onclick="step(1)">›</button><div class="lbcap pix" id="lbcap"></div></div>
<script>
var IMGS=[],VIDS=[],AUDS=[],ai=0,cur=0;
function api(p,b){return fetch(p,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)})}
function el(t,c){var e=document.createElement(t);if(c)e.className=c;return e}

function load(){fetch('/api/list').then(function(r){return r.json()}).then(function(d){
  IMGS=d.images;VIDS=d.videos;AUDS=d.audios;renderImgs();renderVids();renderAuds();
  var h=document.getElementById('hid');h.textContent='↺ 숨김 복구 '+d.hiddenCount;h.className='chip'+(d.hiddenCount?'':' off');
})}
function renderImgs(){var g=document.getElementById('grid');g.innerHTML='';
  if(!IMGS.length){g.innerHTML='<div class="empty">아직 생성된 이미지가 없어요. 위에서 생성해보세요!</div>';return}
  IMGS.forEach(function(it,i){var c=el('div','card');
    c.innerHTML='<img loading="lazy" src="'+it.url+'"><div class="cap">'+it.name+'</div><div class="tools"><button class="tbtn">👁</button><button class="tbtn del">🗑</button></div>';
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
function renderAuds(){if(AUDS.length){setTrack(Math.min(ai,AUDS.length-1))}else{document.getElementById('ptrack').textContent='BGM — 음악 없음'}}

function setTrack(i){if(!AUDS.length)return;ai=(i+AUDS.length)%AUDS.length;var a=document.getElementById('aud');a.src=AUDS[ai].url;document.getElementById('ptrack').textContent='BGM ♪ '+AUDS[ai].name}
function aplay(){var a=document.getElementById('aud');if(!AUDS.length)return;if(a.paused){if(!a.src)setTrack(0);a.play();document.getElementById('pp').textContent='⏸'}else{a.pause();document.getElementById('pp').textContent='▶'}}
function atrk(d){setTrack(ai+d);var a=document.getElementById('aud');a.play();document.getElementById('pp').textContent='⏸'}
document.getElementById('aud').addEventListener('ended',function(){atrk(1)});

function openLb(i){cur=i;showLb();document.getElementById('lb').classList.add('open')}
function showLb(){if(!IMGS.length){closeLb();return}cur=(cur+IMGS.length)%IMGS.length;document.getElementById('lbimg').src=IMGS[cur].url;document.getElementById('lbcap').textContent=IMGS[cur].name}
function step(d){cur+=d;showLb()}
function closeLb(){document.getElementById('lb').classList.remove('open')}
function lbHide(){var it=IMGS[cur];if(it)api('/api/hide',{rel:it.rel}).then(function(){IMGS.splice(cur,1);renderImgs();IMGS.length?showLb():closeLb();updHidQuick()})}
function lbDel(){var it=IMGS[cur];if(it&&confirm('삭제할까요?'))api('/api/delete',{rel:it.rel}).then(function(){IMGS.splice(cur,1);renderImgs();IMGS.length?showLb():closeLb()})}
function updHidQuick(){load()}
function unhideAll(){api('/api/unhide_all',{}).then(load)}
document.addEventListener('keydown',function(e){if(!document.getElementById('lb').classList.contains('open'))return;if(e.key==='ArrowRight')step(1);if(e.key==='ArrowLeft')step(-1);if(e.key==='Escape')closeLb()});

function modeChg(){var m=document.getElementById('mode').value,p=document.getElementById('prompt'),u=document.getElementById('up');
  var ph={image:'무엇을 그릴까요? 예: 노을 지는 바닷가',video:'어떤 영상? 대사는 "따옴표"',song:'어떤 음악? 예: 신나는 EDM',klein:'바꿀 장면 설명 (+ 사진 1장)',faceswap:'장면(선택) + 사진 2장(몸→얼굴)'};
  p.placeholder=ph[m];u.style.display=(m==='klein'||m==='faceswap')?'block':'none';document.getElementById('upinfo').textContent=''}
var picked=[];
function filePick(){var fs=document.getElementById('files').files;picked=[];var done=0;
  if(!fs.length){document.getElementById('upinfo').textContent='';return}
  for(var i=0;i<fs.length;i++){(function(){var fr=new FileReader();fr.onload=function(){picked.push(fr.result);done++;if(done===fs.length)document.getElementById('upinfo').textContent='사진 '+picked.length+'장 첨부됨'};fr.readAsDataURL(fs[i])})()}}
function gen(){var m=document.getElementById('mode').value,p=document.getElementById('prompt'),t=p.value.trim();
  if((m==='image'||m==='video'||m==='song')&&!t){p.classList.add('shake');setTimeout(function(){p.classList.remove('shake')},300);return}
  if(m==='klein'&&picked.length<1){alert('사진 1장을 첨부하세요');return}
  if(m==='faceswap'&&picked.length<2){alert('사진 2장(몸→얼굴)을 첨부하세요');return}
  api('/api/generate',{mode:m,text:t,images:picked}).then(function(r){return r.json()}).then(function(j){
    if(!j.ok){alert(j.err||'실패');return}
    p.value='';picked=[];document.getElementById('files').value='';document.getElementById('upinfo').textContent='큐에 추가됨 ✓ 봇이 곧 처리해요';
  })}
document.getElementById('prompt').addEventListener('keydown',function(e){if(e.key==='Enter')gen()});

function poll(){fetch('/api/status').then(function(r){return r.json()}).then(function(s){
  var hb=document.getElementById('hbox'),dot=document.getElementById('dot'),st=document.getElementById('hstate');
  hb.firstChild&&hb.firstChild.classList&&hb.firstChild.classList.toggle('on',s.alive);
  dot.className='dot'+(s.alive?' a':'');
  st.textContent=!s.alive?'OFFLINE':(s.generating||s.queued?'GENERATING'+(s.queued?(' ('+s.queued+')'):''):'ONLINE')
})}
var rows=["0110110","1111111","1111111","0111110","0011100","0001000"],hs='';
for(var y=0;y<6;y++)for(var x=0;x<7;x++)if(rows[y][x]==='1')hs+='<rect x='+(x*6.5)+' y='+(y*6.5)+' width=6.5 height=6.5 fill="#ff5d8f"/>';
document.getElementById('hbox').innerHTML='<svg class="heart on" viewBox="0 0 46 40">'+hs+'</svg>';
load();poll();setInterval(load,5000);setInterval(poll,2000);
</script></body></html>'''


def main():
    os.makedirs(QUEUE, exist_ok=True)
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
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
