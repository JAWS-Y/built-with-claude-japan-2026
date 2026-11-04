package io.github.jawsy.imakoko

import java.text.Normalizer
import org.json.JSONObject

/**
 * The setup text printed by `python3 phone.py pair` on the PC: which Firebase project to use
 * and the id of this phone's document. Everything ends up in URLs, so only plain characters pass.
 *
 * Copying the text through a document or chat app on the phone can add line breaks, spaces, invisible
 * marks, curly quotes or words around it. None of the values has spaces, so those are dropped before reading.
 */
data class Setup(
    val projectId: String,
    val senderId: String,
    val appId: String,
    val apiKey: String,
    val device: String,
) {
    fun toJson(): String = JSONObject()
        .put("v", 1)
        .put("projectId", projectId)
        .put("senderId", senderId)
        .put("appId", appId)
        .put("apiKey", apiKey)
        .put("device", device)
        .toString()

    companion object {
        private val PROJECT = Regex("[a-z][a-z0-9-]{4,28}[a-z0-9]")
        private val SENDER = Regex("[0-9]{1,20}")
        private val APP = Regex("1:[0-9]{1,20}:android:[0-9a-f]{1,40}")
        private val KEY = Regex("[A-Za-z0-9_-]{20,64}")
        private val DEVICE = Regex("[A-Za-z0-9_-]{40,64}")

        /** The setup, or why the text is not one. The reason names the field, never its value. */
        class Read(val setup: Setup?, val problem: String?)

        fun parse(text: String): Setup? = read(text).setup

        fun read(text: String): Read {
            val flat = Normalizer.normalize(text, Normalizer.Form.NFKC)
                .replace(Regex("[\u201C\u201D\u201E\u201F\u2033]"), "\"")
                .filterNot { it.isWhitespace() || Character.getType(it) == Character.FORMAT.toInt() }
            val start = flat.indexOf('{')
            val end = flat.lastIndexOf('}')
            if (start < 0 || end < start) return Read(null, "「{」から「}」までの文が見つかりません")
            val j = try {
                JSONObject(flat.substring(start, end + 1))
            } catch (e: Exception) {
                return Read(null, "途中が欠けているか、別の文です")
            }
            if (j.optInt("v") != 1) return Read(null, "v が 1 ではありません")
            val s = Setup(
                j.optString("projectId"),
                j.optString("senderId"),
                j.optString("appId"),
                j.optString("apiKey"),
                j.optString("device"),
            )
            val bad = listOf(
                "projectId" to PROJECT.matches(s.projectId),
                "senderId" to SENDER.matches(s.senderId),
                "appId" to APP.matches(s.appId),
                "apiKey" to KEY.matches(s.apiKey),
                "device" to DEVICE.matches(s.device),
            ).filterNot { it.second }.map { it.first }
            return if (bad.isEmpty()) Read(s, null) else Read(null, bad.joinToString("・") + " の形が違います")
        }
    }
}
