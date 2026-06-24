# -*- coding: utf-8 -*-
"""Safe launcher for pingpong.py.

This wrapper keeps the original bot flow but patches two runtime behaviors:
- queue jobs are moved through running/ and failed/ instead of being deleted first
- the auto-started dashboard uses dashboard_safe.py
"""
import json
import os
import subprocess
import sys
import time
import traceback

import pingpong as app

RUNNING_DIR = os.path.join(app.HERE, "running")
FAILED_DIR = os.path.join(app.HERE, "failed")


def _ensure_job_dirs():
    os.makedirs(app.QUEUE_DIR, exist_ok=True)
    os.makedirs(RUNNING_DIR, exist_ok=True)
    os.makedirs(FAILED_DIR, exist_ok=True)


def _failed_name(fn):
    stem, ext = os.path.splitext(fn)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    return f"{stem}_{stamp}{ext or '.json'}"


def _preserve_failed_job(path, fn, job=None, err=None, tb=None):
    os.makedirs(FAILED_DIR, exist_ok=True)
    dest = os.path.join(FAILED_DIR, _failed_name(fn))
    payload = dict(job) if isinstance(job, dict) else {"source_file": fn}
    if err is not None:
        payload["error"] = str(err)
        payload["failed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    if tb:
        payload["traceback"] = tb[-4000:]
    try:
        with open(dest, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.write("\n")
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
    except Exception as move_err:
        app.log("failed to preserve failed job:", move_err)


def process_queue_safe():
    _ensure_job_dirs()
    try:
        files = sorted(f for f in os.listdir(app.QUEUE_DIR) if f.endswith(".json"))
    except FileNotFoundError:
        return

    for fn in files:
        queue_fp = os.path.join(app.QUEUE_DIR, fn)
        running_fp = os.path.join(RUNNING_DIR, fn)
        try:
            os.replace(queue_fp, running_fp)
        except FileNotFoundError:
            continue
        except Exception as e:
            app.log("queue move failed:", e)
            continue

        job = None
        try:
            with open(running_fp, encoding="utf-8") as f:
                job = json.load(f)
        except Exception as e:
            _preserve_failed_job(running_fp, fn, {"source_file": fn}, e, traceback.format_exc())
            continue

        try:
            app.log("queue job:", job.get("mode"))
            app.run_job(job)
        except Exception as e:
            app.tg_send(f"⚠️ 대시보드 작업 오류: {e}")
            tb = traceback.format_exc()
            app.log("job error:", tb)
            _preserve_failed_job(running_fp, fn, job, e, tb)
        else:
            try:
                os.remove(running_fp)
            except FileNotFoundError:
                pass
            except Exception as e:
                app.log("running job cleanup failed:", e)


def start_dashboard_safe():
    url = f"http://127.0.0.1:{app.DASHBOARD_PORT}/api/status"
    try:
        app.requests.get(url, timeout=1)
        app.log("dashboard already running")
        return
    except Exception:
        pass
    try:
        flags = 0
        if os.name == "nt":
            flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        subprocess.Popen(
            [sys.executable, os.path.join(app.HERE, "dashboard_safe.py")],
            cwd=app.HERE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
        app.log("dashboard started", f"http://127.0.0.1:{app.DASHBOARD_PORT}")
    except Exception as e:
        app.log("dashboard start fail:", e)


app.process_queue = process_queue_safe
app.start_dashboard = start_dashboard_safe


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "once":
        mode, body = app.detect_mode(sys.argv[2])
        app.do_text(mode, body)
    else:
        app.main()
