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

本仓库不附 APK（GitHub 不适合托管二进制），自己编译只要三步：

1. 装 **Android Studio**（任意近期版本，自带 SDK 和 Gradle）
2. **Open** 打开 `android-relay/` 目录，等 Gradle 同步完
   （首次会自动下载 Gradle 8.7 和 SDK 组件，要联网）
3. 菜单 **Build → Build APK(s)**，产物在
   `app/build/outputs/apk/debug/app-debug.apk`

传到手机安装（需要允许"安装未知来源应用"）。想要 release 签名包，
Build → Generate Signed Bundle/APK，按向导生成一个 keystore 即可。

不想装 Android Studio 的替代路径：装 JDK 17 + Android 命令行工具，
`gradle assembleDebug`。命令行党自己折腾，不展开。

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

## 已知限制

- `SEND_MULTIPLE` 拿不到纯文字；纯文字分享（浏览器分享链接）会存成 `分享文字.txt`
- 微信的 FileProvider 偶尔不给文件名，APP 会按 `时间戳.jpg/bin` 兜底
- 电脑地址填错或电脑不在线：文件仍然存进了手机 Download，不会丢；
  Toast 会提示失败原因
