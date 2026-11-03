package io.github.jawsy.imakoko

import android.app.Application
import android.content.Context
import com.google.firebase.FirebaseApp
import com.google.firebase.FirebaseOptions

class App : Application() {
    override fun onCreate() {
        super.onCreate()
        Notices.channel(this)
        Prefs.setup(this)?.let { start(this, it) }
    }

    companion object {
        /**
         * Starts Firebase from the setup text (there is no google-services.json in the app).
         * Returns false when Firebase already runs with another project: the app has to be reopened.
         */
        fun start(ctx: Context, s: Setup): Boolean {
            val running = FirebaseApp.getApps(ctx).firstOrNull { it.name == FirebaseApp.DEFAULT_APP_NAME }
            if (running != null) return running.options.projectId == s.projectId
            val options = FirebaseOptions.Builder()
                .setProjectId(s.projectId)
                .setGcmSenderId(s.senderId)
                .setApplicationId(s.appId)
                .setApiKey(s.apiKey)
                .build()
            FirebaseApp.initializeApp(ctx, options)
            return true
        }
    }
}
