package io.github.jawsy.imakoko

import android.content.Context
import android.content.SharedPreferences

/** What the app keeps on the phone. No positions are kept here. */
object Prefs {
    private fun p(ctx: Context): SharedPreferences =
        ctx.getSharedPreferences("imakoko", Context.MODE_PRIVATE)

    fun setup(ctx: Context): Setup? = p(ctx).getString("setup", null)?.let { Setup.parse(it) }
    fun saveSetup(ctx: Context, s: Setup) = p(ctx).edit().putString("setup", s.toJson()).apply()

    fun tracking(ctx: Context): Boolean = p(ctx).getBoolean("tracking", false)
    fun setTracking(ctx: Context, on: Boolean) = p(ctx).edit().putBoolean("tracking", on).apply()

    /** When the last write to Firestore happened and how it went. */
    fun lastWrite(ctx: Context): Pair<Long, String>? {
        val at = p(ctx).getLong("write_at", 0L)
        return if (at == 0L) null else at to (p(ctx).getString("write_result", "") ?: "")
    }
    fun saveWrite(ctx: Context, at: Long, result: String) =
        p(ctx).edit().putLong("write_at", at).putString("write_result", result).apply()

    fun notice(ctx: Context): String? = p(ctx).getString("notice", null)
    fun saveNotice(ctx: Context, json: String) = p(ctx).edit().putString("notice", json).apply()
}
