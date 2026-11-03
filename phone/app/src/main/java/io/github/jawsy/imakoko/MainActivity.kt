package io.github.jawsy.imakoko

import android.Manifest
import android.app.Activity
import android.app.AlertDialog
import android.app.NotificationManager
import android.content.Intent
import android.content.SharedPreferences
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.view.View
import android.view.WindowInsets
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import com.google.firebase.messaging.FirebaseMessaging
import org.json.JSONArray
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

class MainActivity : Activity() {
    private lateinit var status: TextView
    private lateinit var setupText: EditText
    private var message = ""

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        val root = findViewById<View>(R.id.root)
        if (Build.VERSION.SDK_INT >= 35) {
            root.setOnApplyWindowInsetsListener { v, insets ->
                val bars = insets.getInsets(WindowInsets.Type.systemBars() or WindowInsets.Type.ime())
                v.setPadding(bars.left, bars.top, bars.right, bars.bottom)
                insets
            }
        }
        status = findViewById(R.id.status)
        setupText = findViewById(R.id.setup)
        findViewById<Button>(R.id.save_setup).setOnClickListener { saveSetup() }
        findViewById<Button>(R.id.start).setOnClickListener { askNotify() }
        findViewById<Button>(R.id.stop).setOnClickListener { stop() }
    }

    // Writes from PushService and LocationReceiver show up while the screen is open
    private val changed = SharedPreferences.OnSharedPreferenceChangeListener { _, _ -> refresh() }

    override fun onResume() {
        super.onResume()
        Prefs.watch(this, changed)
        refresh()
    }

    override fun onPause() {
        Prefs.unwatch(this, changed)
        super.onPause()
    }

    // ------------------------------------------------------------ setup

    private fun saveSetup() {
        val s = Setup.parse(setupText.text.toString())
        message = when {
            s == null -> "設定の文が読めません。PC の「python3 phone.py pair」が出した 1 行をそのまま貼ります。"
            Prefs.tracking(this) -> "位置を送っている間は設定を変えられません。先に「止める」を押します。"
            !App.start(this, s) -> {
                Prefs.saveSetup(this, s)
                "保存しました。別の Firebase に切り替えたので、アプリを一度閉じて開き直します。"
            }
            else -> {
                Prefs.saveSetup(this, s)
                setupText.setText("")
                "設定を保存しました。"
            }
        }
        refresh()
    }

    // ------------------------------------------------------------ start: notifications → location → always

    private fun granted(p: String) = checkSelfPermission(p) == PackageManager.PERMISSION_GRANTED

    private fun askNotify() {
        val s = Prefs.setup(this)
        if (s == null) {
            message = "先に設定を保存します。"
            refresh()
            return
        }
        if (!App.start(this, s)) {
            message = "アプリを一度閉じて開き直します（Firebase の切り替え）。"
            refresh()
            return
        }
        if (Build.VERSION.SDK_INT >= 33 && !granted(Manifest.permission.POST_NOTIFICATIONS)) {
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), ASK_NOTIFY)
        } else {
            askLocation()
        }
    }

    private fun askLocation() {
        if (granted(Manifest.permission.ACCESS_FINE_LOCATION) || granted(Manifest.permission.ACCESS_COARSE_LOCATION)) {
            askAlways()
        } else {
            requestPermissions(
                arrayOf(Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION),
                ASK_LOCATION,
            )
        }
    }

    private fun askAlways() {
        if (granted(Manifest.permission.ACCESS_BACKGROUND_LOCATION)) {
            begin()
            return
        }
        AlertDialog.Builder(this)
            .setTitle(R.string.background_title)
            .setMessage(R.string.background_text)
            .setPositiveButton(R.string.next) { _, _ ->
                requestPermissions(arrayOf(Manifest.permission.ACCESS_BACKGROUND_LOCATION), ASK_ALWAYS)
            }
            .setNegativeButton(R.string.later) { _, _ -> begin() }
            .show()
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        when (requestCode) {
            ASK_NOTIFY -> askLocation()
            ASK_LOCATION ->
                if (grantResults.any { it == PackageManager.PERMISSION_GRANTED }) {
                    askAlways()
                } else {
                    message = "位置の許可がないので送れません。"
                    refresh()
                }
            ASK_ALWAYS -> begin()
        }
    }

    private fun begin() {
        if (!Tracking.start(this)) {
            message = "位置の許可がないので送れません。"
            refresh()
            return
        }
        Prefs.setTracking(this, true)
        message = "送り始めました。"
        val ctx = applicationContext
        FirebaseMessaging.getInstance().isAutoInitEnabled = true
        // The address arrives in PushService.onRegistered, which writes it
        FirebaseMessaging.getInstance().register().addOnFailureListener {
            message = "通知の宛先を Firebase から受け取れませんでした。"
            refresh()
        }
        Tracking.once(this) { loc ->
            if (loc != null) {
                Background.run { Store.writeLocation(ctx, loc) }
            }
        }
        refresh()
    }

    // ------------------------------------------------------------ stop

    private fun stop() {
        Tracking.stop(this)
        Prefs.setTracking(this, false)
        message = "止めました。"
        val ctx = applicationContext
        val s = Prefs.setup(this)
        if (s != null && App.start(this, s)) {
            FirebaseMessaging.getInstance().isAutoInitEnabled = false
            FirebaseMessaging.getInstance().unregister()
            Background.run { Store.forget(ctx) }
        }
        refresh()
    }

    // ------------------------------------------------------------ screen

    private fun time(ms: Long) = SimpleDateFormat("M/d H:mm", Locale.JAPAN).format(Date(ms))

    private fun refresh() {
        val s = Prefs.setup(this)
        val lines = mutableListOf<String>()
        lines += if (s == null) "設定: まだです" else "設定: 済み（Firebase ${s.projectId}）"
        val fine = granted(Manifest.permission.ACCESS_FINE_LOCATION)
        val coarse = granted(Manifest.permission.ACCESS_COARSE_LOCATION)
        val always = granted(Manifest.permission.ACCESS_BACKGROUND_LOCATION)
        lines += when {
            !fine && !coarse -> "位置の許可: なし"
            !always -> "位置の許可: アプリを使っている間だけ（閉じているときは送れません）"
            else -> "位置の許可: 常に"
        }
        if (coarse && !fine) lines += "位置はおおよそだけです（ハザードマップの区域の当てはめがずれます）"
        val notifies = getSystemService(NotificationManager::class.java).areNotificationsEnabled()
        lines += if (notifies) "通知: 出せます" else "通知: 許可されていません"
        lines += if (Prefs.tracking(this)) "位置を送る: 動いています" else "位置を送る: 止まっています"
        Prefs.lastWrite(this)?.let { (at, result) -> lines += "最後の書き込み: ${time(at)} $result" }
        if (message.isNotEmpty()) lines += message
        status.text = lines.joinToString("\n")
        findViewById<Button>(R.id.start).isEnabled = s != null && !Prefs.tracking(this)
        findViewById<Button>(R.id.stop).isEnabled = Prefs.tracking(this)
        showNotice()
    }

    private fun showNotice() {
        val raw = Prefs.notice(this) ?: return
        val n = try {
            JSONObject(raw)
        } catch (e: Exception) {
            return
        }
        findViewById<TextView>(R.id.notice_title).text = n.optString("title")
        findViewById<TextView>(R.id.notice_body).text = n.optString("body")
        val meta = mutableListOf<String>()
        if (n.has("received")) meta += "届いた時刻 ${time(n.optLong("received"))}"
        if (n.optString("checked").isNotEmpty()) meta += "PC が調べた時刻 ${n.optString("checked")}"
        if (n.optString("demo") == "1") meta += "作りものの警報で試した知らせです"
        findViewById<TextView>(R.id.notice_meta).text = meta.joinToString("\n")

        val box = findViewById<LinearLayout>(R.id.links)
        box.removeAllViews()
        val links = try {
            JSONArray(n.optString("links", "[]"))
        } catch (e: Exception) {
            JSONArray()
        }
        for (i in 0 until links.length()) {
            val pair = links.optJSONArray(i) ?: continue
            val name = pair.optString(0)
            val url = pair.optString(1)
            if (!url.startsWith("https://")) continue
            val b = Button(this)
            b.text = name
            b.isAllCaps = false
            b.setOnClickListener { startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url))) }
            box.addView(b)
        }
    }

    companion object {
        private const val ASK_NOTIFY = 1
        private const val ASK_LOCATION = 2
        private const val ASK_ALWAYS = 3
    }
}
