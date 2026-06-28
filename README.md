# 🎬 X (Twitter) 视频下载器

一个可通过 Docker 部署的小工具：粘贴 X 分享链接 → 自动检测视频、列出可用分辨率 → 一键下载，支持多线程、HTTP 代理、cookies 认证和密码保护。

## 功能

- 🔗 输入 X (Twitter) 分享链接，自动判断是否含视频，显示标题、封面与可下载分辨率
- ⚡ 多线程下载（默认 16，基于 aria2c / yt-dlp 分片并发）
- 📁 视频保存到可配置的服务器目录，也可从页面下载到本地浏览器
- 🌐 支持 HTTP 代理下载
- 🍪 支持上传浏览器导出的 `cookies.txt`，访问需要登录态的视频
- 📋 下载任务列表，实时显示进度 / 速度 / 状态
- 🔒 简单密码保护

## 快速开始

```bash
cd ~/Code/x-downloader
cp .env.example .env        # 修改密码、代理、线程数等
docker compose up -d --build
```

打开 http://localhost:8000 ，输入密码（默认 `changeme`，请在 `.env` 中修改）即可使用。

视频会保存到项目下的 `./downloads` 目录。

## 配置项（环境变量）

| 变量 | 说明 | 默认 |
| --- | --- | --- |
| `APP_PASSWORD` | 页面访问密码，留空则不启用 | `changeme` |
| `SECRET_KEY` | 会话签名密钥，请改成随机串 | — |
| `THREADS` | 下载并发线程数 | `16` |
| `PROXY` | HTTP 代理，如 `http://host:7890` | 空（直连） |
| `DOWNLOAD_DIR` | 容器内保存目录 | `/downloads` |
| `DATA_DIR` | cookies/数据库目录 | `/data` |

## 关于 cookies

X 现在大部分视频需要登录态才能解析。请用浏览器扩展（如 *Get cookies.txt LOCALLY*）导出 **Netscape 格式** 的 `cookies.txt`，
在页面「⚙️ 设置」中上传即可。cookies 会持久化保存在 `./data/cookies.txt`。

## 本地开发（不用 Docker）

```bash
pip install -r requirements.txt
# 需要本地安装 ffmpeg 和 aria2
DOWNLOAD_DIR=./downloads DATA_DIR=./data APP_PASSWORD=test \
  uvicorn app.main:app --reload
```

## 技术栈

FastAPI · yt-dlp · aria2c · ffmpeg · SQLite · 原生 HTML/JS
