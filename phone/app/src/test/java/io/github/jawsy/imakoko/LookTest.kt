package io.github.jawsy.imakoko

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class LookTest {
    @Test
    fun levelColors() {
        assertEquals(0xFFAA00AA.toInt(), Look.color("4"))
        assertEquals(0xFFFF2800.toInt(), Look.color("3"))
        assertNull(Look.color("0"))
        assertNull(Look.color(null))
        assertNull(Look.color("x"))
    }

    @Test
    fun shortWordsStayOnOneLine() {
        val j = "\u2060"
        // the emulator broke 「初 / 台アンダー」 and 「みず / が」; the joiner keeps them whole, the spaces still break
        assertEquals("近${j}い${j}の${j}は 初${j}台${j}ア${j}ン${j}ダ${j}ー 0.84km", Look.glue("近いのは 初台アンダー 0.84km"))
        assertEquals("か${j}わ${j}の　み${j}ず", Look.glue("かわの　みず"))
        // kana next to a number or a bracket is one word too
        assertEquals("れ${j}べ${j}る${j}3 は${j}ん${j}ら${j}ん${j}（${j}か${j}わ${j}の", Look.glue("れべる3 はんらん（かわの"))
        // a word longer than a third of a line may still break; plain English and the emoji are left alone
        val long = "この場所のハザードマップ（重ねるハザードマップ）"
        assertEquals(long, Look.glue(long))
        assertEquals("Flood warning", Look.glue("Flood warning"))
        assertEquals("\u26f0\ufe0f 土${j}砂", Look.glue("\u26f0\ufe0f 土砂"))
        // every line is glued alone, and taking the joiners out gives the text back
        val text = "しんじゅくく に れべる3\n近いのは 初台アンダー 0.84km"
        assertEquals(text, Look.glue(text).replace(j, ""))
        assertEquals(2, Look.glue(text).lines().size)
    }

    @Test
    fun buttonsAreHttpsAndAtMostTwo() {
        val json = """[["警報を開く","https://www.jma.go.jp/bosai/warning/"],["土砂キキクル","https://www.jma.go.jp/bosai/risk/"],""" +
            """["三つ目","https://example.org/"]]"""
        assertEquals(listOf("警報を開く", "土砂キキクル"), Look.buttons(json).map { it.first })
        assertEquals(listOf("b"), Look.buttons("""[["a","http://x/"],["b","https://y/"],["","https://z/"]]""").map { it.first })
        assertEquals(emptyList<Pair<String, String>>(), Look.buttons("not json"))
        assertEquals(emptyList<Pair<String, String>>(), Look.buttons(null))
    }
}
