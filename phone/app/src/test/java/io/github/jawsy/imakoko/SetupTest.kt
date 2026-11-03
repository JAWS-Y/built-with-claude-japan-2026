package io.github.jawsy.imakoko

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
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
}
