package io.github.jawsy.imakoko

import org.json.JSONArray

/** How a notice looks: the 警戒レベル color, the buttons and the line breaks. Plain code so the unit tests can run it. */
object Look {
    private const val JOIN = '\u2060' // WORD JOINER: no line break here
    private const val SHORT = 10

    private fun wide(c: Char) = c in '\u3000'..'\u9fff' || c in '\uff00'..'\uffef'

    private fun plain(c: Char) = !c.isSurrogate() && c != '\u200d' && c !in '\ufe00'..'\ufe0f' &&
        Character.getType(c) != Character.NON_SPACING_MARK.toInt()

    /**
     * Keeps each short word on one line, so a line breaks at the spaces the PC put between words
     * (the emulator showed 「初 / 台アンダー」 and 「みず / が」). A word longer than SHORT may still break.
     */
    fun glue(text: String): String = text.split('\n').joinToString("\n") { line ->
        val out = StringBuilder()
        var word = 0
        for ((i, c) in line.withIndex()) {
            if (c == ' ' || c == '\u3000') {
                word = i + 1
            } else if (i > word) {
                val before = line[i - 1]
                val end = line.indexOfAny(charArrayOf(' ', '\u3000'), i).let { if (it < 0) line.length else it }
                if (end - word <= SHORT && (wide(before) || wide(c)) && plain(before) && plain(c)) out.append(JOIN)
            }
            out.append(c)
        }
        out.toString()
    }

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
