package io.github.jawsy.imakoko

import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage

/** Pushes from the PC arrive here (data only, so the app draws the notice itself). */
class PushService : FirebaseMessagingService() {
    override fun onNewToken(token: String) {
        if (Prefs.tracking(applicationContext)) Store.writeToken(applicationContext, token)
    }

    override fun onMessageReceived(message: RemoteMessage) {
        Notices.show(applicationContext, message.data)
    }
}
