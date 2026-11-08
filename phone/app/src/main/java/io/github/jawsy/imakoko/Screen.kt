package io.github.jawsy.imakoko

import org.json.JSONArray
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Locale

/** What the main screen says, from plain state. No Android calls, so the unit tests can run it. */
object Screen {
    enum class Kind { OK, IDLE, WARN, BAD, INFO }

    /** The settings page that puts a problem right. */
    enum class Fix { NOTIFICATIONS, APP }

    /** A line in the 見守り box. */
    data class Row(val text: String, val kind: Kind, val fix: Fix? = null)

    data class State(
        val project: String?, // the Firebase project of the setup; null before the setup is saved
        val tracking: Boolean,
        val fine: Boolean,
        val coarse: Boolean,
        val always: Boolean,
        val notifies: Boolean,
        val lastWrite: Pair<Long, String>?,
        val lastPlace: Long?, // when a position last reached Firestore
        val message: String,
        val notice: String?, // the last notice as saved by Notices.show
        val now: Long,
    )

    /** The last notice: the title in the level color, then the notes and the official pages to open. */
    data class Card(
        val title: String,
        val level: String,
        val head: String,
        val notes: String,
        val pages: List<Pair<String, String>>,
        val checked: String,
    )

    fun chip(s: State): Row = when {
        s.project == null -> Row("設定がまだです", Kind.WARN)
        !s.tracking -> Row("止まっています", Kind.IDLE)
        rows(s).any { it.kind == Kind.BAD } -> Row("うまく動いていません", Kind.BAD)
        stale(s) != null -> Row("位置が古いままです", Kind.WARN)
        else -> Row("見守り中", Kind.OK)
    }

    /** What keeps the notices from arriving, only while the position is being sent. */
    fun problems(s: State): List<Row> {
        if (!s.tracking) return emptyList()
        val out = mutableListOf<Row>()
        if (!s.notifies) out += Row("通知が切れています。知らせが届いても出ません", Kind.BAD, Fix.NOTIFICATIONS)
        if (!s.fine && !s.coarse) {
            out += Row("位置の許可がないので、位置を送れません", Kind.BAD, Fix.APP)
        } else {
            if (!s.always) out += Row("閉じている間は位置を送れません（許可が「常に」ではない）", Kind.WARN, Fix.APP)
            if (!s.fine) out += Row("位置がおおよそです。区域の当てはめがずれます", Kind.WARN, Fix.APP)
        }
        return out
    }

    fun rows(s: State): List<Row> {
        val out = mutableListOf<Row>()
        if (s.project != null) out += if (s.tracking) Row("位置を送っています", Kind.OK) else Row("位置は送っていません", Kind.IDLE)
        s.lastWrite?.let { (at, result) -> out += written(at, result, s.now) }
        stale(s)?.let { out += it }
        out += problems(s)
        if (s.message.isNotEmpty()) out += Row(s.message, Kind.INFO)
        return out
    }

    /** While sending is on, a position older than an hour is said, so 見守り中 never hides it. */
    fun stale(s: State): Row? {
        val at = s.lastPlace ?: return null
        val hours = (s.now - at) / 3_600_000L
        if (!s.tracking || hours < 1) return null
        return Row("最後に位置が届いたのは ${clock(at, s.now)}（$hours 時間前）。PC はその位置で調べます", Kind.WARN, Fix.APP)
    }

    /** Store's "位置: 送れました" / "位置: つながりません" / "位置: 失敗（HTTP 403）" in plain words with the time. */
    fun written(at: Long, result: String, now: Long): Row {
        val time = clock(at, now)
        val what = result.substringBefore(": ")
        val how = result.substringAfter(": ", "")
        val why = how.removePrefix("失敗（").removeSuffix("）")
        return when {
            how.isEmpty() -> Row("$time $result", Kind.INFO)
            what == "消去" && how == "送れました" -> Row("$time に Firebase の位置と通知の宛先を消しました", Kind.OK)
            what == "消去" -> Row("$time Firebase の位置を消せませんでした（$why）", Kind.BAD)
            how == "送れました" -> Row("$time に${what}を送りました", Kind.OK)
            else -> Row("$time ${what}を送れませんでした（$why）", Kind.BAD)
        }
    }

    fun card(raw: String?, now: Long): Card? {
        val n = try {
            JSONObject(raw ?: return null)
        } catch (e: Exception) {
            return null
        }
        val title = n.optString("title")
        if (title.isEmpty()) return null
        val at = n.optLong("received", 0L)
        val head = listOf(n.optString("area"), if (at > 0) "${clock(at, now)} に届きました" else "")
            .filter { it.isNotEmpty() }.joinToString("・")
        // notices from before the app listed the pages kept everything in the body
        var notes = if (n.has("notes")) n.optString("notes") else n.optString("body")
        if (n.optString("demo") == "1" && !notes.contains("試し")) notes = listOf(notes, "作りものの警報で試した知らせです")
            .filter { it.isNotEmpty() }.joinToString("\n")
        val checked = n.optString("checked")
        return Card(title, n.optString("level"), head, notes, pages(n.optString("links")),
            if (checked.isEmpty()) "" else "PC が調べた時刻 ${day(checked, now)}")
    }

    /** Every [name, url] page, https only; anything malformed is left out. */
    fun pages(json: String?): List<Pair<String, String>> {
        val list = try {
            JSONArray(json ?: "[]")
        } catch (e: Exception) {
            return emptyList()
        }
        val out = mutableListOf<Pair<String, String>>()
        for (i in 0 until list.length()) {
            val pair = list.optJSONArray(i) ?: continue
            val name = pair.optString(0, "").trim()
            val url = pair.optString(1, "")
            if (name.isNotEmpty() && url.startsWith("https://")) out.add(name to url)
        }
        return out
    }

    /**
     * "午後6:50" today, "11/4 午後6:50" on another day. 午前/午後 with the 12-hour clock, whatever the phone's
     * setting: next to the status bar's "8:46", "20:45" was read as the morning or as an old notice (simulated
     * people), and the PC writes the notice's own times the same way, so one screen never mixes two forms.
     */
    fun clock(at: Long, now: Long): String {
        val a = Calendar.getInstance().apply { timeInMillis = at }
        val b = Calendar.getInstance().apply { timeInMillis = now }
        val today = a.get(Calendar.YEAR) == b.get(Calendar.YEAR) && a.get(Calendar.DAY_OF_YEAR) == b.get(Calendar.DAY_OF_YEAR)
        val hour = a.get(Calendar.HOUR_OF_DAY)
        val time = (if (hour < 12) "午前" else "午後") + "${hour % 12}:" + a.get(Calendar.MINUTE).toString().padStart(2, '0')
        return if (today) time else "${a.get(Calendar.MONTH) + 1}/${a.get(Calendar.DAY_OF_MONTH)} $time"
    }

    /** The PC's "2026-11-05 18:49" the way [clock] writes it; anything else as it came. */
    private fun day(checked: String, now: Long): String = try {
        clock(SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.JAPAN).parse(checked)!!.time, now)
    } catch (e: Exception) {
        checked
    }
}
