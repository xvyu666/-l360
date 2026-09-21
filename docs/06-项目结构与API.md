# 06 · 项目结构与 API

## 模块职责

| 文件 | 职责 | 关键内容 |
| --- | --- | --- |
| `server.py` | HTTP 服务、任务调度、全部 API | `ThreadingHTTPServer`，打印任务跑在后台线程，前端轮询 `/api/progress/<tid>` |
| `printer_core.py` | 打印机与纸张能力、GDI 打印 | `list_printers()` / `is_real_printer()` / `real_printers()` / `supported_papers()` / `print_images()` |
| `renderer.py` | 把各种格式渲染成位图 | `open_source()` / `compose_page()` / `apply_margins()` / `mm_to_px()` / `office_to_pdf()` |
| `layout.py` | 多图拼版 | 宫格、缩放、位置 |
| `native.py` | Office COM 原格式直印 | Word/Excel/PPT 的 `PrintOut` |
| `office.py` | Office COM 小工具 | 转 PDF 等 |
| `wechat.py` | 微信本地数据扫描 | `scan()` / `scan_images()` / `image_sessions()` / `status()` / `resolve()` |
| `inbox.py` | 手机中转收件箱 | `add()` / `items()` / `resolve()` / `remove()` / `clear()`，落在 `runtime/inbox/` |
| `notes.py` | 笔记存储 | `save()` / `items()` / `cats()` / `load()`，一份笔记一个 json |
| `htmlnote.py` | 笔记 HTML 渲染 | `make_note_source()` → `NoteSource`，支持标题/粗斜体/列表/引用/分割线 |
| `selftest.py` | 不耗材自检 | 见 [02](02-源码部署.md) |
| `setup.py` | 防火墙 + 开机自启 | 需管理员权限 |
| `build_portable.py` | 打便携版 | 见 [05](05-便携版打包.md) |
| `web/` | 手机端页面 | 原生 HTML/CSS/JS，无框架、无构建步骤 |

## 数据流

```
手机 → POST /api/upload  → runtime/jobs/<id>/ 落盘
     → POST /api/print   → 后台线程
                             ├─ renderer.open_source()  读成页
                             ├─ compose_page()          排版/边距/reflow
                             ├─ printer_core.print_images()  GDI 出纸
                             └─ 进度写进 _tasks，手机轮询 /api/progress
```

微信那条路是 `POST /api/wechat/import` 先把选中的文件**复制**进 jobs 目录，
之后和上传的文件走完全相同的流程。

## HTTP API

### GET

| 路径 | 说明 |
| --- | --- |
| `/` | 手机端页面 |
| `/health` | 健康检查，返回 `{"ok":true,...}`；也被用来判断"这个端口上是不是已经有一个本服务" |
| `/api/info` | 当前打印机、可选打印机列表（含每台的能力详情）、支持的扩展名 |
| `/api/state` | 打印机状态 + 支持的纸张 + 扩展名 |
| `/api/canvas` | 给定纸张/方向/质量，返回精确画布像素尺寸和毫米尺寸 |
| `/api/jobs` | 最近 40 个作业 |
| `/api/preview/<jobId>/<n>` | 第 n 页预览图 |
| `/api/source/<jobId>/<n>` | 第 n 页原图 |
| `/api/progress/<taskId>` | 打印任务进度 |
| `/api/wechat/status` | 微信是否可用、数据目录位置 |
| `/api/wechat/files` | 微信文件列表（支持 `since` 过滤） |
| `/api/wechat/images` | 微信图片列表（带清晰度评级与建议纸张） |
| `/api/wechat/sessions` | 图片所属会话列表（用于筛选） |
| `/api/wechat/thumb/<fid>` | 图片缩略图 |
| `/api/inbox/list` | 手机中转收件箱列表（新的在前） |
| `/api/inbox/thumb/<id>` | 收件箱图片缩略图 |
| `/api/notes/list` | 笔记列表（`?cat=` 按分类过滤） |
| `/api/notes/cats` | 分类清单 + 每类篇数 |
| `/api/notes/get/<id>` | 笔记全文（含 HTML） |

### POST

| 路径 | 说明 |
| --- | --- |
| `/api/text` | 随手记文本 → 作业 |
| `/api/upload` | 上传文件（文件名走 `X-File-Name` 头，支持中文） |
| `/api/print` | 提交打印任务，返回 `taskId` |
| `/api/compose` | 提交拼版打印任务（`printer` 是 body 顶层字段，不是在 `options` 里）。`options.duplex` + `options.phase` 用于手动双面，两轮分别传 `odd` / `even` |
| `/api/compose-preview` | 合成某一页返回 JPEG，只画不出纸；body=`{pages, options, index, edge}` |
| `/api/testpage` | 打一张测试页 |
| `/api/wechat/import` | 把选中的微信文件复制成作业 |
| `/api/inbox/upload` | 安卓中转 APP 上传：body=文件字节，`X-File-Name` 头带文件名 |
| `/api/inbox/import` | `{"ids":[...]}` 把收件箱文件复制成作业 |
| `/api/inbox/remove` | `{"ids":[...]}` 删除收件箱文件 |
| `/api/inbox/clear` | 清空收件箱 |
| `/api/notes/save` | 新建/更新笔记（有 `id` 就更新），自动生成标题与摘要 |
| `/api/notes/remove` | `{"id":...}` 删除笔记 |
| `/api/note` | `{"id":...}` 把笔记加入打印队列（kind=note，走 htmlnote 渲染） |
| `/api/printer` | 记住选择的打印机（写 `runtime/config.json`） |

### DELETE

| 路径 | 说明 |
| --- | --- |
| `/api/job/<jobId>` | 删除作业 |

## 关键常量

```python
MAX_UPLOAD   = 200 MB     # 单个上传上限
PORT_START   = 8760       # 起始端口，占用则顺延到 8765
QUALITY_DPI  = {"draft":200, "standard":300, "high":360}
PREVIEW_EDGE = 320        # 预览图长边像素
```

## 前后端约定

- 前端不猜画布尺寸，一律问 `/api/canvas`，保证预览和出纸一致
- 打印机能力（支持哪些纸）由 `/api/info` 下发，前端把不支持的置灰
- 打印是异步的：提交拿到 `taskId`，轮询进度，不要同步等
