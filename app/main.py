import os

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
)
from fastapi.staticfiles import StaticFiles

from . import auth, config, db, downloader

app = FastAPI(title="X Video Downloader")

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


def require_auth(request: Request):
    token = request.cookies.get(auth.COOKIE_NAME)
    if not auth.verify_token(token):
        raise HTTPException(status_code=401, detail="未登录")


# ---------- 页面 ----------

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    token = request.cookies.get(auth.COOKIE_NAME)
    if not auth.verify_token(token):
        return RedirectResponse("/login")
    with open(os.path.join(STATIC_DIR, "index.html"), encoding="utf-8") as f:
        return HTMLResponse(f.read())


@app.get("/login", response_class=HTMLResponse)
def login_page():
    if not auth.auth_enabled():
        return RedirectResponse("/")
    with open(os.path.join(STATIC_DIR, "login.html"), encoding="utf-8") as f:
        return HTMLResponse(f.read())


@app.post("/api/login")
def login(password: str = Form(...)):
    if not auth.check_password(password):
        raise HTTPException(status_code=401, detail="密码错误")
    resp = JSONResponse({"ok": True})
    resp.set_cookie(
        auth.COOKIE_NAME,
        auth.make_token(),
        max_age=config.SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
    )
    return resp


@app.post("/api/logout")
def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.COOKIE_NAME)
    return resp


# ---------- 业务 API ----------

@app.get("/api/config", dependencies=[Depends(require_auth)])
def get_config():
    return {
        "download_dir": str(config.DOWNLOAD_DIR),
        "proxy": config.PROXY or None,
        "threads": config.THREADS,
        "cookies_uploaded": config.COOKIES_FILE.exists(),
        "aria2c": downloader._aria2c,
        "ffmpeg": downloader._ffmpeg is not None,
    }


@app.post("/api/cookies", dependencies=[Depends(require_auth)])
async def upload_cookies(file: UploadFile = File(...)):
    content = await file.read()
    config.COOKIES_FILE.write_bytes(content)
    return {"ok": True}


@app.post("/api/cookies/token", dependencies=[Depends(require_auth)])
def set_cookies_token(payload: dict):
    auth_token = (payload.get("auth_token") or "").strip()
    ct0 = (payload.get("ct0") or "").strip()
    if not auth_token:
        raise HTTPException(status_code=400, detail="请填写 auth_token")
    try:
        downloader.write_cookies_from_tokens(auth_token, ct0)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


@app.post("/api/probe", dependencies=[Depends(require_auth)])
def probe(payload: dict):
    url = (payload.get("url") or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="请输入链接")
    try:
        return downloader.probe(url)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"解析失败: {exc}")


@app.post("/api/download", dependencies=[Depends(require_auth)])
def start_download(payload: dict):
    url = (payload.get("url") or "").strip()
    format_id = (payload.get("format_id") or "").strip()
    title = payload.get("title") or ""
    resolution = payload.get("resolution") or ""
    if not url or not format_id:
        raise HTTPException(status_code=400, detail="参数缺失")
    task_id = downloader.enqueue(url, title, format_id, resolution)
    return {"task_id": task_id}


@app.get("/api/tasks", dependencies=[Depends(require_auth)])
def tasks():
    return db.list_tasks()


@app.post("/api/tasks/clear", dependencies=[Depends(require_auth)])
def clear_tasks():
    db.clear_tasks()
    return {"ok": True}


@app.post("/api/tasks/{task_id}/retry", dependencies=[Depends(require_auth)])
def retry_task(task_id: str):
    if not downloader.retry(task_id):
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"ok": True}


@app.delete("/api/tasks/{task_id}", dependencies=[Depends(require_auth)])
def remove_task(task_id: str):
    db.delete_task(task_id)
    return {"ok": True}


@app.get("/api/tasks/{task_id}/file", dependencies=[Depends(require_auth)])
def download_file(task_id: str):
    task = db.get_task(task_id)
    if not task or task.get("status") != "finished" or not task.get("filepath"):
        raise HTTPException(status_code=404, detail="文件不存在")
    path = task["filepath"]
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="文件已被移动或删除")
    return FileResponse(path, filename=task.get("filename") or os.path.basename(path))


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
