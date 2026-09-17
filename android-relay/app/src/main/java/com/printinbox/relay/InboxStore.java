package com.printinbox.relay;

import android.content.ContentValues;
import android.content.Context;
import android.net.Uri;
import android.os.Build;
import android.os.Environment;
import android.provider.MediaStore;

import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;

/**
 * 把分享来的内容存进手机固定文件夹：Download/手机打印收件箱/
 *
 * 两条路：
 *   Android 10+ 走 MediaStore（RELATIVE_PATH），不需要任何存储权限；
 *   Android 9-  直接写公共 Download 目录，需要 WRITE_EXTERNAL_STORAGE
 *   （Manifest 里声明了，ReceiveActivity 在老系统上会先申请）。
 *
 * 为什么放 Download：公共目录、文件管理器看得到、Chrome 的文件选择器也能选到——
 * 就算 APP 挂了或者没配电脑地址，文件也不会丢在私有目录里拿不出来。
 */
public final class InboxStore {
    /** 相对 Download 的固定子目录，改这里就等于换了收件箱位置 */
    public static final String SUBDIR = "手机打印收件箱";

    private InboxStore() {}

    public static File legacyDir() {
        File base = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS);
        File dir = new File(base, SUBDIR);
        if (!dir.exists()) dir.mkdirs();
        return dir;
    }

    /** 返回保存后的位置描述（出错抛异常，调用方负责提示）。 */
    public static String save(Context ctx, InputStream in, String displayName) throws Exception {
        String safe = safeName(displayName);
        String name = stamp() + "_" + safe;

        if (Build.VERSION.SDK_INT >= 29) {
            ContentValues cv = new ContentValues();
            cv.put(MediaStore.MediaColumns.DISPLAY_NAME, name);
            cv.put(MediaStore.MediaColumns.MIME_TYPE, guessMime(safe));
            cv.put(MediaStore.MediaColumns.RELATIVE_PATH,
                    Environment.DIRECTORY_DOWNLOADS + "/" + SUBDIR);
            Uri uri = ctx.getContentResolver().insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, cv);
            if (uri == null) throw new Exception("系统拒绝了写入 Download 目录");
            OutputStream out = ctx.getContentResolver().openOutputStream(uri);
            pipe(in, out);
            return SUBDIR + "/" + name;
        } else {
            File dst = new File(legacyDir(), name);
            OutputStream out = new FileOutputStream(dst);
            pipe(in, out);
            return "Download/" + SUBDIR + "/" + name;
        }
    }

    private static void pipe(InputStream in, OutputStream out) throws Exception {
        try {
            byte[] buf = new byte[64 * 1024];
            int n;
            while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
        } finally {
            try { in.close(); } catch (Exception ignore) {}
            try { out.close(); } catch (Exception ignore) {}
        }
    }

    /** 0918-214336 这样的时间前缀：同名文件不互相覆盖，还能看出先后 */
    private static String stamp() {
        java.text.SimpleDateFormat f = new java.text.SimpleDateFormat("MMdd-HHmmss");
        return f.format(new java.util.Date());
    }

    private static String safeName(String s) {
        if (s == null || s.trim().isEmpty()) return "share.bin";
        String n = s.trim().replaceAll("[\\\\/:*?\"<>|]", "_");
        return n.length() > 100 ? n.substring(n.length() - 100) : n;
    }

    private static String guessMime(String name) {
        String n = name.toLowerCase();
        if (n.endsWith(".png")) return "image/png";
        if (n.endsWith(".gif")) return "image/gif";
        if (n.endsWith(".webp")) return "image/webp";
        if (n.endsWith(".pdf")) return "application/pdf";
        if (n.endsWith(".doc")) return "application/msword";
        if (n.endsWith(".docx")) return "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
        if (n.endsWith(".xls")) return "application/vnd.ms-excel";
        if (n.endsWith(".xlsx")) return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
        if (n.endsWith(".ppt")) return "application/vnd.ms-powerpoint";
        if (n.endsWith(".pptx")) return "application/vnd.openxmlformats-officedocument.presentationml.presentation";
        if (n.endsWith(".txt") || n.endsWith(".md")) return "text/plain";
        return "image/jpeg"; // 相册分享来的大头就是 jpg
    }
}
