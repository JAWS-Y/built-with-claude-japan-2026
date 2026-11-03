package io.github.jawsy.imakoko

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import com.google.android.gms.location.LocationResult

/** Gets the positions Tracking asked for and writes the newest one. */
class LocationReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val ctx = context.applicationContext
        val loc = LocationResult.extractResult(intent)?.lastLocation ?: return
        if (!Prefs.tracking(ctx)) {
            Tracking.stop(ctx)
            return
        }
        val pending = goAsync()
        Background.run {
            try {
                Store.writeLocation(ctx, loc)
            } finally {
                pending.finish()
            }
        }
    }
}
