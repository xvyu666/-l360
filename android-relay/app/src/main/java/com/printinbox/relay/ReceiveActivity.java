package com.printinbox.relay;

import android.app.Activity;
import android.content.ContentResolver;
import android.content.Intent;
import android.database.Cursor;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.provider.OpenableColumns;
import android.widget.Toast;

import java.io.InputStream;
import java.util.ArrayList;

/**
 * 分享接收页：透明无界面，收完东西弹个 Toast 就退。
 *
 * 在这里做两件事：
 *   1. 内容存进 Download/手机打印收件箱/（永远做，文件留在手机上）
 *   2. 如果设置页填了电脑地址 → 自动 POST 到电脑收件箱（真正打通的一步）
 *
 * 网页端没法扫手机文件夹（浏览器沙箱），所以「自动上传」才是主链路；
 * 就算没配电脑地址，文件也在 Download 里，网页的文件选择器照样能选到。
 */
public class ReceiveActivity extends Activity {

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        // Android 9 及以下写公共 Download 要运行时申请权限
        if (Build.VERSION.SDK_INT <= 28
                && checkSelfPermission("android.permission.WRITE_EXTERNAL_STORAGE")
                   != android.content.pm.PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{"android.permission.WRITE_EXTERNAL_STORAGE"}, 1);
            return; // 用户授权后走 onRestart 再收一次
        }

        final Intent intent = getIntent();
        final String action = intent.getAction();
        if (!Intent.ACTION_SEND.equals(action) && !Intent.ACTION_SEND_MULTIPLE.equals(action)) {
            finish();
            return;
        }

        // 分享列表点进来的时候，来源 APP 还在前台等着回调，处理必须放后台线程
        new Thread(new Runnable() {
            @Override public void run() {
                final String result = handle(intent, Intent.ACTION_SEND_MULTIPLE.equals(action));
                runOnUiThread(new Runnable() {
                    @Override public void run() {
                        Toast.makeText(getApplicationContext(), result, Toast.LENGTH_LONG).show();
                        finish();
                    }
                });
            }
        }).start();
    }

    @Override
    protected void onRestart() {
        super.onRestart();
        // 权限申请回来后重试
        if (Build.VERSION.SDK_INT <= 28
                && checkSelfPermission("android.permission.WRITE_EXTERNAL_STORAGE")
                   == android.content.pm.PackageManager.PERMISSION_GRANTED) {
            handle(getIntent(), Intent.ACTION_SEND_MULTIPLE.equals(getIntent().getAction()));
            Toast.makeText(this, "已存入收件箱", Toast.LENGTH_LONG).show();
        }
        finish();
    }

    private String handle(Intent intent, boolean multiple) {
        ArrayList<Uri> uris = new ArrayList<>();
        if (multiple) {
            ArrayList<Uri> list = intent.getParcelableArrayListExtra(Intent.EXTRA_STREAM);
            if (list != null) uris.addAll(list);
        } else {
            Uri u = intent.getParcelableExtra(Intent.EXTRA_STREAM);
            if (u != null) {
                uris.add(u);
            } else if (intent.getStringExtra(Intent.EXTRA_TEXT) != null) {
                // 纯文字分享（浏览器"分享链接"之类）落成 txt
                return saveText(intent.getStringExtra(Intent.EXTRA_TEXT));
            }
        }
        if (uris.isEmpty()) return "没收到能转发的文件";

        int saved = 0, uploaded = 0;
        String lastErr = null;
        String server = Config.server(this);
        boolean auto = Config.autoUpload(this) && !server.isEmpty();

        for (Uri uri : uris) {
            String name = queryName(uri);
            try {
                InputStream in = getContentResolver().openInputStream(uri);
                InboxStore.save(this, in, name);
                saved++;
            } catch (Exception e) {
                lastErr = name + "：" + e.getMessage();
                continue;
            }
            if (auto) {
                try {
                    long size = querySize(uri);
                    InputStream in = getContentResolver().openInputStream(uri);
                    String err = Uploader.upload(server, in, size, name);
                    if (err == null) uploaded++;
                    else lastErr = err;
                } catch (Exception e) {
                    lastErr = e.getMessage();
                }
            }
        }

        StringBuilder sb = new StringBuilder();
        if (saved > 0) {
            sb.append("已存入收件箱 ").append(saved).append(" 个");
            if (auto) {
                sb.append("，已传电脑 ").append(uploaded).append(" 个");
            } else {
                sb.append("（未自动上传：先去中转APP里填电脑地址）");
            }
        }
        if (saved == 0 && lastErr != null) sb.append("保存失败：").append(lastErr);
        else if (lastErr != null && uploaded < saved) sb.append("；").append(lastErr);
        return sb.toString();
    }

    private String saveText(String text) {
        try {
            InputStream in = new java.io.ByteArrayInputStream(text.getBytes("UTF-8"));
            InboxStore.save(this, in, "分享文字.txt");
            String server = Config.server(this);
            if (Config.autoUpload(this) && !server.isEmpty()) {
                InputStream up = new java.io.ByteArrayInputStream(text.getBytes("UTF-8"));
                String err = Uploader.upload(server, up, text.length(), "分享文字.txt");
                return err == null ? "文字已传到电脑" : ("已存收件箱；" + err);
            }
            return "文字已存入收件箱";
        } catch (Exception e) {
            return "保存失败：" + e.getMessage();
        }
    }

    private String queryName(Uri uri) {
        ContentResolver cr = getContentResolver();
        Cursor c = null;
        try {
            c = cr.query(uri, null, null, null, null);
            if (c != null && c.moveToFirst()) {
                int i = c.getColumnIndex(OpenableColumns.DISPLAY_NAME);
                if (i >= 0 && c.getString(i) != null) return c.getString(i);
            }
        } catch (Exception ignore) {
        } finally {
            if (c != null) c.close();
        }
        // 微信的 FileProvider 有时不给 DISPLAY_NAME，按类型兜底
        String type = cr.getType(uri);
        return ("share_" + System.currentTimeMillis()) +
               ("image".equals(type == null ? "" : type.split("/")[0]) ? ".jpg" : ".bin");
    }

    private long querySize(Uri uri) {
        Cursor c = null;
        try {
            c = getContentResolver().query(uri, null, null, null, null);
            if (c != null && c.moveToFirst()) {
                int i = c.getColumnIndex(OpenableColumns.SIZE);
                if (i >= 0 && !c.isNull(i)) return c.getLong(i);
            }
        } catch (Exception ignore) {
        } finally {
            if (c != null) c.close();
        }
        return -1;
    }
}
