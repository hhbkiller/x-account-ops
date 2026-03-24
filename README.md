# X Account Ops

一个可直接复用的 OpenAI Codex / OpenClaw Skill，用来自动运维 X 平台账号。

它支持：

- 发布纯文本帖子
- 发布图文帖子
- 发布明确的 thread 串帖
- 搜索指定主题的帖子
- 按“热度”重排搜索结果
- 对热门帖子自动蹭热度，默认走 quote tweet
- 点赞、转发、删除、查帖
- 同时检查 OAuth2 / Auth1 是否可用

注意：

- `Thread` 和 `X Articles` 不是一回事。
- 当前这个 skill 支持的是 `thread`。
- 当前这个 skill 不支持原生 `X Articles` 发布。
- `article` 命令现在不会再默认把长文误发成十几个跟帖。

当前实现采用双栈认证：

- OAuth 2.0：用于文本发帖、搜索、回复、点赞、转发、删除、自检
- OAuth 1.0a：用于更稳定的媒体上传和图文发帖

## 目录结构

```text
x-account-ops/
├─ agents/openai.yaml
├─ references/env-and-scopes.md
├─ scripts/x_ops.py
├─ requirements.txt
├─ README.md
└─ SKILL.md
```

## 环境要求

- Python 3.10+
- `requests`

安装依赖：

```bash
pip install -r requirements.txt
```

## 凭证配置

把凭证写到工作目录 `.env` 中。

### OAuth 2.0

```env
Client ID=...
Client Secret=...
Access Token=...
Refresh Token=...
User ID=...
```

### OAuth 1.0a

```env
Consumer Key=...
Consumer Key Secret=...
auth1 Access Token=...
auth1 Access Secret=...
```

### 代理配置

很多用户所在网络环境无法直接访问 `api.x.com` 或 `upload.twitter.com`，这时必须配置代理。

本项目支持直接把代理写进 `.env`，脚本会自动读取并应用到所有请求：

```env
HTTP_PROXY=http://127.0.0.1:7890
HTTPS_PROXY=http://127.0.0.1:7890
```

如果你使用的是 SOCKS 代理，也可以写成：

```env
ALL_PROXY=socks5://127.0.0.1:1080
```

常见建议：

- 只有一个统一代理时，优先配置 `HTTPS_PROXY`
- 如果 HTTP 和 HTTPS 都走同一个代理，可以同时填写 `HTTP_PROXY` 和 `HTTPS_PROXY`
- 图文发帖会访问 `upload.twitter.com`，如果不走代理，这一步最容易失败

说明：

- 如果只配置 OAuth2，可以完成文本发帖、搜索、跟贴等能力。
- 如果额外配置了 OAuth1，脚本会自动把媒体上传和图文发帖切到 OAuth1。

## 快速开始

### 1. 检查凭证

```bash
python scripts/x_ops.py doctor
```

### 2. 查看当前账号

```bash
python scripts/x_ops.py me
```

### 3. 发布纯文本帖子

```bash
python scripts/x_ops.py post --text "今天开始测试 X 自动运营 skill"
```

### 4. 发布图文帖子

```bash
python scripts/x_ops.py post --text "这是一条图文帖子" --image ./cover.jpg
```

### 5. 搜索主题热门帖

```bash
python scripts/x_ops.py search --query "AI agents -is:retweet" --sort hot --limit 10
```

### 6. 自动跟贴

```bash
python scripts/x_ops.py hot-reply \
  --query "AI agents -is:retweet" \
  --limit 3 \
  --reply-template "@{username} 这条关于{topic}的观点很有意思，尤其是“{excerpt}”。" \
  --dry-run
```

去掉 `--dry-run` 就会真正发送。

说明：

- `hot-reply` 现在默认走 `quote tweet`
- 如果你明确要直接回复，显式加 `--channel reply`
- 如果你只想走 quote tweet，也可以直接用 `hot-quote`

### 7. 发布长文 thread

```bash
python scripts/x_ops.py thread --title "发布说明" --text-file ./article.md
```

## 安装教程

下面给出两种常用安装方式。

### 方式一：直接克隆到本地后使用

```bash
git clone https://github.com/hhbkiller/x-account-ops.git
cd x-account-ops
pip install -r requirements.txt
```

然后在仓库根目录创建 `.env`，写入你的 X 凭证，再执行：

```bash
python scripts/x_ops.py doctor
```

推荐直接从仓库内的 `.env.example` 复制：

```bash
cp .env.example .env
```

Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

### 方式二：安装为 Codex / OpenClaw 本地 skill

如果你希望它作为本地 skill 被长期复用，直接把整个目录复制到本地 skills 目录即可。

常见目录示例：

```bash
~/.codex/skills/x-account-ops
```

或 Windows:

```powershell
$env:USERPROFILE\.codex\skills\x-account-ops
```

推荐步骤：

1. 克隆仓库

```bash
git clone https://github.com/hhbkiller/x-account-ops.git
```

2. 复制目录到 skills 目录

Windows PowerShell 示例：

```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\.codex\skills" | Out-Null
Copy-Item -Recurse -Force .\x-account-ops "$env:USERPROFILE\.codex\skills\x-account-ops"
```

macOS / Linux 示例：

```bash
mkdir -p ~/.codex/skills
cp -R ./x-account-ops ~/.codex/skills/x-account-ops
```

3. 安装依赖

```bash
cd ~/.codex/skills/x-account-ops
pip install -r requirements.txt
```

4. 准备 `.env`

把 `.env` 放在运行工作目录，或在执行命令时显式指定：

```bash
python scripts/x_ops.py --env-file /path/to/.env doctor
```

建议直接从示例文件复制：

```bash
cp .env.example .env
```

5. 重启你的 Codex / OpenClaw 会话，让新的 skill 被重新扫描到

### 安装后如何验证

先跑下面三个命令：

```bash
python scripts/x_ops.py doctor
python scripts/x_ops.py me
python scripts/x_ops.py search --query "AI -is:retweet" --sort hot --limit 3
```

如果：

- `doctor` 显示 `oauth2.ok=true`
- `me` 能返回账号信息
- `search` 能返回帖子列表

说明安装已经成功。

如果你所在网络不能直接访问 X，还要再确认：

- `.env` 已正确填写 `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY`
- 本地代理工具已经启动
- `doctor` 可以正常返回结果而不是网络错误

## 常用命令

```bash
python scripts/x_ops.py doctor
python scripts/x_ops.py me
python scripts/x_ops.py refresh
python scripts/x_ops.py search --query "OpenAI" --sort hot --limit 5
python scripts/x_ops.py lookup --tweet-id 1234567890
python scripts/x_ops.py post --text "hello"
python scripts/x_ops.py post --text "hello" --image ./a.jpg
python scripts/x_ops.py thread --title "长文串帖" --text-file ./article.md
python scripts/x_ops.py reply --tweet-id 1234567890 --text "收到"
python scripts/x_ops.py hot-reply --query "AI" --reply-template "这个观点值得继续展开：{excerpt}" --dry-run
python scripts/x_ops.py hot-reply --query "AI" --channel reply --reply-template "@{username} 我同意你提到的这点：{excerpt}" --dry-run
python scripts/x_ops.py hot-quote --query "AI" --reply-template "这条值得关注：{excerpt}" --dry-run
python scripts/x_ops.py like --tweet-id 1234567890
python scripts/x_ops.py repost --tweet-id 1234567890
python scripts/x_ops.py delete --tweet-id 1234567890
```

## 热门帖过滤规则

`search`、`hot-reply` 和 `hot-quote` 默认会：

- 跳过回复帖
- 跳过转帖

如果你想扩大召回范围，可以显式关闭：

```bash
python scripts/x_ops.py search --query "AI" --sort hot --no-skip-replies --no-skip-reposts
```

## 适用场景

- 自动运营个人 X 账号
- 自动发布产品动态
- 搜索某个主题下的热门帖子
- 对热门帖子做半自动或全自动互动
- 低成本维护品牌账号内容节奏

## 注意事项

- `article` 不再默认把长文转换成 thread。这样做是为了避免把“文章”误发成十几个跟帖。
- 如果你明确就是要发 thread，请使用 `thread` 命令。
- 如果你一定要兼容旧用法，可以显式写：`article --as-thread`
- `hot-reply` 默认走 `quote tweet`，这是为了绕开“禁止陌生人回复”的限制场景
- 大多数情况下 `quote tweet` 比直接回复更稳，但少数帖子也可能禁止被 quote
- 如果你明确要走回复链路，请显式传 `--channel reply`
- `hot-reply` / `hot-quote` 不会自己生成内容，你需要提供 `--reply-text`、`--reply-text-file` 或 `--reply-template`
- 公开搜索只覆盖近 7 天内容
- “热度”是基于最近帖子列表做本地重排，不是 X 官方返回的原生热榜
- 建议先用 `--dry-run` 检查目标帖子和回复内容，再执行真实发送
- 如果你所在网络无法直连 X，请务必先配置代理，否则搜索、发帖、图文上传都可能失败

## 与 Skill 的关系

这个仓库本身就是一个可下载的 skill 目录。  
如果你想把它放进本地 skills 目录，直接复制整个文件夹即可。

## 参考

- [SKILL.md](./SKILL.md)
- [references/env-and-scopes.md](./references/env-and-scopes.md)
