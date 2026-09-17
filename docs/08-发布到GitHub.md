# 08 · 发布到 GitHub（完整步骤）

从零到"别人能搜到、能下载便携版"的全流程。命令都在 **Windows（Git Bash 或 PowerShell）** 下执行。

> 约定：本文里 `<你的用户名>` 替换成 GitHub 用户名，仓库名用 `printserver`。

---

## 第 0 步 · 发布前检查（别跳过）

公开仓库是**不可逆**的——一旦 push，搜索引擎、存档站、爬虫都会留副本。先花两分钟检查：

### 0.1 隐私扫描

```bash
# 在项目根目录执行，搜可能泄露的东西
git grep -n -E "192\.168\.|wxid_|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+" -- .
```

本项目已清理的内容：

- [x] 示例 IP 改成 `192.168.x.x`
- [x] 注释里的本机盘符路径改成通用描述
- [x] `runtime/`（日志、作业、配置）进 `.gitignore`
- [x] `dist/`（80MB 便携版）进 `.gitignore`

**还要自己确认**：`runtime/server.log` 里可能有真实 IP 和文件名，
只要 `runtime/` 被 ignore 就不会上传。用下面的命令复核：

```bash
git status --porcelain        # 列出所有将被提交的文件，逐一眼一遍
git check-ignore -v dist runtime/server.log
```

### 0.2 体积检查

```bash
git add -A
git ls-files | xargs -I{} stat -c "%s {}" {} | sort -rn | head -10
```

任何单文件 > 50MB 都要警惕（GitHub 硬上限 100MB，超过会直接拒收）。
本仓库最大的文件是 `web/index.html`（约 55KB），完全没问题。

---

## 第 1 步 · 准备工具与账号

### 1.1 安装 Git

本机已装：`git version 2.55.0`。没有的话去 <https://git-scm.com/download/win>。

做一次全局配置（用户名邮箱会永久留在提交记录里）：

```bash
git config --global user.name  "你的名字"
git config --global user.email "you@example.com"
```

中文环境建议再加三条，免得文件名和日志变乱码：

```bash
git config --global core.quotepath false      # ls 里直接显示中文文件名，不转义
git config --global core.autocrlf true        # Windows 上 checkout 成 CRLF
git config --global i18n.logOutputEncoding utf-8
```

> 本仓库带了 `.gitattributes`，`*.bat` 强制 CRLF、源码强制 LF，
> 所以不管谁在哪个系统 clone，bat 都不会被改坏。这条在有 .bat 的 Windows 项目里很重要。

### 1.2 GitHub 账号

没有就注册 <https://github.com/signup>。建议顺手开启 **2FA**（后面用 PAT 或 SSH 都需要）。

### 1.3 选一种认证方式（三选一）

| 方式 | 适合 | 备注 |
| --- | --- | --- |
| **A. GitHub CLI（推荐）** | 想少折腾 | `gh auth login` 一条命令搞定浏览器授权 |
| **B. SSH** | 长期开发者 | 配一次，之后完全免密 |
| **C. HTTPS + PAT** | 最通用 | 密码位置填 Personal Access Token |

> ⚠️ 有些网络环境下 GitHub 的 HTTPS（443）会超时或 `Empty reply from server`。
> 如果遇到，直接换 **A** 或 **B**。

#### 方式 A：GitHub CLI

```bash
winget install --id GitHub.cli          # 或去 https://cli.github.com 下载
gh auth login                           # 选 GitHub.com → HTTPS → 浏览器授权
gh auth status                          # 确认已登录
```

#### 方式 B：SSH

```bash
ssh-keygen -t ed25519 -C "you@example.com"     # 一路回车
cat ~/.ssh/id_ed25519.pub                       # 复制输出
```

GitHub → Settings → SSH and GPG keys → **New SSH key** → 粘贴保存。验证：

```bash
ssh -T git@github.com
# 看到 Hi <用户名>! You've successfully authenticated... 就成了
```

#### 方式 C：HTTPS + PAT

GitHub → Settings → Developer settings → Personal access tokens → Tokens (classic)
→ Generate new token → 勾 `repo` → 生成并**立刻复制**（只显示一次）。

推送时：用户名填 GitHub 用户名，密码**填这个 token**（不是登录密码）。

---

## 第 2 步 · 初始化本地仓库

在项目根目录（`printserver/`）执行：

```bash
cd C:/Users/旭/WorkBuddy/2026-09-17-20-26-42/printserver

git init
git branch -M main                 # 统一用 main 作为主分支
git add -A
git status                         # 复核：dist/ runtime/ __pycache__ 不应该出现
git commit -m "feat: 手机打印助手 v1.0 — 手机网页驱动 USB 打印机"
```

**提交前再看一眼 `git status`**，确认清单里只有源码、文档、web 页面，没有：

- `dist/`（80MB 便携版）
- `runtime/jobs/`、`runtime/server.log`、`runtime/config.json`、`runtime/pyembed.zip`
- `__pycache__/`

如果误加了，先救回来：

```bash
git rm -r --cached dist runtime __pycache__
git commit --amend --no-edit
```

提交信息规范（可选但推荐）：`feat:` / `fix:` / `docs:` / `refactor:` / `chore:` 开头。

---

## 第 3 步 · 在 GitHub 上建空仓库

### 用命令行（方式 A）

```bash
gh repo create printserver \
  --public \
  --source=. \
  --remote=origin \
  --description "手机浏览器直接驱动电脑上的 USB 打印机：上传/微信文件一键排版打印，含免装 Python 便携版" \
  --push
```

一条命令完成：建仓 → 关联 → 推送。

### 用网页

1. 打开 <https://github.com/new>
2. **Repository name**：`printserver`
3. **Description**：手机浏览器直接驱动电脑上的 USB 打印机……
4. 选 **Public**
5. **不要**勾 "Add a README file" / "Add .gitignore" / "Choose a license"
   （本地已经有了，勾了会产生冲突的历史）
6. 点 **Create repository**

建好之后页面会给出仓库地址，记住它：

```
https://github.com/<你的用户名>/printserver.git     (HTTPS)
git@github.com:<你的用户名>/printserver.git          (SSH)
```

---

## 第 4 步 · 关联并推送

```bash
# HTTPS
git remote add origin https://github.com/<你的用户名>/printserver.git

# 或 SSH（推荐，避开 443 超时）
git remote add origin git@github.com:<你的用户名>/printserver.git

# 如果第 3 步用了 gh repo create --push，这步可以跳过
git push -u origin main
```

第一次 push 会弹窗要凭据：

- HTTPS：用户名 + **PAT**
- SSH：可能要求确认指纹，输入 `yes`

验证：

```bash
git remote -v
git log --oneline -5
```

浏览器打开 `https://github.com/<你的用户名>/printserver`，应该能看到 README 已经渲染出来了。

---

## 第 5 步 · 远端复查（公开发布前最后一道关）

在 GitHub 网页上逐项确认：

- [ ] README 正常渲染，图片/链接没断
- [ ] 文件列表里**没有** `dist/`、`runtime/`、`__pycache__`
- [ ] 搜索一下自己的 IP、微信 ID、邮箱有没有出现在代码里
      （仓库页面右上角搜索框，或者本地 `git grep`）
- [ ] `LICENSE` 存在且是 MIT

如果这时候发现漏了隐私文件，光删文件没用（历史里还在），要**清历史**：

```bash
# 极端情况：把 dist 从整个历史里彻底抹掉
git filter-repo --path dist --invert-paths --force     # 需 pip install git-filter-repo
git push --force origin main
```

清完之后**立刻去 GitHub 改掉任何可能已泄露的凭据**。

---

## 第 6 步 · 打包便携版并发布 Release

### 6.1 生成便携版

```bash
pip install -r requirements.txt
python tools/fetch_pyembed.py
python build_portable.py
```

产物在 `dist/手机打印助手/`。打成 zip 并带上版本号：

```bash
cd dist
# 用资源管理器压缩，或：
tar -a -c -f 手机打印助手-v1.0.0-便携版.zip 手机打印助手
```

> 压缩包名带中文没问题；GitHub Release 支持中文文件名。
> 体积约 80MB，远低于 Release 单文件 2GB 上限。

### 6.2 打 Tag

```bash
git tag -a v1.0.0 -m "v1.0.0：首个公开版本，含便携版"
git push origin v1.0.0
```

### 6.3 发 Release（网页）

1. 仓库页右侧 **Releases** → **Create a new release**
2. **Choose a tag**：选 `v1.0.0`
3. **Release title**：`v1.0.0 · 手机打印助手（含便携版）`
4. 描述框里写更新说明，模板：

```markdown
## 便携版（推荐）
下载 `手机打印助手-v1.0.0-便携版.zip`，解压后双击 `启动打印助手.bat` 即可。
**不需要安装 Python**，不联网、不写注册表。

## 源码版
pip install -r requirements.txt && python server.py

## 本次包含
- 手机上传打印：图片 / PDF / Word / Excel / PPT / 文本
- 微信自助打印：直接列出电脑版微信收到的文件与图片
- 重排放大：自动剥白边并按页边距等比放大
- 原格式直印：Office 走 COM 原生打印，保留原文件边距
- 多打印机适配：自动排除虚拟 PDF 打印机，按驱动能力过滤纸张

## 已知限制
- 仅 Windows
- 微信聊天原图为加密存储，只能取明文缓存版本（详见 docs/03）

## 校验
SHA256: （贴 `certutil -hashfile 文件名 SHA256` 的结果）
```

5. 把 zip **拖进**下方的附件区，等进度条走完
6. 勾 **Set as the latest release**
7. 点 **Publish release**

### 6.4 或者用命令行发

```bash
gh release create v1.0.0 \
  --title "v1.0.0 · 手机打印助手（含便携版）" \
  --notes-file RELEASE_NOTES.md \
  "release/手机打印助手-v1.0.0-便携版.zip"
```

---

## 第 6.5 步 · 让 GitHub 自动编译 APK（不用装 Android Studio）

仓库里已经放好了 `.github/workflows/build-apk.yml`。代码推上去之后，
GitHub 的服务器会帮你编译安卓中转 APP，APK 直接出现在 Release 附件里。
**整个流程不需要本机有 Java / Android SDK。**

### 6.5.1 三个前提，缺一不可

1. 代码已经推到 GitHub（第 4 步做完）
2. 推送的内容里**包含** `.github/workflows/build-apk.yml`
3. Tag 指向的 commit 里**有**这个文件 —— ⚠️ 最容易在这里白忙活：
   如果你**先打了 tag、后加的 workflow**，那个 tag 还停在旧 commit 上，
   推上去之后 **Actions 根本不会触发**。

   先确认：

   ```bash
   git ls-tree -r --name-only v1.0.0 | grep workflow
   # 有输出 = 没问题；没输出 = tag 指错了，重建它：
   git tag -d v1.0.0
   git tag -a v1.0.0 -m "v1.0.0：首个公开版本"
   ```

### 6.5.2 推 tag，触发编译

```bash
git push origin v1.0.0
```

### 6.5.3 等 3~5 分钟，看结果

1. 仓库页 → **Actions** 标签
2. 左侧列表里找「构建安卓中转 APK」，黄色圆点=在跑，绿色勾=成功，红叉=失败
3. 点进去可以看每一步的日志（Gradle 首次编译会慢一点）

### 6.5.4 拿 APK

- **打了 tag 的情况**：APK 会自动挂在 Release 附件里，叫 `app-debug.apk`（约 100KB）
- **没打 tag 的情况**：Actions 页 → 右上角 `Run workflow` → 跑完在页面底部
  **Artifacts** 区下载（需登录 GitHub，保留 90 天）

装到手机上的步骤见 [10-手机端安装](10-手机端安装.md)。

### 6.5.5 出问题时

| 现象 | 原因 | 怎么办 |
| --- | --- | --- |
| Actions 页空空如也 | tag 指向旧 commit（见 6.5.1） | 重建 tag 再推 |
| 工作流没被触发 | 仓库设置里禁用了 Actions | Settings → Actions → General → 选 Allow all |
| 编译失败（红叉） | SDK/Gradle 版本问题 | 点进日志看报错；一般是网络抖动，`Re-run` 一次 |
| Release 里没有 APK | 不是 tag 触发的 | 手动跑一次，或重新推 tag |
| 上传 APK 报权限错 | 仓库没开写权限 | Settings → Actions → General → Workflow permissions → Read and write |

---

## 第 7 步 · 让别人更容易找到

1. **Topics**：仓库页右上角齿轮 → 加 `printer` `print-server` `wechat` `windows` `python` `epson` `lan`
2. **About**：填简介 + 勾选 Website（可填 Release 地址）
3. **README 徽章**（可选）：

```markdown
![Platform](https://img.shields.io/badge/platform-Windows-blue)
![Python](https://img.shields.io/badge/python-3.10%2B-green)
![License](https://img.shields.io/badge/license-MIT-green)
```

4. 把 Release 链接发到群里 / 论坛时，带上"免装 Python"这句话，转化率最高。

---

## 第 8 步 · 之后的日常更新

```bash
git status                       # 看改了什么
git add -A
git commit -m "fix: 修复 XXX"
git push                         # 已 -u 过，不用再带 origin main
```

要发新版本：

```bash
python build_portable.py         # 重新打包
# 打 zip
git tag -a v1.1.0 -m "v1.1.0：XXX"
git push && git push origin v1.1.0
gh release create v1.1.0 --notes "更新内容…" "dist/手机打印助手-v1.1.0-便携版.zip"
```

---

## 第 9 步 · 故障排查

| 症状 | 原因 / 解决 |
| --- | --- |
| `fatal: unable to access ... Empty reply from server` | HTTPS 443 被干扰，换 SSH 或 `gh` CLI |
| curl/github.com 返回 `000`、TLS 握手超时，但 `api.github.com` 正常 | 只对主站干扰。**SSH 是通的**（22 和 443 都行），用 `git@github.com:...` 推送；HTTPS/PAT 这条路在本机走不通 |
| `ssh-keygen -f "C:/Users/中文名/.ssh/id_ed25519"` 报 No such file | ssh-keygen 处理不了非 ASCII 路径。改用 `cd ~/.ssh && ssh-keygen -t ed25519 -f id_ed25519`（相对路径） |
| Actions 编译 APK 一直没跑起来 | 见 6.5.1：tag 指向的 commit 里必须有 workflow 文件 |
| `Support for password authentication was removed` | GitHub 不再接受登录密码，用 PAT 或 SSH |
| `Permission denied (publickey)` | SSH key 没加上，跑 `ssh -T git@github.com` 复测 |
| `remote: error: File ... is 120.00 MB; this exceeds GitHub's file size limit` | 大文件进了提交。用 `git rm --cached` 移出，历史里的用 `git filter-repo` 清 |
| `src refspec main does not match any` | 还没 commit 就 push，先 `git commit` |
| `Updates were rejected because the remote contains work` | 远端有 README/LICENSE（第 3 步勾了）。先 `git pull --rebase origin main` 再 push |
| 中文文件名显示成 `\344\275\240` | 设 `git config --global core.quotepath false` |
| bat 在别人机器上跑不起来 | `.gitattributes` 里已强制 CRLF；确认没被编辑器的 LF 覆盖 |
| push 很慢 | 正常，别中断；大仓库考虑 `git config --global http.postBuffer 524288000` |

---

## 附：完全不想用命令行？

GitHub 网页支持**拖拽上传**：

1. 新建一个空仓库（第 3 步网页方式，但可以勾 README）
2. 仓库页点 **Add file → Upload files**
3. 把 `printserver` 目录下的**源码文件**拖进去（记得排除 `dist`、`runtime`、`__pycache__`）
4. 底部填提交信息 → Commit changes
5. 便携版 zip 走 Release 页面上传（第 6.3 步）

缺点是每次更新都要手动拖，没有历史 diff 的便利，但发第一版是够用的。
