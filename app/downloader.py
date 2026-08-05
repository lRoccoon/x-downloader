import os
import shutil
import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadCancelled

from . import config, db

_executor = ThreadPoolExecutor(max_workers=3)
_aria2c = shutil.which("aria2c") is not None
_pause_flags: dict[str, bool] = {}  # task_id -> True 表示请求暂停
_task_tmpfiles: dict[str, str] = {}  # task_id -> 下载中的 .part 路径


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
        # 连接卡住时 15s 超时重试，避免永久阻塞；也保证暂停（靠 hook 检测）能及时生效
        "socket_timeout": 15,
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
    # X 的 http 直链（音视频合一）vcodec 为 None(未知)，不能按 vcodec 剔除；
    # 纯音频流 vcodec 为 "none" 且无 height，两个条件都能把它筛掉
    video_formats = [
        f for f in formats
        if f.get("vcodec") != "none" and f.get("height")
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
        # HLS 视频变体不含音轨(acodec=="none")，拼上最佳音频交给 ffmpeg 合并；
        # 万一没有独立音频流则回退纯视频
        format_id = f["format_id"]
        if f.get("acodec") == "none":
            format_id = f"{format_id}+bestaudio/{format_id}"
        resolutions.append({
            "format_id": format_id,
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
        if _pause_flags.get(task_id):
            raise DownloadCancelled("用户暂停")
        status = d.get("status")
        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            done = d.get("downloaded_bytes") or 0
            progress = (done / total * 100) if total else 0
            fields = dict(
                status="downloading",
                progress=round(progress, 1),
                speed=d.get("_speed_str", "").strip() or None,
                eta=d.get("_eta_str", "").strip() or None,
            )
            if total:
                fields["filesize"] = total  # 下载中显示估算总大小
            db.update_task(task_id, **fields)
        elif status == "finished":
            # 分片合并前的 finished，进度置满，文件名稍后由 download() 落库
            db.update_task(task_id, progress=100, speed=None, eta=None)
    return hook


def _aria2_total_length(tmpfile: str) -> int | None:
    """从 aria2 的控制文件读真实总大小（X 标称码率虚高，估算值可差数倍）。

    .aria2 布局(BE)：版本 2B、扩展 4B、infoHash 长度 4B(http 下载为 0)、
    piece 长度 4B、总长度 8B。格式对不上就放弃。
    """
    try:
        with open(tmpfile + ".aria2", "rb") as f:
            head = f.read(22)
        if len(head) == 22 and head[0:2] == b"\x00\x01" \
                and int.from_bytes(head[6:10], "big") == 0:
            return int.from_bytes(head[14:22], "big") or None
    except OSError:
        pass
    return None


def _watch_aria2c_progress(task_id: str, tmpfile: str, est_total: int | None) -> None:
    """aria2c 外部下载期间 yt-dlp 不回调进度，改为轮询 .part 文件落库。

    多连接分段写入下 st_size 几秒内即达文件末尾，改用 st_blocks（稀疏
    文件的真实写入量）计算进度与速度。
    """
    last_size, last_t = 0, time.time()
    total = None
    while _task_tmpfiles.get(task_id) == tmpfile and not _pause_flags.get(task_id):
        time.sleep(2)
        # sleep 期间任务可能已暂停/结束，写库前必须复查，且写入带状态守卫，
        # 避免最后一拍把 paused/finished 覆盖回 downloading
        if _task_tmpfiles.get(task_id) != tmpfile or _pause_flags.get(task_id):
            break
        try:
            st = os.stat(tmpfile)
        except OSError:
            continue
        size = st.st_blocks * 512
        total = total or _aria2_total_length(tmpfile) or est_total
        now = time.time()
        speed = (size - last_size) / max(now - last_t, 0.1)
        last_size, last_t = size, now
        fields = dict(
            speed=f"{speed / 1048576:.1f}MiB/s" if speed > 0 else None,
        )
        if total:
            fields["progress"] = round(min(size / total * 100, 99.9), 1)
            fields["filesize"] = total
            if speed > 0:
                remain = int(max(total - size, 0) / speed)
                fields["eta"] = f"{remain // 60:02d}:{remain % 60:02d}"
        db.update_task(task_id, only_if_status="downloading", **fields)


def _kill_task_aria2c(task_id: str) -> None:
    """终止某任务的 aria2c 子进程（外部下载器收不到暂停信号，只能杀进程）。

    aria2c 默认 -c 续传且留有 .aria2 控制文件，SIGTERM 后可精确断点续传。
    """
    tmpfile = _task_tmpfiles.get(task_id)
    if not tmpfile:
        return
    needle = os.path.basename(tmpfile).encode()
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            cmdline = open(f"/proc/{pid}/cmdline", "rb").read()
        except OSError:
            continue
        if b"aria2c" in cmdline and needle in cmdline:
            try:
                os.kill(int(pid), signal.SIGTERM)
            except OSError:
                pass


def _run_download(task_id: str, url: str, format_id: str) -> None:
    _pause_flags.pop(task_id, None)
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
            # 先解析（不下载）拿到目标文件名，供暂停杀进程与进度轮询定位 .part
            info = ydl.extract_info(url, download=False)
            if info.get("_type") == "playlist" and info.get("entries"):
                info = next(e for e in info["entries"] if e)
            tmpfile = ydl.prepare_filename(info) + ".part"
            _task_tmpfiles[task_id] = tmpfile
            protocol = info.get("protocol") or ""
            if _aria2c and protocol.startswith("http") and "m3u8" not in protocol:
                total = info.get("filesize") or info.get("filesize_approx")
                threading.Thread(
                    target=_watch_aria2c_progress,
                    args=(task_id, tmpfile, total),
                    daemon=True,
                ).start()

            info = ydl.extract_info(url, download=True)
            if info.get("_type") == "playlist" and info.get("entries"):
                info = next(e for e in info["entries"] if e)
            filepath = ydl.prepare_filename(info)
            # merge 后扩展名可能变为 mp4
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
    except DownloadCancelled:
        db.update_task(task_id, status="paused", speed=None, eta=None)
    except Exception as exc:  # noqa: BLE001
        if _pause_flags.get(task_id):
            # aria2c 被暂停杀掉后 yt-dlp 抛 DownloadError，按暂停处理
            db.update_task(task_id, status="paused", speed=None, eta=None)
        else:
            db.update_task(task_id, status="error", error=str(exc)[:500])
    finally:
        _task_tmpfiles.pop(task_id, None)


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


def pause(task_id: str) -> bool:
    """请求暂停一个下载中的任务（保留已下载部分，可续传）。

    HLS 分片下载靠 progress hook 检测 flag 生效；aria2c 外部下载全程无
    hook 回调，需要直接终止 aria2c 进程。状态先置 pausing 给前端即时反馈，
    真正落为 paused 由 _run_download 的异常分支完成。
    """
    task = db.get_task(task_id)
    if not task or task.get("status") != "downloading":
        return False
    _pause_flags[task_id] = True
    db.update_task(task_id, status="pausing", speed=None, eta=None)
    _kill_task_aria2c(task_id)
    return True


def resume(task_id: str) -> bool:
    """继续一个已暂停的任务，yt-dlp 会从已下载分片断点续传。"""
    task = db.get_task(task_id)
    if not task or task.get("status") != "paused":
        return False
    _pause_flags.pop(task_id, None)
    db.update_task(task_id, status="queued", error=None)
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
