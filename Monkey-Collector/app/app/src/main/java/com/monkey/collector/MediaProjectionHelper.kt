package com.monkey.collector

import android.content.Intent

/**
 * Singleton to pass MediaProjection permission result from Activity to Service.
 *
 * MediaProjection requires Activity context for permission request.
 * This helper stores the result so CollectorService can use it.
 */
object MediaProjectionHelper {

    var resultCode: Int = 0
        private set

    var resultData: Intent? = null
        private set

    val isGranted: Boolean
        get() = resultCode != 0 && resultData != null

    fun saveResult(code: Int, data: Intent?) {
        resultCode = code
        resultData = data
    }

    fun clear() {
        resultCode = 0
        resultData = null
    }
}
