package io.github.jawsy.imakoko

import org.json.JSONObject

/**
 * The setup text printed by `python3 phone.py pair` on the PC: which Firebase project to use
 * and the id of this phone's document. Everything ends up in URLs, so only plain characters pass.
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

        fun parse(text: String): Setup? {
            val j = try {
                JSONObject(text.trim())
            } catch (e: Exception) {
                return null
            }
            if (j.optInt("v") != 1) return null
            val s = Setup(
                j.optString("projectId"),
                j.optString("senderId"),
                j.optString("appId"),
                j.optString("apiKey"),
                j.optString("device"),
            )
            val ok = PROJECT.matches(s.projectId) && SENDER.matches(s.senderId) && APP.matches(s.appId) &&
                KEY.matches(s.apiKey) && DEVICE.matches(s.device)
            return if (ok) s else null
        }
    }
}
