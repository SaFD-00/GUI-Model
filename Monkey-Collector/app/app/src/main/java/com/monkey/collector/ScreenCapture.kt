package com.monkey.collector

import android.accessibilityservice.AccessibilityService
import android.graphics.Bitmap
import android.os.Build
import android.util.Log
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

object ScreenCapture {
    private const val TAG = "ScreenCapture"

    fun take(service: AccessibilityService, callback: (Bitmap?) -> Unit) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            // API 30+ : takeScreenshot API
            service.takeScreenshot(
                android.view.Display.DEFAULT_DISPLAY,
                service.mainExecutor,
                object : AccessibilityService.TakeScreenshotCallback {
                    override fun onSuccess(result: AccessibilityService.ScreenshotResult) {
                        val hardwareBitmap = Bitmap.wrapHardwareBuffer(
                            result.hardwareBuffer,
                            result.colorSpace
                        )
                        val bitmap = hardwareBitmap?.copy(Bitmap.Config.ARGB_8888, false)
                        hardwareBitmap?.recycle()
                        result.hardwareBuffer.close()
                        callback(bitmap)
                    }

                    override fun onFailure(errorCode: Int) {
                        Log.e(TAG, "Screenshot failed: errorCode=$errorCode")
                        callback(null)
                    }
                }
            )
        } else {
            Log.w(TAG, "Screenshot API requires API 30+")
            callback(null)
        }
    }

    fun takeSync(service: AccessibilityService, timeoutMs: Long = 5000): Bitmap? {
        var result: Bitmap? = null
        val latch = CountDownLatch(1)

        take(service) { bitmap ->
            result = bitmap
            latch.countDown()
        }

        latch.await(timeoutMs, TimeUnit.MILLISECONDS)
        return result
    }
}
