# 09 · 安卓中转 APP（打印中转）

> 一个只做一件事的 APP：注册成系统分享接收者。
> 微信 / 浏览器 / 相册 / WPS 里点「分享」→ 列表里出现「打印中转」→
> 文件存进手机固定文件夹，同时自动传到电脑的打印收件箱，网页里直接选着打。

## 它做了什么（以及刻意不做什么）

```
微信/相册/浏览器/WPS
        │ 点「分享」选「打印中转」
        ▼
ReceiveActivity（透明页，收完即退）
        │
        ├──① 存进手机 Download/手机打印收件箱/（永远做，离线也有）
        │
        └──② 配了电脑地址的话，POST /api/inbox/upload 自动上传（主链路）
                     │
                     ▼
             电脑 runtime/inbox/ → 网页「手机中转」里选着打
```

- **没有界面残留**：接收页是透明的，收完弹个 Toast 就退，不打断你手上的操作。
- **不上架、不要权限**：Android 10+ 用 MediaStore 写 Download，零存储权限；
  Android 9 及以下才需要运行时申请一次写权限。
- **不联网**：只连你在设置里填的那台电脑，走局域网 HTTP。
- **零第三方依赖**：连 okhttp 都没用，上传是原生 `HttpURLConnection`，
  编译不需要额外仓库，也不会因为依赖升级而坏。

> 为什么主链路是「自动上传」而不是「网页监控手机文件夹」：
> 手机浏览器有沙箱，网页**没有能力**扫手机任意文件夹。
> 所以让 APP 主动把文件推到电脑；存手机 Download 那份是保险——
> 就算没配电脑地址，你也可以在网页的「选择文件」里手动选到它。

## 目录结构

```
android-relay/
├── build.gradle / settings.gradle / gradle.properties
├── gradle/wrapper/gradle-wrapper.properties
└── app/
    ├── build.gradle                    compileSdk 34 / minSdk 21 / 零依赖
    └── src/main/
        ├── AndroidManifest.xml         SEND/SEND_MULTIPLE 注册在这里
        ├── java/com/printinbox/relay/
        │   ├── ReceiveActivity.java    分享接收（核心，全透明）
        │   ├── MainActivity.java       设置页：电脑地址 + 自动上传开关
        │   ├── InboxStore.java         存 Download/手机打印收件箱
        │   ├── Uploader.java           流式 POST 到 /api/inbox/upload
        │   └── Config.java             SharedPreferences
        └── res/                        图标是矢量 XML，没有二进制 png
```

## 编译

两条路，推荐第一条（不用装 1.5GB 的 Android Studio）：

**A. GitHub 云端编译（推荐）**

仓库里已经配好 `.github/workflows/build-apk.yml`。代码推上去后打一个 tag：

```bash
git tag v1.0.0 && git push origin v1.0.0
```

Actions 会自动编译，产物直接挂在 Release 附件里：`app-debug.apk` 和 `app-release.apk`（各约 100KB）。
也可以不打 tag：Actions 页手动 `Run workflow`，从 Artifacts 下载。

**B. 本地 Android Studio**

1. 装 Android Studio（自带 SDK + Gradle）
2. **Open** 打开 `android-relay/` 目录（子目录，不是仓库根），等同步完
3. **Build → Build APK(s)**，产物在 `app/build/outputs/apk/debug/app-debug.apk`

仓库里没有 gradlew（只有 `gradle-wrapper.properties`），Studio 打开时会自动补齐；
不想开 IDE 就用系统 Gradle：`gradle -p android-relay assembleDebug`（需 JDK 17 + Android SDK）。

> **签名**：仓库不含私钥，但没有 keystore 时 `assembleRelease` 会自动回落到 debug 签名，
> 所以两个包都是能直接装到手机上的。想用自己的签名就跑一次 `make-keystore.bat`。
> 完整的构建命令、产物路径、最低 Android 版本（5.0）和装机授权步骤见
> **[11-APK构建与安装](11-APK构建与安装.md)**。

> 装到手机上的具体授权步骤（各品牌的「允许安装未知应用」入口、常见报错、
> 首次填写电脑地址）见 **[10-手机端安装](10-手机端安装.md)**。

## 手机端设置（一次）

1. 打开「打印中转」APP
2. 填**电脑地址**：电脑上打印服务窗口显示的那个，如 `http://192.168.1.10:8760`
3. 「收到就自动传到电脑」保持开启

## 用法

| 场景 | 操作 |
| --- | --- |
| 微信收到的文件/图片 | 点开 → 长按或右上角 → 分享 → **打印中转** |
| 相册截图/照片 | 相册勾选（可多选）→ 分享 → **打印中转** |
| 浏览器网页/图片 | 分享 → **打印中转** |
| WPS 打开的文档 | 分享/发送 → **打印中转** |

然后回到打印网页 →「手机中转」→ 文件已经在列表里了，勾选加入队列。

多选分享走 `SEND_MULTIPLE`，一批最多传到 30 个（服务端上限）。
单个文件上限 200MB，和服务端上传一致。

## 网页端「手机中转」面板

- 列表每 **4 秒**自动刷新，新到的文件带「新」标
- 图片直接出缩略图，文档按类型显示色块徽标
- 全选 / 不选 / 清空收件箱
- 「加入队列」后文件进入正常打印流程（可预览、可选纸张），
  收件箱里的原件保留，手动「清空收件箱」才删

## 服务端接口（给想自己写客户端的人）

| 接口 | 说明 |
| --- | --- |
| `POST /api/inbox/upload` | body=文件字节，`X-File-Name: <URL编码的文件名>`，返回 `{"ok":true,"item":{...}}` |
| `GET /api/inbox/list` | 列表，新的在前 |
| `POST /api/inbox/import` | `{"ids":[...]}` → 复制成打印作业 |
| `POST /api/inbox/remove` | `{"ids":[...]}` |
| `POST /api/inbox/clear` | 清空 |
| `GET /api/inbox/thumb/<id>` | 图片缩略图 |

## 排错：手机上显示"已存入收件箱"，但电脑收不到

最典型的报错是 Toast 显示 **`电脑返回 404`**，同时网页「手机中转」面板里也看不到文件。

> 这两个症状是同一个原因：
> 文件确实存进了手机（所以 Toast 前半句成功），但**上传这一步在电脑上被拒了**。

### 先做这一条：确认打印服务是不是"旧进程"（九成是这个）

打印服务是**常驻进程**。改完 `server.py` / `inbox.py` 之后，
老进程内存里还是旧的代码，新加的那几个 `/api/inbox/*` 路由它根本没有，
于是请求一律走到路由表末尾返回 **404**。

从头到尾 10 秒搞定：

```bash
python tools/restart_service.py --probe    # 先看一眼状态
python tools/restart_service.py            # 确认要重启，这条会停旧进程再拉新的
```

`--probe` 的输出长这样，看最后两行就知道该不该重启：

```
    /api/inbox/list      -> 404          <<< 404：进程用的是旧代码
  服务端特性：inbox, notes, wechat
    !! 缺少 inbox —— 这个进程加载的是改之前的 server.py，必须重启才会生效。
```

`features` 是服务端自己报告的能力清单，由 `server.py` 里的 `supported_features()`
从**运行时真正加载的那段代码**反查得来（见 `server.py` 尾部），不会说谎 ——
所以它能干净地把「进程旧」和「代码写错了」区分开。

直接看也行，浏览器打开：

```
http://127.0.0.1:8760/api/info
```

返回的 `features` 数组里没有 `"inbox"`，就是这个进程需要重启。

> 只想重启、不想要脚本：Windows 上双击 `启动打印服务.bat` 之前，
> 先运行一次 `停止打印服务.bat`；或者直接关掉那个最小化的黑色窗口再重启。

### 第二可能：手机里填的电脑地址不对

中转 APP 里要填的是 **`http://电脑IP:8760`**，四个地方容易错：

| 错法 | 表现 |
| --- | --- |
| 端口填成 8765 或其它 | 通常直接连不上（超时/拒绝），不是 404 |
| 电脑 DHCP 换了 IP | 同上；建议路由器里把 MAC 和 IP 绑起来 |
| 地址末尾多了路径 | 拼出来是 `/xxx/api/inbox/upload` → 404 |
| 写成 `https://` | 连不上，服务端只监听 http |

确认当前该填什么，看 `启动打印服务.bat` 打开的黑窗口，或者：

```
http://127.0.0.1:8760/api/info     →   里面 "ip" 和 "port" 字段
```

### 第三可能：用的是便携版，而便携版是旧打包

`dist/手机打印助手/` 里的代码是**打包那一刻的快照**，不会自动跟着源码变。
改完 `server.py` 要重新打一次：

```bash
python build_portable.py
```

判断依据也很直接：便携版目录下的 `app/server.py` 里搜不到 `api/inbox` 字样，
说明这是个没有收件箱功能的旧包。

### 第四可能：Integrate 之后还是要重启

改了这些文件，全都需要重启打印服务才生效：

`server.py`、`inbox.py`、`notes.py`、`htmlnote.py`、`renderer.py`、`printer_core.py`、
`wechat.py`、`native.py`、`office.py`

只改 `web/index.html` 不用重启，**手机浏览器强刷一次**（下拉刷新）即可。

## 已知限制

- `SEND_MULTIPLE` 拿不到纯文字；纯文字分享（浏览器分享链接）会存成 `分享文字.txt`
- 微信的 FileProvider 偶尔不给文件名，APP 会按 `时间戳.jpg/bin` 兜底
- 电脑地址填错或电脑不在线：文件仍然存进了手机 Download，不会丢；
  Toast 会提示失败原因
- 改完服务端代码忘了重启：见上面排错第一节，症状是 404
