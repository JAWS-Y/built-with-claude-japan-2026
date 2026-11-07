package io.github.jawsy.imakoko

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/** Location requests end with a restart or an app update; ask again, and send one position, if sending was on. */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED && intent.action != Intent.ACTION_MY_PACKAGE_REPLACED) return
        val ctx = context.applicationContext
        if (!Prefs.tracking(ctx) || Prefs.setup(ctx) == null) return
        Tracking.start(ctx)
        // the requests above wait for the next fix; send one now so the PC does not use the position from before
        val pending = goAsync()
        Tracking.sendNow(ctx) { pending.finish() }
    }
}
