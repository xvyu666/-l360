package com.printinbox.relay;

import android.app.Activity;
import android.app.DownloadManager;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.view.View;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.Switch;
import android.widget.TextView;
import android.widget.Toast;

/**
 * 设置页。就三样东西：电脑地址、自动上传开关、看收件箱。
 *
 * 页面是代码拼的，不用 XML 布局文件——这个 APP 一共一个输入框一个开关，
 * 引入布局解析器反而多一层出错机会。（学校/办公电脑装的 Android Studio
 * 版本五花八门，少一个 XML 就少一个解析坑。）
 */
public class MainActivity extends Activity {

    private EditText server;
    private Switch auto;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        int pad = (int) (16 * getResources().getDisplayMetrics().density);
        root.setPadding(pad, pad, pad, pad);

        TextView title = new TextView(this);
        title.setText("打印中转");
        title.setTextSize(22);
        title.setPadding(0, 0, 0, pad / 2);
        root.addView(title);

        TextView hint = new TextView(this);
        hint.setText("微信 / 浏览器 / 相册 / WPS 里点「分享」，选「打印中转」，"
                + "文件就会存进手机 Download/手机打印收件箱，"
                + "并且自动传到下面的电脑，在打印网页里直接选着打。");
        hint.setTextSize(14);
        hint.setPadding(0, 0, 0, pad);
        root.addView(hint);

        server = new EditText(this);
        server.setHint("电脑地址，如 http://192.168.1.10:8760");
        server.setSingleLine(true);
        server.setText(Config.server(this));
        root.addView(server);

        auto = new Switch(this);
        auto.setText("收到就自动传到电脑");
        auto.setChecked(Config.autoUpload(this));
        auto.setPadding(0, pad / 2, 0, pad / 2);
        root.addView(auto);

        TextView btnOpenBox = new TextView(this);
        btnOpenBox.setText("📂 打开手机收件箱（Download 目录）");
        btnOpenBox.setTextSize(15);
        btnOpenBox.setPadding(0, pad, 0, pad);
        btnOpenBox.setOnClickListener(new View.OnClickListener() {
            @Override public void onClick(View v) {
                try {
                    startActivity(new Intent(DownloadManager.ACTION_VIEW_DOWNLOADS));
                } catch (Exception e) {
                    Toast.makeText(MainActivity.this, "打开失败：" + e.getMessage(), Toast.LENGTH_SHORT).show();
                }
            }
        });
        root.addView(btnOpenBox);

        TextView btnOpenWeb = new TextView(this);
        btnOpenWeb.setText("🌐 打开打印网页");
        btnOpenWeb.setTextSize(15);
        btnOpenWeb.setPadding(0, 0, 0, pad);
        btnOpenWeb.setOnClickListener(new View.OnClickListener() {
            @Override public void onClick(View v) {
                String s = Config.server(MainActivity.this);
                if (s.isEmpty()) {
                    Toast.makeText(MainActivity.this, "先填上面的电脑地址", Toast.LENGTH_SHORT).show();
                    return;
                }
                try {
                    startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(s)));
                } catch (Exception e) {
                    Toast.makeText(MainActivity.this, "打开失败：" + e.getMessage(), Toast.LENGTH_SHORT).show();
                }
            }
        });
        root.addView(btnOpenWeb);

        TextView footer = new TextView(this);
        footer.setText("手机和电脑要在同一个 WiFi。地址不知道的话，看电脑上打印服务窗口显示的网址。");
        footer.setTextSize(12);
        root.addView(footer);

        setContentView(root);
    }

    @Override
    protected void onPause() {
        super.onPause();
        Config.setServer(this, server.getText().toString());
        Config.setAutoUpload(this, auto.isChecked());
    }
}
