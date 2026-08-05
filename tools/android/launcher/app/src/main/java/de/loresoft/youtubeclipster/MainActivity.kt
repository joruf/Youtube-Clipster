package de.loresoft.youtubeclipster

import android.annotation.SuppressLint
import android.app.Activity
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.View
import android.webkit.JavascriptInterface
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.FrameLayout
import android.widget.TextView
import java.io.File

/**
 * Standalone Clipster launcher for the phone.
 *
 * The Python program runs inside Termux on this device. This activity wakes
 * Termux, starts [clipster-start], and shows the local web UI. Quit / Beenden
 * (and the system Back key) stop the backend again so nothing keeps running
 * in the background. No PC is needed after the one-time USB install.
 */
class MainActivity : Activity() {

    private lateinit var root: FrameLayout
    private lateinit var webView: WebView
    private lateinit var placeholder: TextView
    private val handler = Handler(Looper.getMainLooper())
    private var openUrl: String? = null
    private var attempts = 0
    private var loadedOk = false
    private var stopping = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        root = FrameLayout(this)
        webView = WebView(this)
        placeholder = TextView(this).apply {
            gravity = Gravity.CENTER
            text = getString(R.string.starting_hint)
            setPadding(48, 48, 48, 48)
        }
        root.addView(
            webView,
            FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT,
            ),
        )
        root.addView(
            placeholder,
            FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT,
            ),
        )
        webView.visibility = View.GONE
        setContentView(root)

        setupWebView()
        urlFromIntent(intent)?.let { persistUrl(it) }
        openUrl = resolveUrl(intent)
        wakeTermuxThenStart()
        scheduleAttempt()
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun setupWebView() {
        webView.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            mixedContentMode = WebSettings.MIXED_CONTENT_ALWAYS_ALLOW
            loadWithOverviewMode = true
            useWideViewPort = true
        }
        webView.addJavascriptInterface(ClipsterBridge(), "ClipsterBridge")
        webView.webViewClient = object : WebViewClient() {
            override fun onPageFinished(view: WebView?, url: String?) {
                if (loadedOk) return
                val title = view?.title.orEmpty()
                if (title.contains("nicht verfügbar", ignoreCase = true)
                    || title.contains("not available", ignoreCase = true)
                    || title.contains("webpage not available", ignoreCase = true)
                ) {
                    retrySoon()
                    return
                }
                loadedOk = true
                placeholder.visibility = View.GONE
                webView.visibility = View.VISIBLE
            }

            override fun onReceivedError(
                view: WebView?,
                request: WebResourceRequest?,
                error: WebResourceError?,
            ) {
                if (request?.isForMainFrame == true) {
                    retrySoon()
                }
            }
        }
    }

    private fun urlFromIntent(intent: Intent?): String? {
        if (intent == null) return null
        intent.getStringExtra("url")?.trim()?.takeIf { it.isNotEmpty() }?.let { return it }
        intent.getStringExtra(Intent.EXTRA_TEXT)?.trim()?.takeIf { it.isNotEmpty() }?.let { return it }
        intent.data?.toString()?.trim()?.takeIf { it.isNotEmpty() }?.let { return it }
        return null
    }

    private fun resolveUrl(intent: Intent?): String? {
        urlFromIntent(intent)?.let { return it }
        getSharedPreferences(PREFS, MODE_PRIVATE)
            .getString(PREF_OPEN_URL, null)
            ?.trim()
            ?.takeIf { it.isNotEmpty() }
            ?.let { return it }
        openUrlFile()?.readText()?.trim()?.takeIf { it.isNotEmpty() }?.let { return it }
        return null
    }

    private fun openUrlFile(): File? {
        val dir = getExternalFilesDir(null) ?: return null
        val file = File(dir, OPEN_URL_FILE)
        return if (file.isFile) file else null
    }

    private fun persistUrl(url: String) {
        getSharedPreferences(PREFS, MODE_PRIVATE)
            .edit()
            .putString(PREF_OPEN_URL, url)
            .apply()
        val dir = getExternalFilesDir(null) ?: return
        File(dir, OPEN_URL_FILE).writeText(url)
    }

    /**
     * Android 12+/MIUI blocks starting another app's *background* service.
     * Starting Termux's RunCommandService as a *foreground* service is allowed
     * from our visible activity and keeps everything on-device — no PC.
     */
    private fun wakeTermuxThenStart() {
        startClipsterBackend()
        // A second kick shortly after helps when Termux was force-stopped.
        handler.postDelayed({ startClipsterBackend() }, TERMUX_WAKE_MS)
    }

    private fun runTermuxCommand(vararg arguments: String, label: String) {
        val service = Intent().apply {
            setClassName(TERMUX_PACKAGE, TERMUX_RUN_SERVICE)
            action = TERMUX_RUN_ACTION
            putExtra(TERMUX_EXTRA_PATH, TERMUX_BASH)
            putExtra(TERMUX_EXTRA_ARGUMENTS, arguments)
            putExtra(TERMUX_EXTRA_BACKGROUND, true)
            putExtra(TERMUX_EXTRA_WORKDIR, TERMUX_HOME)
            putExtra(TERMUX_EXTRA_LABEL, label)
        }
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                startForegroundService(service)
            } else {
                startService(service)
            }
        } catch (_: Exception) {
            try {
                startService(service)
            } catch (_: Exception) {
                // Permission / Termux missing.
            }
        }
    }

    private fun startClipsterBackend() {
        if (stopping) return
        runTermuxCommand(CLIPSTER_START, label = "Clipster")
    }

    private fun stopClipsterBackend() {
        runTermuxCommand(CLIPSTER_START, "--stop", label = "Clipster stop")
    }

    /**
     * Tear the standalone stack down: stop the Python server and close the UI.
     * Safe to call more than once.
     */
    private fun quitStandalone() {
        if (stopping) return
        stopping = true
        handler.removeCallbacksAndMessages(null)
        stopClipsterBackend()
        finish()
    }

    private fun scheduleAttempt() {
        handler.postDelayed({ attemptLoad() }, FIRST_DELAY_MS)
    }

    private fun retrySoon() {
        if (loadedOk || stopping) return
        handler.postDelayed({ attemptLoad() }, RETRY_DELAY_MS)
    }

    private fun attemptLoad() {
        if (loadedOk || isFinishing || stopping) return
        attempts += 1
        val url = openUrl
        if (url.isNullOrBlank()) {
            placeholder.text = getString(R.string.setup_hint)
            placeholder.visibility = View.VISIBLE
            webView.visibility = View.GONE
            return
        }
        if (attempts > MAX_ATTEMPTS) {
            placeholder.text = getString(R.string.start_failed_hint)
            placeholder.visibility = View.VISIBLE
            webView.visibility = View.GONE
            return
        }
        placeholder.text = getString(R.string.starting_hint)
        placeholder.visibility = View.VISIBLE
        webView.visibility = View.INVISIBLE
        if (attempts == 1 || attempts % 3 == 0) {
            startClipsterBackend()
        }
        webView.loadUrl(url)
    }

    override fun onNewIntent(intent: Intent?) {
        super.onNewIntent(intent)
        setIntent(intent)
    }

    @Deprecated("Deprecated in Java")
    override fun onBackPressed() {
        // Back = Beenden: tear the server down, do not leave it running.
        quitStandalone()
    }

    override fun onDestroy() {
        handler.removeCallbacksAndMessages(null)
        if (isFinishing && !stopping) {
            // Process death / swipe-away from recents should also stop the server.
            stopClipsterBackend()
        }
        webView.destroy()
        super.onDestroy()
    }

    /** Called from the web UI Quit / Beenden button. */
    private inner class ClipsterBridge {
        @JavascriptInterface
        fun quitApp() {
            runOnUiThread { quitStandalone() }
        }
    }

    companion object {
        private const val PREFS = "clipster"
        private const val PREF_OPEN_URL = "open_url"
        private const val OPEN_URL_FILE = "open.url"
        private const val FIRST_DELAY_MS = 2500L
        private const val RETRY_DELAY_MS = 1500L
        private const val TERMUX_WAKE_MS = 800L
        private const val MAX_ATTEMPTS = 20

        private const val TERMUX_PACKAGE = "com.termux"
        private const val TERMUX_RUN_SERVICE = "com.termux.app.RunCommandService"
        private const val TERMUX_RUN_ACTION = "com.termux.RUN_COMMAND"
        private const val TERMUX_EXTRA_PATH = "com.termux.RUN_COMMAND_PATH"
        private const val TERMUX_EXTRA_ARGUMENTS = "com.termux.RUN_COMMAND_ARGUMENTS"
        private const val TERMUX_EXTRA_BACKGROUND = "com.termux.RUN_COMMAND_BACKGROUND"
        private const val TERMUX_EXTRA_WORKDIR = "com.termux.RUN_COMMAND_WORKDIR"
        private const val TERMUX_EXTRA_LABEL = "com.termux.RUN_COMMAND_COMMAND_LABEL"
        private const val TERMUX_BASH = "/data/data/com.termux/files/usr/bin/bash"
        private const val TERMUX_HOME = "/data/data/com.termux/files/home"
        private const val CLIPSTER_START =
            "/data/data/com.termux/files/home/youtube-clipster/tools/android/clipster-start"
    }
}
