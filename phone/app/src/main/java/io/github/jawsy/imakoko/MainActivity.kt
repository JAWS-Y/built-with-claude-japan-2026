package io.github.jawsy.imakoko

import android.Manifest
import android.app.Activity
import android.app.AlertDialog
import android.app.NotificationManager
import android.content.ActivityNotFoundException
import android.content.Intent
import android.content.SharedPreferences
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.view.View
import android.view.WindowInsets
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast
import com.google.firebase.messaging.FirebaseMessaging

class MainActivity : Activity(), ScreenView.Actions {
    private lateinit var setupText: EditText
    private lateinit var setupResult: TextView
    private var message = ""
    private var editing = false // the setup box is open after the setup was saved

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        val root = findViewById<View>(R.id.root)
        if (Build.VERSION.SDK_INT >= 35) {
            val bar = findViewById<View>(R.id.bar)
            val barTop = bar.paddingTop
            root.setOnApplyWindowInsetsListener { v, insets ->
                val bars = insets.getInsets(WindowInsets.Type.systemBars() or WindowInsets.Type.ime())
                // the pale blue bar runs up under the status bar; the rest keeps clear of the bars
                v.setPadding(bars.left, 0, bars.right, bars.bottom)
                bar.setPadding(bar.paddingLeft, barTop + bars.top, bar.paddingRight, bar.paddingBottom)
                insets
            }
        }
        setupText = findViewById(R.id.setup)
        setupResult = findViewById(R.id.setup_result)
        findViewById<View>(R.id.setup_toggle).setOnClickListener {
            editing = !editing
            setupResult.text = ""
            refresh()
        }
        findViewById<Button>(R.id.save_setup).setOnClickListener { saveSetup() }
        findViewById<Button>(R.id.start).setOnClickListener { askNotify() }
        findViewById<Button>(R.id.stop).setOnClickListener { askStop() }
    }

    // Writes from PushService and LocationReceiver show up while the screen is open
    private val changed = SharedPreferences.OnSharedPreferenceChangeListener { _, _ -> refresh() }

    override fun onResume() {
        super.onResume()
        Prefs.watch(this, changed)
        val place = Prefs.lastPlace(this)
        if (place == null || System.currentTimeMillis() - place > FRESH) Tracking.sendNow(applicationContext)
        refresh()
    }

    override fun onPause() {
        Prefs.unwatch(this, changed)
        super.onPause()
    }

    // ------------------------------------------------------------ setup

    private fun saveSetup() {
        val read = Setup.read(setupText.text.toString())
        val s = read.setup
        // a problem stays by the paste box; a saved setup closes it and the next step shows by the start button
        var said = ""
        when {
            s == null -> said = "設定の文が読めません（${read.problem}）。PC の「python3 phone.py pair」が出した文を貼ります。"
            Prefs.tracking(this) -> said = "位置を送っている間は設定を変えられません。先に「見守りを止める」を押します。"
            !App.start(this, s) -> {
                Prefs.saveSetup(this, s)
                message = "保存しました。別の Firebase に切り替えたので、アプリを一度閉じて開き直します。"
            }
            else -> {
                Prefs.saveSetup(this, s)
                setupText.setText("")
                message = "設定を保存しました。「位置を送り始める」を押します。"
            }
        }
        if (said.isEmpty()) editing = false
        setupResult.text = said
        Toast.makeText(this, said.ifEmpty { message }, Toast.LENGTH_LONG).show()
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
        message = "" // the rows say it
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

    // "止める" alone was read as "stop the sound" (94 of 100 simulated people named it in the look round), and
    // stopping also ends the notices, so the button says what it stops and asks once before it does
    private fun askStop() {
        AlertDialog.Builder(this)
            .setTitle(R.string.stop_title)
            .setMessage(R.string.stop_text)
            .setPositiveButton(R.string.stop_yes) { _, _ -> stop() }
            .setNegativeButton(R.string.stop_no, null)
            .show()
    }

    private fun stop() {
        Tracking.stop(this)
        Prefs.setTracking(this, false)
        message = ""
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

    override fun open(url: String) {
        try {
            startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
        } catch (e: ActivityNotFoundException) {
            Toast.makeText(this, "ページを開くアプリがありません。", Toast.LENGTH_LONG).show()
        }
    }

    override fun fix(f: Screen.Fix) {
        startActivity(
            when (f) {
                Screen.Fix.NOTIFICATIONS ->
                    Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS).putExtra(Settings.EXTRA_APP_PACKAGE, packageName)
                Screen.Fix.APP -> Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.fromParts("package", packageName, null))
            },
        )
    }

    private fun refresh() {
        val state = Screen.State(
            project = Prefs.setup(this)?.projectId,
            tracking = Prefs.tracking(this),
            fine = granted(Manifest.permission.ACCESS_FINE_LOCATION),
            coarse = granted(Manifest.permission.ACCESS_COARSE_LOCATION),
            always = granted(Manifest.permission.ACCESS_BACKGROUND_LOCATION),
            notifies = getSystemService(NotificationManager::class.java).areNotificationsEnabled(),
            lastWrite = Prefs.lastWrite(this),
            lastPlace = Prefs.lastPlace(this),
            message = message,
            notice = Prefs.notice(this),
            now = System.currentTimeMillis(),
        )
        ScreenView.draw(findViewById(R.id.root), state, editing, this)
    }

    companion object {
        private const val FRESH = 15 * 60_000L // older than this, opening the app sends a position
        private const val ASK_NOTIFY = 1
        private const val ASK_LOCATION = 2
        private const val ASK_ALWAYS = 3
    }
}
