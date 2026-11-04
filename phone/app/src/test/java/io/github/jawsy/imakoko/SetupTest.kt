package io.github.jawsy.imakoko

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class SetupTest {
    private val device = "Ab-_0123456789Ab-_0123456789Ab-_0123456789x"
    private val good = """{"v":1,"projectId":"imakoko-test","senderId":"123456789012",""" +
        """"appId":"1:123456789012:android:0123456789abcdef","apiKey":"test-api-key-0123456789abcdef",""" +
        """"device":"$device"}"""

    @Test
    fun readsWhatPairPrints() {
        val s = Setup.parse("  $good\n")
        assertNotNull(s)
        assertEquals("imakoko-test", s!!.projectId)
        assertEquals(device, s.device)
        assertEquals(s, Setup.parse(s.toJson()))
    }

    @Test
    fun refusesOtherText() {
        assertNull(Setup.parse(""))
        assertNull(Setup.parse("not json"))
        assertNull(Setup.parse(good.replace("\"v\":1", "\"v\":2")))
        assertNull(Setup.parse(good.replace(device, "short")))
        assertNull(Setup.parse(good.replace(device, "$device/../x")))
        assertNull(Setup.parse(good.replace("imakoko-test", "Imakoko/Test")))
        assertNull(Setup.parse(good.replace("test-api-key-0123456789abcdef", "key&x=1")))
    }

    @Test
    fun readsTextBrokenByCopying() {
        val want = Setup.parse(good)
        assertNotNull(want)
        // a document app on the phone breaks long lines inside the values when copying
        val broken = good.replace("android", "andro\nid").replace("\"device\"", "\"dev\r\nice\"")
            .replace(",", ",\n ")
        assertEquals(want, Setup.parse(broken))
        assertEquals(want, Setup.parse("\uFEFF" + good))
        assertEquals(want, Setup.parse(good.replace("\"", "\u201D").replaceFirst("\u201D", "\u201C")))
        assertEquals(want, Setup.parse(good.replace("0123", "01\u200B23").replace(":", "\u00A0:\u3000")))
        assertEquals(want, Setup.parse("携帯のアプリの「設定」に貼る文です。\n\n$good\n"))
    }

    @Test
    fun saysWhichPartIsWrongWithoutTheValue() {
        assertEquals(null, Setup.read(good).problem)
        assertTrue(Setup.read("not json").problem!!.contains("{"))
        assertTrue(Setup.read(good.dropLast(12) + "}").problem!!.contains("欠けて"))
        val badKey = Setup.read(good.replace("test-api-key-0123456789abcdef", "key&x=1"))
        assertNull(badKey.setup)
        assertEquals("apiKey の形が違います", badKey.problem)
        assertTrue(!badKey.problem!!.contains("key&x=1"))
    }
}
