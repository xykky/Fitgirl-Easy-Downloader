# Fitgirl-Easy-Downloader

从 fitgirl-repacks.site 获取游戏页面上的全部 FuckingFast 镜像链接，并通过真实浏览器完成 Cloudflare 校验、换取直链，最后交给内置下载器或 aria2 批量下载。

整个流程分两步，对应两个脚本：

1. `get_links.py` —— 抓取 FitGirl 文章页里的所有 FuckingFast 链接，**自动写入 `input.txt`**。
2. `main.py` —— 逐条用浏览器解析直链，然后二选一下载方式：脚本内下载，或转发给 aria2。

## 环境要求

- `Python 3.13`（推荐；更高版本可能尚未适配）
- Python 依赖：

```bash
pip install -r requirements.txt
```

- 仅 `--method aria2` 需要：一个 `aria2c` 可执行文件。启动时开启 RPC 与完成回调：

```bash
aria2c --enable-rpc --rpc-listen-port=16800 --rpc-listen-all=false --continue=true ^
       --on-download-complete="python C:\path\to\aria2_hook.py complete" ^
       --on-download-error="python C:\path\to\aria2_hook.py error"
```

（回调是可选的：即使不配，已完成的文件也会通过磁盘上的文件被识别。）

## 使用方法

### 1. 收集链接

```bash
python get_links.py
python get_links.py "https://fitgirl-repacks.site/some-game/"            # 直接传 URL
python get_links.py "https://fitgirl-repacks.site/some-game/" --append   # 合并，不覆盖
```

`input.txt` **每次运行都会被重写**（原子替换，中途崩溃不会留下半截文件）。想保留上一个游戏的链接就加 `--append`。抓到的完整列表同时会复制到剪贴板。

### 2. 下载

```bash
python main.py                    # 运行后询问使用哪种下载方式
python main.py --method aria2     # 跳过询问，转发到 127.0.0.1:16800 的 aria2
python main.py --method direct    # 跳过询问，使用脚本内置下载
```

启动时会先询问：

```
22:35:38 » INFO  • Download method : 1 = direct (built-in)  |  2 = aria2 (RPC)
22:35:38 » INPUT • Select download method [1/2] (default 1) :
```

按 `1` 或直接回车使用内置下载器，按 `2` 使用 aria2。传了 `--method` 就完全跳过提问；`--no-prompt`（或在计划任务等非交互环境下运行）会直接回落到 `direct`。如果选了 aria2 但 RPC 端口没开，脚本会询问是否改用内置下载，而不是在过完 Cloudflare 之后才报错退出。

**aria2 模式下，所有链接转发完毕脚本就退出。** aria2 是独立进程，会继续下载：

```
22:51:57 » DONE • Forwarded : 3 job(s) to aria2 on port 16800
22:51:57 » INFO •   job : Hollow_Knight_...part1.rar -> 2080a4d1...
22:51:57 » INFO • Download folder : D:\Downloads\...\Hollow_Knight
```

想保留「一直等到下完」的旧行为，加 `--wait`，脚本会持续轮询 `aria2.tellStatus`。

`main.py` **从不修改 `input.txt`**。进度记录在 `state.json` 中，以原始链接为键，因此同一份文件可以反复运行：

- 已完成的条目会被跳过（并核对磁盘上文件的大小）；
- `forwarded` 状态但文件还不存在时，会向 aria2 确认一次：任务仍在跑就跳过，gid 已失效则重新解析；
- 未完成的条目从已有字节继续续传；
- 失败的条目会重试；
- `--force` 忽略以上全部记录。

由于脚本会提前退出，它看不到最终结果。有两种办法补上这块信息：

1. **完成回执（推荐）** —— 用上面的参数启动 aria2。下载完成或失败时，`aria2_hook.py` 会向 `aria2_receipts.jsonl` 追加一行 JSON；下次运行 `main.py` 时会把这些回执合并进 `state.json`。
2. **直接重跑** —— 已完成的文件会被识别并跳过，不会重新走浏览器。

## 工作原理

```
input.txt ──► 逐条处理（串行，共用同一个浏览器会话）
                 ├─ URL 的 fragment 就是真实文件名
                 ├─ 用 nodriver 打开链接，等待 Cloudflare 校验通过
                 ├─ 等待新的 Turnstile token（第 7 秒自动点击一次）
                 ├─ 页面内 POST /f/<id>/go → 读取 HX-Redirect → 直链
                 └─ 分派：
                       direct → requests 流式下载 + Range 续传 + tqdm 进度条
                       aria2  → aria2.addUri 后立即退出（--wait 时轮询 tellStatus）
```

每一条链接都是解析完、转发完才处理下一条，所以 aria2 已经在下载 part1 时，浏览器还在解析 part2。最后一条转发完成后浏览器立即关闭，aria2 继续运行。

## 参数

`python main.py --help`

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--method {direct,aria2}` | 询问 | 下载方式；不传会触发 `1/2` 选择 |
| `--no-prompt` | 关闭 | 从不提问；未指定 `--method` 时使用 `direct` |
| `--input PATH` | `input.txt` | 链接列表 |
| `--output PATH` | `downloads/<游戏名>` | 下载目录 |
| `--state PATH` | `state.json` | 进度记录 |
| `--limit N` | `0`（全部） | 只处理前 N 条链接 |
| `--force` | 关闭 | 忽略已记录的状态，全部重做 |
| `--headless` | 关闭 | 浏览器不显示窗口 |
| `--aria2-host` / `--aria2-port` | `127.0.0.1` / `16800` | aria2 RPC 地址 |
| `--aria2-secret` | `$ARIA2_SECRET` | aria2 RPC 密钥 |
| `--aria2-split` | `4` | 每个文件的连接数 |
| `--wait` | 关闭 | 持续轮询 aria2，直到所有任务结束 |
| `--receipts PATH` | `aria2_receipts.jsonl` | 启动时合并的回执文件 |

`python get_links.py --help`

| 参数 | 默认值 | 说明 |
|---|---|---|
| `url` | 交互输入 | FitGirl 游戏页面地址 |
| `--input PATH` | `input.txt` | 链接写入位置 |
| `--append` | 关闭 | 与已有列表合并 |
| `--timeout SECS` | `30` | HTTP 超时 |

## 说明

- `input.txt`、`state.json`、`downloads/`、`aria2_receipts.jsonl`、`.temp_browser_profile/` 都相对于脚本所在目录解析，不再依赖当前工作目录。
- 无头模式在浏览器 profile 已被信任时可以工作，但 Turnstile 可能需要点击 —— 如果卡住，去掉 `--headless` 重跑。
- 直链排队久了会过期；失败的链接会以 `failed` 留在 `state.json` 中，下次运行重试。
- 有任何链接解析失败或被 aria2 拒绝时退出码为 `1`，否则为 `0`。
- `state.json` 是普通 JSON，删掉即可从零开始。`aria2_receipts.jsonl` 超过 4 MB 会自动轮转为 `.old`。

### 状态字段

| 状态 | 含义 | 下次运行 |
|---|---|---|
| `done` | 已完成，记录了 `size` | 文件仍匹配则跳过 |
| `forwarded` | 已交给 aria2，结果未知 | 文件已存在或 aria2 仍持有该 gid 则跳过，否则重新解析 |
| `failed` | 解析失败或被 aria2 拒绝 | 重试 |

## 常见问题

- `aria2 refused <method> with HTTP 400: ...` —— 消息里会带 aria2 自己的响应体。最常见原因是 RPC 的 `--rpc-secret` 与 `--aria2-secret` 不一致。
- `cannot reach aria2 RPC ... start aria2c with --enable-rpc` —— 端口上没有服务，检查 `--aria2-port` 与 `--rpc-listen-port` 是否一致。
- `aria2 reported complete but <path> is missing`（仅 `--wait` 模式）—— aria2 把文件写到了别处，通常是 `--dir`/`out` 被其他客户端覆盖。

## 免责声明

本工具仅用于学习与合法用途。请勿将其用于任何恶意或违法场景。开发者不对使用本工具造成的任何非法或不道德行为负责。
