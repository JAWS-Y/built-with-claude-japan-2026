package io.github.jawsy.imakoko

import android.content.Context
import android.location.Location
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.ProtocolException
import java.net.URL
import java.net.URLEncoder

/**
 * Writes this phone's document in Firestore over REST. The rules let anyone with the id write it
 * and nobody read it; the PC reads it with its own service account.
 * Each write names its fields in updateMask, so a position never wipes the push token and back.
 */
object Store {
    private const val BASE = "https://firestore.googleapis.com/v1"

    fun url(s: Setup, fields: List<String>): String {
        val mask = fields.joinToString("") { "&updateMask.fieldPaths=" + URLEncoder.encode(it, "UTF-8") }
        return "$BASE/projects/${s.projectId}/databases/(default)/documents/devices/${s.device}?key=${s.apiKey}$mask"
    }

    fun locationBody(lat: Double, lon: Double, accuracy: Double, at: Long): JSONObject = JSONObject().put(
        "fields",
        JSONObject()
            .put("lat", JSONObject().put("doubleValue", lat))
            .put("lon", JSONObject().put("doubleValue", lon))
            .put("accuracy", JSONObject().put("doubleValue", accuracy))
            .put("at", JSONObject().put("integerValue", at.toString())),
    )

    fun tokenBody(token: String): JSONObject =
        JSONObject().put("fields", JSONObject().put("token", JSONObject().put("stringValue", token)))

    /** Runs on a background thread. */
    fun writeLocation(ctx: Context, loc: Location): Boolean {
        val s = Prefs.setup(ctx) ?: return false
        val at = loc.time / 1000
        val body = locationBody(loc.latitude, loc.longitude, loc.accuracy.toDouble(), at)
        return record(ctx, "位置", send("PATCH", url(s, listOf("lat", "lon", "accuracy", "at")), body))
    }

    /** Runs on a background thread. */
    fun writeToken(ctx: Context, token: String): Boolean {
        val s = Prefs.setup(ctx) ?: return false
        return record(ctx, "通知の宛先", send("PATCH", url(s, listOf("token")), tokenBody(token)))
    }

    /** Runs on a background thread. Removes the whole document (position and token). */
    fun forget(ctx: Context): Boolean {
        val s = Prefs.setup(ctx) ?: return false
        val code = send("DELETE", url(s, emptyList()), null)
        return record(ctx, "消去", if (code == 404) 200 else code)
    }

    private fun record(ctx: Context, what: String, code: Int): Boolean {
        val ok = code in 200..299
        val result = if (ok) "$what: 送れました" else if (code < 0) "$what: つながりません" else "$what: 失敗（HTTP $code）"
        Prefs.saveWrite(ctx, System.currentTimeMillis(), result)
        return ok
    }

    private fun send(method: String, url: String, body: JSONObject?): Int {
        val c = URL(url).openConnection() as HttpURLConnection
        return try {
            try {
                c.requestMethod = method
            } catch (e: ProtocolException) {
                c.requestMethod = "POST"
                c.setRequestProperty("X-HTTP-Method-Override", method)
            }
            c.connectTimeout = 15_000
            c.readTimeout = 15_000
            if (body != null) {
                val bytes = body.toString().toByteArray()
                c.doOutput = true
                c.setRequestProperty("Content-Type", "application/json; charset=utf-8")
                c.setFixedLengthStreamingMode(bytes.size)
                c.outputStream.use { it.write(bytes) }
            }
            c.responseCode
        } catch (e: java.io.IOException) {
            -1
        } finally {
            c.disconnect()
        }
    }
}
