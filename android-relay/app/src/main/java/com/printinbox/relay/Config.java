package com.printinbox.relay;

import android.content.Context;
import android.content.SharedPreferences;

/** 只存两个东西：电脑地址、要不要自动上传。 */
public final class Config {
    private static final String SP = "relay";
    private static final String K_SERVER = "server";
    private static final String K_AUTO = "auto";

    private Config() {}

    private static SharedPreferences sp(Context c) {
        return c.getSharedPreferences(SP, Context.MODE_PRIVATE);
    }

    /** 形如 http://192.168.1.10:8760，末尾不带斜杠 */
    public static String server(Context c) {
        return trim(sp(c).getString(K_SERVER, ""));
    }

    public static void setServer(Context c, String s) {
        sp(c).edit().putString(K_SERVER, trim(s)).apply();
    }

    public static boolean autoUpload(Context c) {
        return sp(c).getBoolean(K_AUTO, true);
    }

    public static void setAutoUpload(Context c, boolean b) {
        sp(c).edit().putBoolean(K_AUTO, b).apply();
    }

    /** 去掉末尾斜杠、空白；没有协议头就补 http:// */
    private static String trim(String s) {
        if (s == null) return "";
        s = s.trim();
        if (s.isEmpty()) return "";
        if (!s.startsWith("http://") && !s.startsWith("https://")) s = "http://" + s;
        while (s.endsWith("/")) s = s.substring(0, s.length() - 1);
        return s;
    }
}
