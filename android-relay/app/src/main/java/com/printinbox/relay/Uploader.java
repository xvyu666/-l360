package com.printinbox.relay;

import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;

/**
 * 上传到电脑打印服务的收件箱接口。
 *
 * 服务端约定（server.py 的 /api/inbox/upload）：
 *   POST，body 就是文件字节，文件名走 X-File-Name 头（URL 编码）。
 *   故意不用 multipart——手机端一个 HttpURLConnection 就够了，零依赖。
 */
public final class Uploader {
    private static final int TIMEOUT = 20 * 1000;
    private static final int MAX_SIZE = 200 * 1024 * 1024; // 和服务端上限一致

    private Uploader() {}

    /** 成功返回 null，失败返回错误信息（直接给 Toast 用）。 */
    public static String upload(String serverBase, InputStream in, long size, String fileName) {
        HttpURLConnection conn = null;
        try {
            URL url = new URL(serverBase + "/api/inbox/upload");
            conn = (HttpURLConnection) url.openConnection();
            conn.setRequestMethod("POST");
            conn.setDoOutput(true);
            conn.setConnectTimeout(TIMEOUT);
            conn.setReadTimeout(TIMEOUT);
            conn.setFixedLengthStreamingMode(size); // 流式直传，不在内存里攒一份
            conn.setRequestProperty("X-File-Name", URLEncoder.encode(fileName, "UTF-8"));
            conn.setRequestProperty("X-Origin", "android-relay");
            conn.setRequestProperty("Content-Type", "application/octet-stream");

            OutputStream out = conn.getOutputStream();
            byte[] buf = new byte[64 * 1024];
            long total = 0;
            int n;
            while ((n = in.read(buf)) > 0) {
                out.write(buf, 0, n);
                total += n;
                if (total > MAX_SIZE) {
                    return "文件超过 200MB，不传了";
                }
            }
            out.close();

            int code = conn.getResponseCode();
            if (code == 200) return null;
            String msg = readErr(conn);
            if (code == 400 && msg.contains("大")) return msg;
            return "电脑返回 " + code + (msg.isEmpty() ? "" : ("：" + msg));
        } catch (java.net.SocketTimeoutException e) {
            return "连不上电脑（超时）";
        } catch (java.io.IOException e) {
            return "网络错误：" + e.getMessage();
        } catch (Exception e) {
            return e.getMessage();
        } finally {
            if (conn != null) conn.disconnect();
        }
    }

    private static String readErr(HttpURLConnection conn) {
        try {
            java.io.InputStream in = conn.getErrorStream();
            if (in == null) return "";
            java.io.ByteArrayOutputStream bos = new java.io.ByteArrayOutputStream();
            byte[] buf = new byte[4096];
            int n;
            while ((n = in.read(buf)) > 0) bos.write(buf, 0, n);
            String s = bos.toString("UTF-8");
            // 服务端回的是 JSON，把 "error":"..." 抠出来就够 Toast 用的
            int k = s.indexOf("\"error\"");
            if (k >= 0) {
                int a = s.indexOf(':', k) + 1;
                int b1 = s.indexOf('"', a);
                int b2 = s.lastIndexOf('"');
                if (b1 > 0 && b2 > b1) return s.substring(b1 + 1, b2);
            }
            return s;
        } catch (Exception e) {
            return "";
        }
    }
}
