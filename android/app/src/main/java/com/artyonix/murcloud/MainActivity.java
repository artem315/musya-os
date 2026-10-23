package com.artyonix.murcloud;

import android.annotation.SuppressLint;
import android.app.DownloadManager;
import android.content.ActivityNotFoundException;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.net.ConnectivityManager;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.net.Uri;
import android.net.http.SslError;
import android.os.Bundle;
import android.os.Environment;
import android.os.Handler;
import android.os.Looper;
import android.view.KeyEvent;
import android.view.View;
import android.view.inputmethod.EditorInfo;
import android.webkit.CookieManager;
import android.webkit.SslErrorHandler;
import android.webkit.URLUtil;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import androidx.activity.OnBackPressedCallback;
import androidx.activity.result.ActivityResultLauncher;
import androidx.activity.result.contract.ActivityResultContracts;
import androidx.appcompat.app.AppCompatActivity;
import androidx.webkit.WebSettingsCompat;
import androidx.webkit.WebViewFeature;

import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * MURCLOUD Android client: a hardened WebView pointed at a real MURCLOUD backend.
 * Everything (desktop, MUR AI, Service Engine, Drive) runs on the server; this app adds
 * a connection screen, HTTPS enforcement, offline handling, file upload and downloads.
 */
public class MainActivity extends AppCompatActivity {
    private static final String PREFS = "murcloud";
    private static final String KEY_SERVER = "server";

    private WebView web;
    private ScrollView connectPanel;
    private LinearLayout offlinePanel;
    private ProgressBar progress;
    private EditText serverInput;
    private CheckBox allowHttp;
    private Button connectBtn;
    private TextView connectError;

    private String server;
    private boolean pageFailed;
    private ValueCallback<Uri[]> fileCallback;
    private ConnectivityManager.NetworkCallback netCallback;
    private final ExecutorService io = Executors.newSingleThreadExecutor();
    private final Handler main = new Handler(Looper.getMainLooper());

    private final ActivityResultLauncher<Intent> filePicker = registerForActivityResult(
            new ActivityResultContracts.StartActivityForResult(), result -> {
                if (fileCallback == null) return;
                Uri[] uris = null;
                Intent data = result.getData();
                if (result.getResultCode() == RESULT_OK && data != null) {
                    if (data.getClipData() != null) {
                        int n = data.getClipData().getItemCount();
                        uris = new Uri[n];
                        for (int i = 0; i < n; i++) uris[i] = data.getClipData().getItemAt(i).getUri();
                    } else if (data.getData() != null) {
                        uris = new Uri[]{data.getData()};
                    }
                }
                fileCallback.onReceiveValue(uris);
                fileCallback = null;
            });

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);
        web = findViewById(R.id.web);
        connectPanel = findViewById(R.id.connect_panel);
        offlinePanel = findViewById(R.id.offline_panel);
        progress = findViewById(R.id.progress);
        serverInput = findViewById(R.id.server_url);
        allowHttp = findViewById(R.id.allow_http);
        connectBtn = findViewById(R.id.connect_btn);
        connectError = findViewById(R.id.connect_error);

        allowHttp.setVisibility(BuildConfig.DEBUG ? View.VISIBLE : View.GONE);
        connectBtn.setOnClickListener(v -> tryConnect());
        serverInput.setOnEditorActionListener((v, actionId, event) -> {
            if (actionId == EditorInfo.IME_ACTION_GO || (event != null && event.getKeyCode() == KeyEvent.KEYCODE_ENTER)) {
                tryConnect();
                return true;
            }
            return false;
        });
        findViewById(R.id.retry_btn).setOnClickListener(v -> retry());
        findViewById(R.id.change_server_btn).setOnClickListener(v -> showConnect(null));

        setupWebView();
        getOnBackPressedDispatcher().addCallback(this, new OnBackPressedCallback(true) {
            @Override
            public void handleOnBackPressed() {
                if (web.getVisibility() == View.VISIBLE && web.canGoBack()) {
                    web.goBack();
                } else {
                    setEnabled(false);
                    getOnBackPressedDispatcher().onBackPressed();
                }
            }
        });

        SharedPreferences prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        server = prefs.getString(KEY_SERVER, null);
        if (server == null) {
            serverInput.setText(BuildConfig.DEFAULT_SERVER);
            showConnect(null);
        } else if (savedInstanceState != null) {
            showWeb();
            web.restoreState(savedInstanceState);
        } else {
            openServer();
        }
        registerNetworkCallback();
    }

    @SuppressLint("SetJavaScriptEnabled")
    private void setupWebView() {
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(true);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        s.setAllowFileAccess(false);
        s.setAllowContentAccess(false);
        s.setJavaScriptCanOpenWindowsAutomatically(false);
        s.setSupportMultipleWindows(false);
        s.setUserAgentString(s.getUserAgentString() + " MURCLOUD-Android/" + BuildConfig.VERSION_NAME);
        if (WebViewFeature.isFeatureSupported(WebViewFeature.SAFE_BROWSING_ENABLE)) {
            WebSettingsCompat.setSafeBrowsingEnabled(s, true);
        }
        CookieManager cookies = CookieManager.getInstance();
        cookies.setAcceptCookie(true);
        cookies.setAcceptThirdPartyCookies(web, false);

        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                String url = request.getUrl().toString();
                if (server != null && ServerUrl.sameOrigin(server, url)) return false;
                // Links to other sites open in the system browser, never inside the authenticated WebView.
                try {
                    startActivity(new Intent(Intent.ACTION_VIEW, request.getUrl()));
                } catch (ActivityNotFoundException ignored) {
                    // no browser installed
                }
                return true;
            }

            @Override
            public void onPageStarted(WebView view, String url, android.graphics.Bitmap favicon) {
                pageFailed = false;
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                progress.setVisibility(View.GONE);
                if (!pageFailed) {
                    showWeb();
                    CookieManager.getInstance().flush();
                }
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame()) {
                    pageFailed = true;
                    showOffline(getString(R.string.offline_text) + "\n(" + error.getDescription() + ")");
                }
            }

            @Override
            public void onReceivedSslError(WebView view, SslErrorHandler handler, SslError error) {
                handler.cancel(); // never proceed with an invalid certificate
                pageFailed = true;
                showOffline(getString(R.string.err_ssl));
            }
        });

        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onProgressChanged(WebView view, int newProgress) {
                progress.setProgress(newProgress);
                progress.setVisibility(newProgress < 100 ? View.VISIBLE : View.GONE);
            }

            @Override
            public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback, FileChooserParams params) {
                if (fileCallback != null) fileCallback.onReceiveValue(null);
                fileCallback = callback;
                Intent pick = new Intent(Intent.ACTION_GET_CONTENT);
                pick.addCategory(Intent.CATEGORY_OPENABLE);
                pick.setType("*/*");
                pick.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, params.getMode() == FileChooserParams.MODE_OPEN_MULTIPLE);
                try {
                    filePicker.launch(Intent.createChooser(pick, null));
                } catch (ActivityNotFoundException e) {
                    fileCallback = null;
                    return false;
                }
                return true;
            }
        });

        web.setDownloadListener((url, userAgent, contentDisposition, mimeType, contentLength) -> {
            if (server == null || !ServerUrl.sameOrigin(server, url)) return;
            String name = URLUtil.guessFileName(url, contentDisposition, mimeType);
            DownloadManager.Request req = new DownloadManager.Request(Uri.parse(url));
            // The session cookie authorises the download; it is sent only to the MURCLOUD origin.
            String cookie = CookieManager.getInstance().getCookie(url);
            if (cookie != null) req.addRequestHeader("Cookie", cookie);
            req.addRequestHeader("User-Agent", userAgent);
            req.setMimeType(mimeType);
            req.setTitle(name);
            req.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
            req.setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, "MURCLOUD/" + name);
            DownloadManager dm = (DownloadManager) getSystemService(Context.DOWNLOAD_SERVICE);
            if (dm != null) {
                dm.enqueue(req);
                Toast.makeText(this, getString(R.string.download_started, name), Toast.LENGTH_SHORT).show();
            }
        });
    }

    // ---------------------------------------------------------------- screens
    private void showConnect(String error) {
        web.setVisibility(View.GONE);
        offlinePanel.setVisibility(View.GONE);
        connectPanel.setVisibility(View.VISIBLE);
        if (server != null && serverInput.getText().length() == 0) serverInput.setText(server);
        connectBtn.setEnabled(true);
        connectBtn.setText(R.string.connect);
        connectError.setVisibility(error == null ? View.GONE : View.VISIBLE);
        if (error != null) connectError.setText(error);
    }

    private void showWeb() {
        connectPanel.setVisibility(View.GONE);
        offlinePanel.setVisibility(View.GONE);
        web.setVisibility(View.VISIBLE);
    }

    private void showOffline(String text) {
        connectPanel.setVisibility(View.GONE);
        web.setVisibility(View.GONE);
        offlinePanel.setVisibility(View.VISIBLE);
        ((TextView) findViewById(R.id.offline_text)).setText(text);
    }

    // ---------------------------------------------------------------- connect
    private void tryConnect() {
        String url;
        try {
            url = ServerUrl.normalize(serverInput.getText().toString(), BuildConfig.DEBUG && allowHttp.isChecked());
        } catch (ServerUrl.Invalid e) {
            showConnect(getString(e.needsHttps ? R.string.err_https : R.string.err_url));
            return;
        }
        connectBtn.setEnabled(false);
        connectBtn.setText(R.string.connecting);
        connectError.setVisibility(View.GONE);
        final String candidate = url;
        io.execute(() -> {
            String problem = checkHealth(candidate);
            main.post(() -> {
                if (problem != null) {
                    showConnect(problem);
                    return;
                }
                server = candidate;
                getSharedPreferences(PREFS, MODE_PRIVATE).edit().putString(KEY_SERVER, server).apply();
                openServer();
            });
        });
    }

    /** Returns null when {@code base}/api/health answers like a MURCLOUD backend, else an error text. */
    private String checkHealth(String base) {
        HttpURLConnection c = null;
        try {
            c = (HttpURLConnection) new URL(base + "/api/health").openConnection();
            c.setConnectTimeout(8000);
            c.setReadTimeout(8000);
            c.setInstanceFollowRedirects(false);
            int code = c.getResponseCode();
            if (code != 200) return getString(R.string.err_not_murcloud) + " (HTTP " + code + ")";
            StringBuilder sb = new StringBuilder();
            try (BufferedReader r = new BufferedReader(new InputStreamReader(c.getInputStream(), StandardCharsets.UTF_8))) {
                String line;
                while ((line = r.readLine()) != null && sb.length() < 4096) sb.append(line);
            }
            JSONObject json = new JSONObject(sb.toString());
            if (!json.optBoolean("ok") || !json.has("version")) return getString(R.string.err_not_murcloud);
            return null;
        } catch (javax.net.ssl.SSLException e) {
            return getString(R.string.err_ssl);
        } catch (Exception e) {
            return getString(R.string.offline_text) + " (" + e.getClass().getSimpleName() + ")";
        } finally {
            if (c != null) c.disconnect();
        }
    }

    private void openServer() {
        if (!isOnline()) {
            showOffline(getString(R.string.offline_text));
            return;
        }
        progress.setVisibility(View.VISIBLE);
        web.loadUrl(server + "/");
    }

    private void retry() {
        if (server == null) {
            showConnect(null);
        } else if (web.getUrl() != null && !pageFailed) {
            showWeb();
            web.reload();
        } else {
            openServer();
        }
    }

    // ---------------------------------------------------------------- network
    private boolean isOnline() {
        ConnectivityManager cm = (ConnectivityManager) getSystemService(Context.CONNECTIVITY_SERVICE);
        if (cm == null) return true;
        Network n = cm.getActiveNetwork();
        NetworkCapabilities caps = n == null ? null : cm.getNetworkCapabilities(n);
        return caps != null && caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET);
    }

    private void registerNetworkCallback() {
        ConnectivityManager cm = (ConnectivityManager) getSystemService(Context.CONNECTIVITY_SERVICE);
        if (cm == null) return;
        netCallback = new ConnectivityManager.NetworkCallback() {
            @Override
            public void onAvailable(Network network) {
                main.post(() -> {
                    if (offlinePanel.getVisibility() == View.VISIBLE && server != null) openServer();
                });
            }

            @Override
            public void onLost(Network network) {
                main.post(() -> {
                    if (!isOnline() && web.getVisibility() == View.VISIBLE) {
                        Toast.makeText(MainActivity.this, R.string.offline_title, Toast.LENGTH_SHORT).show();
                    }
                });
            }
        };
        cm.registerDefaultNetworkCallback(netCallback);
    }

    @Override
    protected void onSaveInstanceState(Bundle outState) {
        super.onSaveInstanceState(outState);
        web.saveState(outState);
    }

    @Override
    protected void onPause() {
        super.onPause();
        CookieManager.getInstance().flush();
    }

    @Override
    protected void onDestroy() {
        ConnectivityManager cm = (ConnectivityManager) getSystemService(Context.CONNECTIVITY_SERVICE);
        if (cm != null && netCallback != null) cm.unregisterNetworkCallback(netCallback);
        io.shutdownNow();
        web.destroy();
        super.onDestroy();
    }
}
