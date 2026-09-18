@echo off
REM 生成安卓 APK 签名，并自动写好 signing.properties
REM 需要 JDK 的 keytool。没有 JDK 也能出包（会退回 debug 签名），见 docs/11
chcp 65001 >nul
setlocal

set KS=keystore.jks
set ALIAS=printrelay
set STOREPASS=printrelay123
set KEYPASS=printrelay123

cd /d "%~dp0"

where keytool >nul 2>nul
if errorlevel 1 (
  echo [X] 没找到 keytool，请先安装 JDK 17 并把它加进 PATH
  echo     没有 JDK 也能出能装的包：gradle assembleRelease 会退回 debug 签名
  exit /b 1
)

if exist "%KS%" (
  echo 已存在 %KS%，跳过生成这一步
) else (
  echo 正在生成 %KS% ...
  keytool -genkeypair -v -keystore "%KS%" -alias %ALIAS% -keyalg RSA -keysize 2048 -validity 10950 ^
    -storepass %STOREPASS% -keypass %KEYPASS% ^
    -dname "CN=Print Relay, OU=Personal, O=PrintRelay, C=CN"
  if errorlevel 1 exit /b 1
)

(
echo RELEASE_STORE_FILE=%KS%
echo RELEASE_STORE_PASSWORD=%STOREPASS%
echo RELEASE_KEY_ALIAS=%ALIAS%
echo RELEASE_KEY_PASSWORD=%KEYPASS%
) > signing.properties

echo.
echo 完成：已生成 %KS%，配置写入 signing.properties
echo 这两个文件都在 .gitignore 里，不会进版本库。keystore 丢了就出不了升级包，记得备份。
endlocal
