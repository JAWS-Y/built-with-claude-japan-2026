package io.github.jawsy.imakoko

import org.json.JSONArray

/** How a notice looks: the 警戒レベル color and the buttons. Plain code so the unit tests can run it. */
object Look {
    /** The colors of the 警戒レベル（5 black, 4 purple, 3 red, 2 yellow）. null below 2 (no level color). */
    fun color(level: String?): Int? = when (level?.toIntOrNull()) {
        5 -> 0xFF0C000C.toInt()
        4 -> 0xFFAA00AA.toInt()
        3 -> 0xFFFF2800.toInt()
        2 -> 0xFFF2E700.toInt()
        else -> null
    }

    /** At most two [label, url] buttons, https only; anything malformed is left out. */
    fun buttons(json: String?): List<Pair<String, String>> {
        val list = try {
            JSONArray(json ?: "[]")
        } catch (e: Exception) {
            return emptyList()
        }
        val out = mutableListOf<Pair<String, String>>()
        for (i in 0 until list.length()) {
            val pair = list.optJSONArray(i) ?: continue
            val label = pair.optString(0, "").trim()
            val url = pair.optString(1, "")
            if (label.isEmpty() || label.length > 20 || !url.startsWith("https://")) continue
            out.add(label to url)
            if (out.size == 2) break
        }
        return out
    }
}
