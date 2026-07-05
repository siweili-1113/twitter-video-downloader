# TweetMedia — Twitter/X 媒体批量下载器

一个基于 Python asyncio + httpx 的 Twitter/X 推文媒体资源批量下载工具，支持图片和视频的高速并发下载。

## 特性

- **图片 & 视频** — 支持原图（orig）/ JPG / PNG 三种图片格式，视频自动选取最高比特率
- **异步并发** — asyncio + httpx，独立控制 API 并发数与下载并发数，充分利用带宽
- **断点续传** — 已下载文件自动跳过，中断后重新运行不重复下载
- **Rate Limit 保护** — 自动检测 HTTP 429 并等待，等待期间不消耗重试配额
- **进度显示** — tqdm 双进度条（总体进度 + 单文件下载进度）
- **原子化写入** — 先写 `.part` 临时文件，完成后再重命名，避免中断导致文件损坏
- **自动去重** — 自动过滤重复推文链接
- **凭证保护** — 支持环境变量 `TWITTER_COOKIE`，配置文件已加入 `.gitignore`

## 环境要求

- Python 3.9+
- 可访问 Twitter/X 的网络环境（支持代理）

## 快速开始

```bash
# 1. 克隆仓库
git clone <your-repo-url>
cd twitter_download

# 2. 安装依赖
pip install -r requirements.txt

# 3. 首次运行会自动生成 config.json 模板
python main.py
```

首次运行后会生成 `config.json`，填入你的 Twitter Cookie 后再次运行即可。

## 配置指南

### 1. 获取 Twitter Cookie

在浏览器中登录 Twitter/X 后，按 `F12` 打开开发者工具：

1. 切换到 **Application**（应用程序）标签
2. 左侧找到 **Cookies** → `https://x.com`
3. 找到 `auth_token` 和 `ct0` 两个字段的值
4. 按以下格式填入 `config.json` 的 `cookie` 字段：

```
auth_token=你的auth_token值; ct0=你的ct0值;
```

> **安全提示**：Cookie 包含你的登录凭证，切勿分享给他人。`config.json` 已在 `.gitignore` 中，不会被提交到 Git。你也可以设置环境变量 `TWITTER_COOKIE` 来替代配置文件中的 cookie 字段。

### 2. config.json 配置说明

首次运行程序会自动创建 `config.json` 模板。以下是各配置项的说明：

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `cookie` | string | `""` | **必填**。Twitter Cookie 字符串（格式见上方） |
| `save_path` | string | `""` | 下载保存目录，留空则保存在当前目录 |
| `url_file` | string | `"links.txt"` | 推文链接列表文件名 |
| `image_format` | string | `"orig"` | 图片格式：`orig`（原图）/ `jpg` / `png` |
| `has_video` | bool | `true` | 是否下载视频 |
| `max_concurrent_requests` | int | `8` | 同时下载媒体的最大并发数 |
| `max_api_concurrent` | int | `3` | 同时调用 Twitter API 的最大并发数 |
| `api_delay` | float | `0.5` | 每次 API 调用前的等待间隔（秒） |
| `proxy` | string/null | `null` | 代理地址，如 `"http://127.0.0.1:7890"` |
| `bearer_token` | string | `""` | Bearer Token，留空使用内置默认值 |

### 3. 准备链接文件

在 `links.txt` 中每行填写一个推文链接，支持 `twitter.com` 和 `x.com` 域名：

```
https://twitter.com/username/status/1234567890
https://x.com/i/status/9876543210
https://twitter.com/another_user/status/1111111111
```

### 4. 开始下载

```bash
python main.py
```

输出示例：
```
Total links: 161, after dedup: 161
API concurrency: 3 | Download concurrency: 8 | API interval: 0.5s
Overall Progress:  35%|████████        | 56/161 [01:23<02:34, 1.47s/it]
Downloaded: ./username_1234567890_2025-01-15 12-30_img_0.jpg
```

### 5. 处理无媒体链接

如果推文没有图片或视频，其链接会自动写入 `no_media.txt`。你可以：

- 手动检查后删除
- 使用 `dedup.py` 对 `no_media.txt` 去重：

```bash
# 默认处理 no_media.txt
python dedup.py

# 指定文件
python dedup.py /path/to/your/file.txt
```

## 文件命名规则

下载的文件按以下格式命名：

```
{用户名}_{tweet_id}_{推文时间}_{媒体类型}_{序号}.{扩展名}
```

示例：
```
username_1234567890_2025-01-15_12-30_img_0.jpg
username_1234567890_2025-01-15_12-30_vid_0.mp4
```

## 工作原理

```
┌───────────────────────────────────────────┐
│ 1. 读取 links.txt → 正则提取 tweet_id → 去重 │
└───────────────┬───────────────────────────┘
                ▼
┌───────────────────────────────────────────┐
│ 2. API 信号量（max_api_concurrent=3）       │
│    调用 Twitter 内部 API 获取媒体信息        │
└───────────────┬───────────────────────────┘
                ▼
┌───────────────────────────────────────────┐
│ 3. 解析 extended_entities                  │
│    photo  → 原图 URL                       │
│    video  → 最高比特率 variant URL          │
└───────────────┬───────────────────────────┘
                ▼
┌───────────────────────────────────────────┐
│ 4. 下载信号量（max_concurrent_requests=8）  │
│    并发下载 → .part 临时文件 → 原子化重命名   │
└───────────────────────────────────────────┘
```

## 故障排查

| 现象 | 可能原因 | 解决方案 |
|------|---------|---------|
| HTTP 403 | Cookie 过期 | 重新获取 Cookie 并更新 `config.json` |
| HTTP 404 | 推文已删除 | 正常，脚本自动跳过 |
| HTTP 429 | 触发频率限制 | 脚本自动等待；可降低 `max_api_concurrent` |
| 无法提取 ct0 令牌 | Cookie 格式错误 | 确认包含 `auth_token=xxx; ct0=xxx;` |
| 下载速度慢 | 网络/代理问题 | 提高 `max_concurrent_requests`，检查代理 |
| 部分链接"无媒体内容" | 推文为纯文本 | 检查链接是否确实包含图片/视频 |

## 项目结构

```
tweet_downloader/
├── main.py              # 主程序（下载核心逻辑）
├── dedup.py             # 文本去重工具
├── requirements.txt     # Python 依赖
├── links.txt            # 推文链接列表
├── config.json          # 配置文件（Git 已忽略）
├── .gitignore           # Git 忽略规则
└── README.md            # 本文件
```

## 依赖

| 库 | 版本 | 用途 |
|----|------|------|
| [httpx](https://github.com/encode/httpx) | ≥0.24.0 | 异步 HTTP 客户端 |
| [tqdm](https://github.com/tqdm/tqdm) | ≥4.65.0 | 进度条 |

## License

MIT License

> 本项目仅供个人学习和研究使用，请遵守 Twitter/X 的使用条款和当地法律法规。