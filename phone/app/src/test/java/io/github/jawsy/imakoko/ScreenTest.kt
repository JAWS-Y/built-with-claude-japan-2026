package io.github.jawsy.imakoko

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.BeforeClass
import org.junit.Test
import java.text.SimpleDateFormat
import java.util.Locale
import java.util.TimeZone

class ScreenTest {
    companion object {
        @BeforeClass
        @JvmStatic
        fun tokyo() {
            TimeZone.setDefault(TimeZone.getTimeZone("Asia/Tokyo"))
        }

        private fun at(s: String) = SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.JAPAN).parse(s)!!.time
    }

    private val now = at("2026-11-05 19:00")

    private fun state(
        project: String? = "imakoko-demo",
        tracking: Boolean = true,
        fine: Boolean = true,
        coarse: Boolean = true,
        always: Boolean = true,
        notifies: Boolean = true,
        lastWrite: Pair<Long, String>? = null,
        message: String = "",
        lastPlace: Long? = null,
    ) = Screen.State(project, tracking, fine, coarse, always, notifies, lastWrite, lastPlace, message, null, now)

    @Test
    fun anOldPositionIsSaidNotHidden() {
        // the emulator showed 見守り中 while the PC used a position from 4 hours before
        val old = state(lastPlace = now - 4 * 3_600_000L - 22 * 60_000L)
        assertEquals(Screen.Row("位置が古いままです", Screen.Kind.WARN), Screen.chip(old))
        val row = Screen.rows(old).single { it.kind == Screen.Kind.WARN }
        assertTrue(row.text, row.text.startsWith("最後に位置が届いたのは ") && row.text.contains("（4 時間前）"))
        assertEquals(Screen.Fix.APP, row.fix)
        // fresh, never sent yet, or not sending: nothing to say
        assertEquals(Screen.Kind.OK, Screen.chip(state(lastPlace = now - 50 * 60_000L)).kind)
        assertNull(Screen.stale(state(lastPlace = now - 50 * 60_000L)))
        assertNull(Screen.stale(state(lastPlace = null)))
        assertNull(Screen.stale(state(tracking = false, lastPlace = now - 5 * 3_600_000L)))
        // a real failure still wins over an old position
        assertEquals(Screen.Kind.BAD, Screen.chip(state(notifies = false, lastPlace = now - 5 * 3_600_000L)).kind)
    }

    @Test
    fun chipSaysTheOneThingThatMatters() {
        assertEquals(Screen.Row("設定がまだです", Screen.Kind.WARN), Screen.chip(state(project = null, tracking = false)))
        assertEquals(Screen.Row("止まっています", Screen.Kind.IDLE), Screen.chip(state(tracking = false)))
        assertEquals(Screen.Row("見守り中", Screen.Kind.OK), Screen.chip(state()))
        assertEquals(Screen.Kind.BAD, Screen.chip(state(notifies = false)).kind)
        // a warning alone does not stop the watch
        assertEquals(Screen.Kind.OK, Screen.chip(state(always = false)).kind)
    }

    @Test
    fun problemsOnlyWhileSending() {
        assertEquals(emptyList<Screen.Row>(), Screen.problems(state(tracking = false, notifies = false, fine = false, coarse = false)))
        val off = Screen.problems(state(notifies = false))
        assertEquals(listOf(Screen.Kind.BAD), off.map { it.kind })
        assertEquals(Screen.Fix.NOTIFICATIONS, off[0].fix)
        val none = Screen.problems(state(fine = false, coarse = false, always = false))
        assertEquals(1, none.size)
        assertEquals(Screen.Kind.BAD, none[0].kind)
        assertEquals(Screen.Fix.APP, none[0].fix)
        val rough = Screen.problems(state(fine = false, always = false))
        assertEquals(listOf(Screen.Kind.WARN, Screen.Kind.WARN), rough.map { it.kind })
        assertTrue(rough[0].text.contains("常に"))
        assertTrue(rough[1].text.contains("おおよそ"))
    }

    @Test
    fun rowsInOrder() {
        assertEquals(emptyList<Screen.Row>(), Screen.rows(state(project = null, tracking = false)))
        val r = Screen.rows(state(always = false, lastWrite = at("2026-11-05 18:52") to "位置: 送れました", message = "設定を保存しました。"))
        assertEquals(
            listOf(
                Screen.Row("位置を送っています", Screen.Kind.OK),
                Screen.Row("午後6:52 に位置を送りました", Screen.Kind.OK),
            ),
            r.take(2),
        )
        assertEquals(Screen.Kind.WARN, r[2].kind)
        assertEquals(Screen.Row("設定を保存しました。", Screen.Kind.INFO), r[3])
        assertEquals(Screen.Row("位置は送っていません", Screen.Kind.IDLE), Screen.rows(state(tracking = false))[0])
    }

    @Test
    fun writtenInPlainWords() {
        val t = at("2026-11-05 18:52")
        assertEquals(Screen.Row("午後6:52 に位置を送りました", Screen.Kind.OK), Screen.written(t, "位置: 送れました", now))
        assertEquals(Screen.Row("午後6:52 に通知の宛先を送りました", Screen.Kind.OK), Screen.written(t, "通知の宛先: 送れました", now))
        assertEquals(Screen.Row("午後6:52 位置を送れませんでした（つながりません）", Screen.Kind.BAD), Screen.written(t, "位置: つながりません", now))
        assertEquals(Screen.Row("午後6:52 位置を送れませんでした（HTTP 403）", Screen.Kind.BAD), Screen.written(t, "位置: 失敗（HTTP 403）", now))
        assertEquals(Screen.Kind.OK, Screen.written(t, "消去: 送れました", now).kind)
        assertEquals(Screen.Row("午後6:52 Firebase の位置を消せませんでした（HTTP 500）", Screen.Kind.BAD), Screen.written(t, "消去: 失敗（HTTP 500）", now))
        assertEquals(Screen.Row("午後6:52 なにか", Screen.Kind.INFO), Screen.written(t, "なにか", now))
        assertEquals("11/4 午後6:52 に位置を送りました", Screen.written(at("2026-11-04 18:52"), "位置: 送れました", now).text)
    }

    @Test
    fun cardFromANotice() {
        val raw = """{"title":"土砂災害に気をつけて（試し）","area":"名古屋市","level":"4",""" +
            """"links":"[[\"気象庁 警報・注意報\",\"https://www.jma.go.jp/bosai/warning/\"],[\"気象庁 キキクル\",\"https://www.jma.go.jp/bosai/risk/\"]]",""" +
            """"notes":"今いる所は土砂災害警戒区域です\n警報は試しの作りもの。ほかは本物","checked":"2026-11-05 18:49","demo":"1",""" +
            """"received":${at("2026-11-05 18:50")}}"""
        val c = Screen.card(raw, now)!!
        assertEquals("土砂災害に気をつけて（試し）", c.title)
        assertEquals("4", c.level)
        assertEquals("名古屋市・午後6:50 に届きました", c.head)
        // the PC's own 試し note is kept and not doubled
        assertEquals("今いる所は土砂災害警戒区域です\n警報は試しの作りもの。ほかは本物", c.notes)
        assertEquals(listOf("気象庁 警報・注意報", "気象庁 キキクル"), c.pages.map { it.first })
        assertEquals("PC が調べた時刻 午後6:49", c.checked)
    }

    @Test
    fun oldAndOddNotices() {
        // before the app listed pages, everything was in the body
        val old = Screen.card("""{"title":"大雨に気をつけて","body":"本文だけ","demo":"1"}""", now)!!
        assertEquals("本文だけ\n作りものの警報で試した知らせです", old.notes)
        assertEquals("", old.head)
        assertEquals("", old.checked)
        assertEquals(emptyList<Pair<String, String>>(), old.pages)
        assertEquals("作りものの警報で試した知らせです", Screen.card("""{"title":"t","demo":"1"}""", now)!!.notes)
        assertEquals("", Screen.card("""{"title":"t","notes":""}""", now)!!.notes)
        assertEquals("きのう", Screen.card("""{"title":"t","checked":"きのう"}""", now)!!.checked.removePrefix("PC が調べた時刻 "))
        assertNull(Screen.card("""{"title":""}""", now))
        assertNull(Screen.card("not json", now))
        assertNull(Screen.card(null, now))
    }

    @Test
    fun pagesAreHttpsWithNames() {
        val json = """[["a","https://x/"],["b","http://y/"],["","https://z/"],["c"],"d",["e","https://w/"]]"""
        assertEquals(listOf("a" to "https://x/", "e" to "https://w/"), Screen.pages(json))
        assertEquals(emptyList<Pair<String, String>>(), Screen.pages("{"))
        assertEquals(emptyList<Pair<String, String>>(), Screen.pages(null))
    }

    @Test
    fun clockShowsTheDayOnlyWhenNotToday() {
        assertEquals("午前9:05", Screen.clock(at("2026-11-05 09:05"), now))
        assertEquals("11/4 午後11:59", Screen.clock(at("2026-11-04 23:59"), now))
        assertEquals("午前0:30", Screen.clock(at("2026-11-05 00:30"), now))
        assertEquals("午後0:05", Screen.clock(at("2026-11-05 12:05"), now))
        assertEquals("午前11:59", Screen.clock(at("2026-11-05 11:59"), now))
        // the PC's check time from another day keeps its day
        assertEquals("PC が調べた時刻 11/4 午後11:50",
            Screen.card("""{"title":"t","checked":"2026-11-04 23:50"}""", now)!!.checked)
    }
}
