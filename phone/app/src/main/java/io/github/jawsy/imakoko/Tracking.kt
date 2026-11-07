package io.github.jawsy.imakoko

import android.annotation.SuppressLint
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.location.Location
import com.google.android.gms.location.LocationRequest
import com.google.android.gms.location.LocationServices
import com.google.android.gms.location.Priority

/**
 * Asks the system for this phone's position now and then, also while the app is closed.
 * The positions go to LocationReceiver, which writes them to Firestore.
 * A phone lying still is woken by the PC's high-priority push, not by this.
 */
object Tracking {
    private const val EVERY = 10 * 60_000L
    private const val AT_MOST = 5 * 60_000L

    private fun intent(ctx: Context): PendingIntent = PendingIntent.getBroadcast(
        ctx,
        0,
        Intent(ctx, LocationReceiver::class.java),
        PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_MUTABLE,
    )

    @SuppressLint("MissingPermission")
    fun start(ctx: Context): Boolean {
        val request = LocationRequest.Builder(Priority.PRIORITY_BALANCED_POWER_ACCURACY, EVERY)
            .setMinUpdateIntervalMillis(AT_MOST)
            .build()
        return try {
            LocationServices.getFusedLocationProviderClient(ctx).requestLocationUpdates(request, intent(ctx))
            true
        } catch (e: SecurityException) {
            false
        }
    }

    fun stop(ctx: Context) {
        LocationServices.getFusedLocationProviderClient(ctx).removeLocationUpdates(intent(ctx))
    }

    /**
     * One position right away, then done() on the write thread. Used after a restart, when a push arrives and when
     * the app opens with an old position: the emulator still showed 見守り中 while its position was 4 hours old.
     */
    fun sendNow(ctx: Context, done: () -> Unit = {}) {
        if (!Prefs.tracking(ctx) || Prefs.setup(ctx) == null) return done()
        once(ctx) { loc ->
            if (loc == null) {
                done()
            } else {
                Background.run {
                    try {
                        Store.writeLocation(ctx, loc)
                    } finally {
                        done()
                    }
                }
            }
        }
    }

    /** One position right away (when the user presses start). */
    @SuppressLint("MissingPermission")
    fun once(ctx: Context, done: (Location?) -> Unit) {
        try {
            LocationServices.getFusedLocationProviderClient(ctx)
                .getCurrentLocation(Priority.PRIORITY_HIGH_ACCURACY, null)
                .addOnCompleteListener { done(if (it.isSuccessful) it.result else null) }
        } catch (e: SecurityException) {
            done(null)
        }
    }
}
