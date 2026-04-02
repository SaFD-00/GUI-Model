package com.monkey.collector

import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.PixelFormat
import android.graphics.Rect
import android.hardware.display.DisplayManager
import android.hardware.display.VirtualDisplay
import android.media.Image
import android.media.ImageReader
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.util.Log
import java.nio.ByteBuffer
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Visual screen stabilization using MediaProjection low-res capture.
 *
 * Ported from computer-use-preview-for-mobile's Screen_Service.kt.
 * Captures low-resolution frames (100px wide) and compares consecutive
 * frames to detect when the screen has stopped changing (animations complete).
 */
class ScreenStabilizer(
    private val screenWidth: Int,
    private val screenHeight: Int,
    private val screenDensityDpi: Int
) {
    companion object {
        private const val TAG = "ScreenStabilizer"
        const val TARGET_WIDTH = 100
        const val STABILITY_THRESHOLD = 0.02f   // 2% pixel difference
        const val FIRST_SCREEN_THRESHOLD = 0.05f // 5% — more lenient for first screen comparison
        const val REQUIRED_STABLE_FRAMES = 3     // 3 consecutive stable frames
        const val MAX_ATTEMPTS = 30              // ~15 seconds max
        const val CHECK_INTERVAL_MS = 500L       // 500ms between checks
    }

    private var mediaProjectionManager: MediaProjectionManager? = null
    private var mediaProjection: MediaProjection? = null
    private var imageReader: ImageReader? = null
    private var virtualDisplay: VirtualDisplay? = null
    private var lastStableFrame: Bitmap? = null
    private var firstScreenFrame: Bitmap? = null
    private val isStabilizing = AtomicBoolean(false)

    private val targetWidth: Int = TARGET_WIDTH
    private val targetHeight: Int =
        if (screenWidth > 0) (screenHeight.toFloat() / screenWidth.toFloat() * TARGET_WIDTH).toInt()
        else 178 // fallback for 1080x1920

    fun initProjection(context: Context) {
        mediaProjectionManager =
            context.getSystemService(Context.MEDIA_PROJECTION_SERVICE) as MediaProjectionManager
    }

    /**
     * Start the MediaProjection capture session with low-res ImageReader.
     * Must be called AFTER startForeground() on Android 14+.
     */
    fun startCaptureSession(resultCode: Int, data: Intent) {
        if (mediaProjectionManager == null) {
            Log.e(TAG, "MediaProjectionManager is null")
            return
        }

        // Release VirtualDisplay/ImageReader but keep MediaProjection alive
        stopCaptureSession()

        // Only acquire new projection if we don't have one
        if (mediaProjection == null) {
            try {
                mediaProjection = mediaProjectionManager!!.getMediaProjection(resultCode, data)
                mediaProjection?.registerCallback(object : MediaProjection.Callback() {
                    override fun onStop() {
                        Log.w(TAG, "MediaProjection stopped externally")
                        mediaProjection = null
                    }
                }, null)
            } catch (e: SecurityException) {
                Log.e(TAG, "MediaProjection token expired: ${e.message}")
                mediaProjection = null
                return
            }
        }

        if (targetHeight <= 0) {
            Log.e(TAG, "Invalid target height: $targetHeight")
            return
        }

        imageReader = ImageReader.newInstance(
            targetWidth, targetHeight, PixelFormat.RGBA_8888, 2
        )

        virtualDisplay = mediaProjection?.createVirtualDisplay(
            "MonkeyCollector_Stabilizer",
            targetWidth,
            targetHeight,
            screenDensityDpi,
            DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,
            imageReader?.surface,
            null,
            null
        )

        if (virtualDisplay == null) {
            Log.e(TAG, "VirtualDisplay creation failed! MediaProjection may be invalid.")
            return
        }

        Log.i(TAG, "Capture session started (${targetWidth}x${targetHeight})")
    }

    fun stopCaptureSession() {
        virtualDisplay?.release()
        virtualDisplay = null
        imageReader?.close()
        imageReader = null
        lastStableFrame?.recycle()
        lastStableFrame = null
        firstScreenFrame?.recycle()
        firstScreenFrame = null
        Log.d(TAG, "Capture session stopped")
    }

    /**
     * Full cleanup including MediaProjection.
     * Call only when the service is being destroyed.
     */
    fun release() {
        stopCaptureSession()
        mediaProjection?.stop()
        mediaProjection = null
        Log.d(TAG, "ScreenStabilizer fully released")
    }

    /**
     * Take a low-resolution comparison screenshot via MediaProjection.
     */
    fun takeComparisonScreenshot(): Bitmap? {
        if (imageReader == null) return null

        var image: Image? = null
        try {
            image = imageReader!!.acquireLatestImage() ?: return null
            return convertImageToBitmap(image)
        } catch (e: Exception) {
            Log.e(TAG, "Screenshot failed: ${e.message}")
            return null
        } finally {
            image?.close()
        }
    }

    /**
     * Wait for the screen to stabilize (stop changing).
     *
     * Compares consecutive low-res frames at 500ms intervals.
     * Returns true when 3 consecutive frames are within 2% difference.
     * Returns false on timeout (captures should still proceed).
     *
     * This is a blocking call — must be called from a worker thread.
     */
    fun waitForStable(): Boolean {
        if (!isStabilizing.compareAndSet(false, true)) {
            Log.d(TAG, "Already stabilizing, skipping")
            return true
        }

        try {
            return doWaitForStable()
        } finally {
            isStabilizing.set(false)
        }
    }

    private fun doWaitForStable(): Boolean {
        var stableCount = 0
        var bitmapA: Bitmap? = null

        for (retry in 0..2) {
            bitmapA = takeComparisonScreenshot()
            if (bitmapA != null) break
            Thread.sleep(500)
        }
        if (bitmapA == null) {
            Log.w(TAG, "Initial capture failed after 3 retries, skipping stabilization")
            return true
        }

        try {
            for (i in 1..MAX_ATTEMPTS) {
                Thread.sleep(CHECK_INTERVAL_MS)

                val bitmapB = takeComparisonScreenshot()

                val difference = if (bitmapB == null) {
                    0.0f // No new frame = screen is static
                } else {
                    BitmapComparator.compare(bitmapA!!, bitmapB)
                }

                if (difference < STABILITY_THRESHOLD) {
                    stableCount++
                    bitmapB?.recycle()

                    if (stableCount >= REQUIRED_STABLE_FRAMES) {
                        Log.d(TAG, "Screen stable after $i checks ($stableCount consecutive)")
                        return true
                    }
                } else {
                    stableCount = 0
                    bitmapA!!.recycle()
                    bitmapA = bitmapB ?: takeComparisonScreenshot()
                }
            }
        } finally {
            if (bitmapA != null && !bitmapA.isRecycled) {
                bitmapA.recycle()
            }
        }

        Log.w(TAG, "Stabilization timeout ($MAX_ATTEMPTS attempts)")
        return false
    }

    /**
     * Check if the screen has visually changed since the last stable frame.
     * Also updates lastStableFrame for the next comparison.
     *
     * @return true if visual change detected, false if screen looks the same.
     */
    fun hasVisualChange(): Boolean {
        val currentFrame = takeComparisonScreenshot() ?: return true

        val previous = lastStableFrame
        if (previous == null) {
            lastStableFrame = currentFrame
            return true // First frame, always consider changed
        }

        val diff = BitmapComparator.compare(previous, currentFrame)

        previous.recycle()
        lastStableFrame = currentFrame

        val changed = diff > STABILITY_THRESHOLD
        if (!changed) {
            Log.d(TAG, "No visual change (diff=${String.format("%.4f", diff)})")
        }
        return changed
    }

    /**
     * Save the current frame as the first screen reference.
     * Called once when the very first screen capture is about to be sent.
     */
    fun saveFirstScreen() {
        val frame = takeComparisonScreenshot() ?: return
        firstScreenFrame?.recycle()
        firstScreenFrame = frame
        Log.i(TAG, "First screen saved for back-button protection")
    }

    /**
     * Check if the current screen visually matches the first screen.
     * Uses a more lenient threshold (5%) than stability checks
     * to tolerate minor dynamic content (clock, badges).
     *
     * @return true if current screen matches first screen, false otherwise.
     *         Returns false if first screen was never saved.
     */
    fun isFirstScreen(): Boolean {
        val reference = firstScreenFrame ?: return false
        val current = takeComparisonScreenshot() ?: return false
        val diff = BitmapComparator.compare(reference, current)
        current.recycle()
        val isFirst = diff < FIRST_SCREEN_THRESHOLD
        if (isFirst) {
            Log.d(TAG, "Current screen matches first screen (diff=${String.format("%.4f", diff)})")
        }
        return isFirst
    }

    /**
     * Convert an Image from ImageReader to a clean Bitmap.
     * Handles row padding artifacts from the ImageReader buffer.
     */
    private fun convertImageToBitmap(image: Image): Bitmap? {
        try {
            val planes = image.planes
            val buffer: ByteBuffer = planes[0].buffer
            val pixelStride = planes[0].pixelStride
            val rowStride = planes[0].rowStride
            val rowPadding = rowStride - pixelStride * image.width

            // Create bitmap with padding
            val rawBitmap = Bitmap.createBitmap(
                image.width + rowPadding / pixelStride,
                image.height,
                Bitmap.Config.ARGB_8888
            )
            rawBitmap.copyPixelsFromBuffer(buffer)

            // Create clean bitmap without padding
            val cleanBitmap = Bitmap.createBitmap(
                image.width,
                image.height,
                Bitmap.Config.ARGB_8888
            )
            val canvas = Canvas(cleanBitmap)
            val srcRect = Rect(0, 0, image.width, image.height)
            val dstRect = Rect(0, 0, image.width, image.height)
            canvas.drawBitmap(rawBitmap, srcRect, dstRect, null)

            rawBitmap.recycle()
            return cleanBitmap
        } catch (e: Exception) {
            Log.e(TAG, "Image→Bitmap conversion failed: ${e.message}")
            return null
        }
    }
}
