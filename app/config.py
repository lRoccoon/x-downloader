import os
from pathlib import Path


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


# 视频默认保存目录（建议挂载为 docker volume）
DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", "/downloads"))

# 应用数据目录：存放 cookies.txt 和任务数据库
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))

# HTTP 代理，例如 http://host:7890 ；留空表示直连
PROXY = (os.getenv("PROXY") or os.getenv("HTTP_PROXY") or "").strip()

# 下载并发线程数（HLS 分片并发 / aria2c 连接数），默认 16
THREADS = _int("THREADS", 16)

# 访问页面所需的密码；留空表示不启用密码保护
APP_PASSWORD = os.getenv("APP_PASSWORD", "").strip()

# 用于给会话 cookie 签名的密钥；生产环境务必修改
SECRET_KEY = os.getenv("SECRET_KEY", "please-change-this-secret-key")

# 会话有效期（秒），默认 7 天
SESSION_MAX_AGE = _int("SESSION_MAX_AGE", 7 * 24 * 3600)

COOKIES_FILE = DATA_DIR / "cookies.txt"
DB_FILE = DATA_DIR / "tasks.db"

DATA_DIR.mkdir(parents=True, exist_ok=True)
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
