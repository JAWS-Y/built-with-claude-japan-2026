package io.github.jawsy.imakoko

import android.annotation.SuppressLint
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage

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
    }
}
