package io.github.jawsy.imakoko

import java.util.concurrent.Executors

/** One thread for network writes, so they never run on the main thread and never overlap. */
object Background {
    private val pool = Executors.newSingleThreadExecutor()

    fun run(work: () -> Unit) {
        pool.execute(work)
    }
}
