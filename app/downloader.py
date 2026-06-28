import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from yt_dlp import YoutubeDL

from . import config, db

_executor = ThreadPoolExecutor(max_workers=3)
_aria2c = shutil.which("aria2c") is not None


def _find_ffmpeg():
    """优先用系统 ffmpeg；没有则回退到 pip 的 imageio-ffmpeg 自带二进制。"""
    path = shutil.which("ffmpeg")
    if path:
        return path
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


_ffmpeg = _find_ffmpeg()


def _base_opts() -> dict:
    """yt-dlp 通用选项：代理 + cookies。"""
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
    }
    if config.PROXY:
        opts["proxy"] = config.PROXY
    if config.COOKIES_FILE.exists():
        opts["cookiefile"] = str(config.COOKIES_FILE)
    return opts


def probe(url: str) -> dict:
    """解析链接，返回标题、缩略图和可下载的分辨率列表。"""
    opts = _base_opts()
    opts["skip_download"] = True
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    # 推文里可能含多个视频，取第一个 entry
    if info.get("_type") == "playlist" and info.get("entries"):
        entries = [e for e in info["entries"] if e]
        if not entries:
            raise ValueError("该链接下未找到视频")
        info = entries[0]

    formats = info.get("formats") or []
    video_formats = [
        f for f in formats
        if f.get("vcodec") not in (None, "none") and f.get("height")
    ]
    if not video_formats:
        raise ValueError("该推文中没有可下载的视频")

    # 按分辨率(height)聚合，同分辨率保留码率最高的；优先 http(s) 直链(可多线程)
    def rank(f):
        proto = f.get("protocol") or ""
        is_http = proto.startswith("http") and "m3u8" not in proto
        return (is_http, f.get("tbr") or 0)

    best_by_height: dict[int, dict] = {}
    for f in video_formats:
        h = f["height"]
        if h not in best_by_height or rank(f) > rank(best_by_height[h]):
            best_by_height[h] = f

    resolutions = []
    for h in sorted(best_by_height, reverse=True):
        f = best_by_height[h]
        size = f.get("filesize") or f.get("filesize_approx")
        resolutions.append({
            "format_id": f["format_id"],
            "height": h,
            "width": f.get("width"),
            "ext": f.get("ext", "mp4"),
            "tbr": round(f["tbr"]) if f.get("tbr") else None,
            "filesize": size,
            "label": f"{f.get('width') or '?'}x{h}",
        })

    return {
        "title": info.get("title") or info.get("id") or "未命名",
        "uploader": info.get("uploader") or info.get("uploader_id"),
        "thumbnail": info.get("thumbnail"),
        "duration": info.get("duration"),
        "resolutions": resolutions,
    }


def _make_hook(task_id: str):
    def hook(d):
        status = d.get("status")
        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            done = d.get("downloaded_bytes") or 0
            progress = (done / total * 100) if total else 0
            db.update_task(
                task_id,
                status="downloading",
                progress=round(progress, 1),
                speed=d.get("_speed_str", "").strip() or None,
                eta=d.get("_eta_str", "").strip() or None,
            )
        elif status == "finished":
            # 分片合并前的 finished，进度置满，文件名稍后由 download() 落库
            db.update_task(task_id, progress=100, speed=None, eta=None)
    return hook


def _run_download(task_id: str, url: str, format_id: str) -> None:
    db.update_task(task_id, status="downloading", progress=0, error=None)
    opts = _base_opts()
    opts.update({
        "format": format_id,
        # 标题按字节截断（中文每字 3 字节），避免文件名超过 255 字节上限
        "outtmpl": str(config.DOWNLOAD_DIR / "%(title).100B [%(id)s] %(height)sp.%(ext)s"),
        "concurrent_fragment_downloads": config.THREADS,
        "progress_hooks": [_make_hook(task_id)],
        "merge_output_format": "mp4",
    })
    if _ffmpeg:
        opts["ffmpeg_location"] = _ffmpeg
    if _aria2c:
        opts["external_downloader"] = {"http": "aria2c", "https": "aria2c"}
        opts["external_downloader_args"] = {
            "aria2c": ["-x", str(config.THREADS), "-s", str(config.THREADS),
                       "-k", "1M", "--file-allocation=none"]
        }
    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if info.get("_type") == "playlist" and info.get("entries"):
                info = next(e for e in info["entries"] if e)
            filepath = ydl.prepare_filename(info)
            # merge 后扩展名可能变为 mp4
            import os
            if not os.path.exists(filepath):
                base, _ = os.path.splitext(filepath)
                if os.path.exists(base + ".mp4"):
                    filepath = base + ".mp4"
            filesize = os.path.getsize(filepath) if os.path.exists(filepath) else None
            db.update_task(
                task_id,
                status="finished",
                progress=100,
                filename=os.path.basename(filepath),
                filepath=filepath,
                filesize=filesize,
                speed=None,
                eta=None,
            )
    except Exception as exc:  # noqa: BLE001
        db.update_task(task_id, status="error", error=str(exc)[:500])


def enqueue(url: str, title: str, format_id: str, resolution: str) -> str:
    task_id = db.create_task(url, title, format_id, resolution)
    _executor.submit(_run_download, task_id, url, format_id)
    return task_id


def retry(task_id: str) -> bool:
    """重置一个失败/已存在的任务并重新下载。"""
    task = db.get_task(task_id)
    if not task:
        return False
    db.update_task(task_id, status="queued", progress=0, error=None, speed=None, eta=None)
    _executor.submit(_run_download, task_id, task["url"], task["format_id"])
    return True


def write_cookies_from_tokens(auth_token: str, ct0: str = "") -> None:
    """根据浏览器里的 auth_token / ct0 生成 Netscape 格式 cookies.txt。"""
    auth_token = auth_token.strip()
    ct0 = ct0.strip()
    if not auth_token:
        raise ValueError("auth_token 不能为空")
    expiry = int(time.time()) + 2 * 365 * 24 * 3600  # 约 2 年后过期
    pairs = [("auth_token", auth_token)]
    if ct0:
        pairs.append(("ct0", ct0))
    lines = ["# Netscape HTTP Cookie File", ""]
    for domain in (".x.com", ".twitter.com"):  # 两个域都写，兼容性更好
        for name, value in pairs:
            lines.append("\t".join([domain, "TRUE", "/", "TRUE", str(expiry), name, value]))
    config.COOKIES_FILE.write_text("\n".join(lines) + "\n")
