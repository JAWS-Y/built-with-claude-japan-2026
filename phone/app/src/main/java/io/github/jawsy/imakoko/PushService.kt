package io.github.jawsy.imakoko

import android.annotation.SuppressLint
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/**
 * Pushes from the PC arrive here (data only, so the app draws the notice itself).
 * With firebase_messaging_installation_id_enabled the SDK calls onRegistered, not onNewToken.
 */
@SuppressLint("MissingFirebaseInstanceTokenRefresh")
class PushService : FirebaseMessagingService() {
    /** After register(), and again whenever Firebase changes the address. */
    override fun onRegistered(token: String) {
        if (Prefs.tracking(applicationContext)) Store.writeToken(applicationContext, token)
    }

    override fun onMessageReceived(message: RemoteMessage) {
        Notices.show(applicationContext, message.data)
        // a high-priority push wakes a phone lying still; send its position while it is awake (this runs off the main
        // thread, and the system gives it about 20 s)
        val sent = CountDownLatch(1)
        Tracking.sendNow(applicationContext) { sent.countDown() }
        sent.await(15, TimeUnit.SECONDS)
    }
}
