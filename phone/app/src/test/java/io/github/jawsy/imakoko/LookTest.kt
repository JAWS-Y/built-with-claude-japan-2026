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
    fun buttonsAreHttpsAndAtMostTwo() {
        val json = """[["警報を開く","https://www.jma.go.jp/bosai/warning/"],["土砂キキクル","https://www.jma.go.jp/bosai/risk/"],""" +
            """["三つ目","https://example.org/"]]"""
        assertEquals(listOf("警報を開く", "土砂キキクル"), Look.buttons(json).map { it.first })
        assertEquals(listOf("b"), Look.buttons("""[["a","http://x/"],["b","https://y/"],["","https://z/"]]""").map { it.first })
        assertEquals(emptyList<Pair<String, String>>(), Look.buttons("not json"))
        assertEquals(emptyList<Pair<String, String>>(), Look.buttons(null))
    }
}
