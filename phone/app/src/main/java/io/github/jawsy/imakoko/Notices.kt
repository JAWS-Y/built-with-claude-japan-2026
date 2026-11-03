package io.github.jawsy.imakoko

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import org.json.JSONObject

/** Draws the notice the PC sent: the official facts for this place and the links to their sources. */
object Notices {
    private const val CHANNEL = "official"

    fun channel(ctx: Context) {
        val c = NotificationChannel(CHANNEL, ctx.getString(R.string.channel), NotificationManager.IMPORTANCE_HIGH)
        c.description = ctx.getString(R.string.channel_about)
        ctx.getSystemService(NotificationManager::class.java).createNotificationChannel(c)
    }

    fun show(ctx: Context, data: Map<String, String>) {
        val title = data["title"] ?: return
        val body = data["body"] ?: ""
        val saved = JSONObject()
        for ((k, v) in data) saved.put(k, v)
        saved.put("received", System.currentTimeMillis())
        Prefs.saveNotice(ctx, saved.toString())

        val nm = ctx.getSystemService(NotificationManager::class.java)
        if (!nm.areNotificationsEnabled()) return
        val open = PendingIntent.getActivity(
            ctx,
            0,
            Intent(ctx, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val n = Notification.Builder(ctx, CHANNEL)
            .setSmallIcon(R.drawable.ic_notice)
            .setContentTitle(title)
            .setContentText(body.lineSequence().firstOrNull() ?: "")
            .setStyle(Notification.BigTextStyle().bigText(body))
            .setContentIntent(open)
            .setAutoCancel(true)
            .setShowWhen(true)
            .build()
        nm.notify(1, n)
    }
}
