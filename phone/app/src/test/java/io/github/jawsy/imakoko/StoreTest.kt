package io.github.jawsy.imakoko

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class StoreTest {
    private val s = Setup(
        "imakoko-test", "123", "1:123:android:abc", "test-api-key-0123456789abcdef",
        "Ab-_0123456789Ab-_0123456789Ab-_0123456789x",
    )

    @Test
    fun urlNamesTheFieldsItWrites() {
        val u = Store.url(s, listOf("lat", "lon"))
        assertTrue(u.startsWith("https://firestore.googleapis.com/v1/projects/imakoko-test/databases/(default)/documents/devices/Ab-_"))
        assertTrue(u.endsWith("&updateMask.fieldPaths=lat&updateMask.fieldPaths=lon"))
        assertFalse(Store.url(s, emptyList()).contains("updateMask"))
    }

    @Test
    fun locationBodyHasTheFieldsTheRulesAllow() {
        val f = Store.locationBody(35.5, 139.25, 12.0, 1730000000L).getJSONObject("fields")
        assertEquals(setOf("lat", "lon", "accuracy", "at"), f.keys().asSequence().toSet())
        assertEquals(35.5, f.getJSONObject("lat").getDouble("doubleValue"), 0.0)
        assertEquals("1730000000", f.getJSONObject("at").getString("integerValue"))
    }

    @Test
    fun tokenBodyHasOnlyTheToken() {
        val f = Store.tokenBody("t0k").getJSONObject("fields")
        assertEquals(setOf("token"), f.keys().asSequence().toSet())
        assertEquals("t0k", f.getJSONObject("token").getString("stringValue"))
    }
}
