# 11 · APK 能不能打出来？（构建 → 传手机 → 装好）

> 直说结论：**工程本身已经完备，APK 已经实际构建出来并通过官方签名校验。**
>
> 两条路都已跑通：**GitHub 云端**（三条命令），或**本机一键** `python tools/build_apk.py`
> （自动下载 JDK 17 / Android SDK / Gradle 8.7 装到 `C:\android-kit`）。
> 想要立刻拿到安装包，直接看 §3 的产物路径。

---

## 1. 现状体检

### 1.1 已经齐备的部分

逐个查过 `android-relay/` 的结论：

| 组成 | 状态 | 说明 |
| --- | --- | --- |
| Gradle 工程骨架 | ✅ | `settings.gradle` / `build.gradle` / `gradle.properties` 齐全，AGP 8.5.2 |
| 源码 | ✅ | 5 个 Java 文件，互相调用关系完整，无未定义的引用 |
| Manifest | ✅ | `SEND` / `SEND_MULTIPLE` 已注册，两个 Activity 都显式写了 `android:exported`（targetSdk 31+ 的硬要求） |
| 资源 | ✅ | `strings.xml` / `themes.xml` / 矢量图标，无二进制 png |
| 权限 | ✅ | `INTERNET`；`WRITE_EXTERNAL_STORAGE` 限 `maxSdkVersion=28`（Android 10+ 走 MediaStore，零存储权限） |
| 明文流量 | ✅ | `usesCleartextTraffic="true"` —— 局域网 HTTP 上传必需，targetSdk 28+ 默认禁明文 |
| 版本适配代码 | ✅ | `InboxStore` 用 `SDK_INT >= 29` 分叉 MediaStore / 老式写文件 |
| 服务端配套接口 | ✅ | `POST /api/inbox/upload` 在 `server.py:874` 已实现，认 `X-File-Name` 头 |
| 第三方依赖 | ✅ | 零依赖，只用原生 `HttpURLConnection` |

### 1.2 还差的东西

| 缺口 | 影响 | 现状 |
| --- | --- | --- |
| **构建环境**（JDK 17 + Android SDK + Gradle 8.7） | 本机原本无法就地出包 | ✅ `tools/build_apk.py` 一键装到 `C:\android-kit` 并编译（实测 20 分钟内全绿） |
| **`gradlew` / `gradle-wrapper.jar`** | 无法 `./gradlew assembleDebug` | ⚠️ 仓库里只有 `gradle-wrapper.properties`。云端用系统 gradle 绕过；本地脚本同样直接用 Gradle 8.7 |
| **release 签名配置** | `assembleRelease` 会产出**装不上**的 `app-release-unsigned.apk` | ✅ 已补：无 keystore 时自动回落 debug 签名 |
| 应用图标（自适应） | 部分桌面 launcher 上图标可能不显示 | ⚠️ 用的是矢量 drawable，非 `mipmap-anydpi` 自适应图标。不影响安装和运行 |

> 三个缺口里只有「构建环境」是真门槛，而且它不在仓库里、在你机器上。

---

## 2. 构建

### 2.1 路线 A：GitHub 云端编译（推荐，本机零安装）

仓库已配好 `.github/workflows/build-apk.yml`。

```bash
# 1) 代码推上去（已推过就跳过）
git push -u origin main

# 2) 打个 tag 触发编译
git tag v1.0.0
git push origin v1.0.0
```

等 3~5 分钟 → 仓库 **Actions** 页看到「构建安卓中转 APK」变绿 → **Releases** 页附件里就有 APK。

不想打 tag：Actions → 左侧选该工作流 → 右上 `Run workflow` → 跑完在页面底部 **Artifacts** 下载（需登录，保留 90 天）。

云端用的是 `gradle -p android-relay assembleDebug assembleRelease`，绕开缺失的 wrapper 脚本。

### 2.2 路线 B：本机一键脚本（推荐，云端之外的第二选择）

```bash
python tools/build_apk.py
```

它会自己下载 JDK 17、Android SDK（API 34 + build-tools）、Gradle 8.7 装到
**`C:\android-kit`**（纯 ASCII 路径），然后编译，最后把 APK 拷回原工程的输出目录。
不写注册表、不改系统 PATH，不需要 Android Studio。第二次运行会跳过已装好的部分。

```bash
python tools/build_apk.py --check    # 只看工具链是否齐备
python tools/build_apk.py --debug    # 只出 debug 包
```

> **为什么工具装在 `C:\android-kit` 而不是用户目录**：AGP 见到非 ASCII 的项目路径会直接拒绝构建
> （报错 `Your project path contains non-ASCII characters`，见 b.android.com/95744）。
> 中文用户名正是这种情况。脚本因此额外把工程复制到 ASCII 目录再编，
> 项目里也加了 `android.overridePathCheck=true` 兜底。

### 2.3 路线 C：本地命令行（已自行配好 JDK + SDK 时）

```bash
cd android-relay
gradle assembleDebug             # 出 debug 包
gradle assembleRelease           # 出 release 包（无 keystore 时自动退化成 debug 签名）
```

配了自己的签名之后，第二条才会产出真正属于你的正式包：

```bash
make-keystore.bat                # 生成 keystore.jks + signing.properties
gradle assembleRelease
```

### 2.4 路线 D：Android Studio

`File → Open` 选 **`android-relay`**（子目录，不是仓库根）→ 等同步完
→ `Build → Build Bundle(s) / APK(s) → Build APK(s)` → 右下角弹窗 `locate`。

Studio 会自动把缺失的 `gradlew` / `gradle-wrapper.jar` 补齐。

---

## 3. 产物在哪

| 构建 | 产物路径 | 实测大小 |
| --- | --- | --- |
| Debug | `android-relay/app/build/outputs/apk/debug/app-debug.apk` | **20 KB** |
| Release | `android-relay/app/build/outputs/apk/release/app-release.apk` | **19 KB** |

本机用 `tools/build_apk.py` 构建时产物就落在上面这两个路径；
云端构建的产物不落本机，从 Actions 的 Artifacts 或 Release 附件取。

> 为什么这么小：零第三方依赖，没有 androidx、没有 okhttp，图标是矢量 XML。
> 作为对比，一个空的 Hello World + AndroidX 通常就要 1~2 MB。

本机实测用官方 `apksigner` 校验过：

```
apksigner verify --print-certs app-debug.apk   → 退出码 0，签名有效
package: name='com.printinbox.relay' versionCode='1' versionName='1.0'
sdkVersion:'21'          ← 最低 Android 5.0
targetSdkVersion:'34'
uses-permission: INTERNET
uses-permission: WRITE_EXTERNAL_STORAGE maxSdkVersion='28'
application-label:'打印中转'
```

---

## 4. 版本要求

| 项目 | 值 | 含义 |
| --- | --- | --- |
| **minSdk 21** | **Android 5.0** | **最低能装的手机版本**，2015 年之后的机器基本都满足 |
| targetSdk 34 | Android 14 | 按最新行为适配（含明文流量、存储分区） |
| compileSdk 34 | Android 14 | 编译时 API |

签名之外没有用到任何会被商店拒绝的能力，但**这个 APP 的设计目标就是自用时绕过商店**：既不上架，也不需要 Play 服务。

---

## 5. 签名：为什么 debug 包也能直接装

Android 只要求 APK **有签名**，并不要求是你自己的签名。两种包都是签过的：

| 包 | 用什么签名 | 能装吗 | 适合 |
| --- | --- | --- | --- |
| `app-debug.apk` | AGP 自带的公共调试密钥 | ✅ 直接装 | **自用首选** |
| `app-release.apk`（无 keystore） | 自动回落到同一个调试密钥 | ✅ 直接装 | 同上，只是没带调试标记 |
| `app-release.apk`（有 keystore） | 你的私钥 | ✅ | 要长期升级、要分发给别人 |

配置了密钥也别传上去：`signing.properties` 和 `*.jks` 都已在 `.gitignore` 里。
**keystore 丢了就再也出不了同一个 APP 的升级包**，只能让用户卸载重装——记得备份。

---

## 6. 把 APK 弄到手机上

按推荐顺序：

| 方式 | 怎么做 | 注意 |
| --- | --- | --- |
| **手机浏览器直接下** | 手机打开 Release 页 → 点 `app-debug.apk` | 最省事，100KB 秒下 |
| **微信 / QQ 传给自己** | 发给「文件传输助手」，手机端点开 | 微信可能给 `.apk` 改名成 `.apk.1`（见 §7 报错表） |
| **USB 拷** | 手机选「传输文件」，拖进 Download | 之后要在文件管理器里找到它 |
| **adb**（开发者） | `adb install app-debug.apk` | 不用管未知来源授权 |
| 局域网 HTTP | 起个临时静态服务让手机访问 | 需要同一 WiFi |

---

## 7. 安装：未知来源授权与兼容性

### 7.1 「允许安装未知应用」

Android 8 起是**按来源**授权的：你用哪个 APP 打开的 APK，就要给哪个 APP 开权限。

| 品牌 | 入口 |
| --- | --- |
| 小米 / Redmi | 设置 → 隐私保护 → 特殊权限设置 → 安装未知应用 → 选你的浏览器 → 允许 |
| 华为 / 荣耀 | 设置 → 安全 → 更多安全设置 → 安装未知应用；**装不上就先关「纯净模式」/「增强防护」** |
| OPPO / realme | 设置 → 权限与隐私 → 安装未知应用 → 选浏览器 → 允许 |
| vivo / iQOO | 设置 → 应用与权限 → 权限管理 → 安装未知应用 → 选浏览器 → 允许 |
| 三星 | 设置 → 应用程序 → ⋮ → 特殊访问 → 安装未知应用 |
| 原生 Android / Pixel | 设置 → 应用 → 特殊应用权限 → 安装未知应用 |

装的时候弹「是否允许来自此来源的应用」→ 允许即可。
**注意：debug 包在某些华为 / vivo 上会被安全扫描拦一下**，点「继续安装 / 忽略风险」就行（代码全在仓库里，可自行核对）。

### 7.2 报错对照表

| 报错 | 原因 | 处理 |
| --- | --- | --- |
| 「无法解析安装包」 | 下载不完整，或系统低于 Android 5.0 | 重下一次；老机器用 §6 的 USB 方式 |
| 微信传的包装不了 | 微信自动改后缀成 `.apk.1` | 文件管理器里重命名，删掉 `.1` |
| 「与已装应用冲突」 | 之前装过同名不同签名的版本 | 先卸载旧的 |
| 装完桌面没图标 | 图标进了抽屉 | 应用列表里找「打印中转」；现补的自适应图标缺失问题只影响图标显示，不影响运行 |
| 装完分享列表里没有它 | 系统要重建 intent 索引 | 重启一次手机，一般就出现了 |
| 提示应用未通过安全检测 | 未知来源 + 调试签名 | 继续安装即可；介意就用 §2.3 自己生成 keystore 签 |

### 7.3 装完之后

打开「打印中转」→ 填电脑地址（如 `http://192.168.1.23:8760`，**末尾不要加斜杠**）→ 保持「自动传电脑」开启。

> **这一步之前务必重启一次电脑上的打印服务**——旧进程还没加载收件箱接口 `/api/inbox/*`。

完整的使用与验收步骤见 [10-手机端安装](10-手机端安装.md) 第四章。

---

## 8. 一句话总结

- **能不能出 APK**：能。云端三条命令、本机一条 `python tools/build_apk.py`，两条路都已实测跑通。
- **最低能装的手机**：Android 5.0（API 21），实测产物 20 KB。
- **要不要签名**：自用不需要，debug 包直接装；要长期升级就跑一次 `make-keystore.bat`。
- **三个曾经让人白忙的点**：
  1. release 没有签名配置 → 产出装不上的 unsigned 包（已加自动回落）
  2. 中文用户名路径 → AGP 直接拒绝构建（脚本会把工程复制到 ASCII 目录）
  3. `gradle wrapper` 只有 properties 没有 jar → `./gradlew` 不可用（云端和脚本都改用系统 Gradle）
