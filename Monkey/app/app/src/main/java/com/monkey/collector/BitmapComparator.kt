package com.monkey.collector

import android.graphics.Bitmap

/**
 * Compare two bitmaps to measure visual difference.
 *
 * Ported from computer-use-preview-for-mobile's compareBitmapsKotlin().
 * Uses simple per-pixel RGBA comparison on low-resolution bitmaps.
 */
object BitmapComparator {

    /**
     * Compare two bitmaps and return the fraction of differing pixels.
     *
     * @return 0.0f (identical) to 1.0f (completely different).
     *         Returns 1.0f if dimensions don't match.
     */
    fun compare(bitmapA: Bitmap, bitmapB: Bitmap): Float {
        if (bitmapA.width != bitmapB.width || bitmapA.height != bitmapB.height) {
            return 1.0f
        }

        val width = bitmapA.width
        val height = bitmapA.height
        val size = width * height

        val pixelsA = IntArray(size)
        val pixelsB = IntArray(size)

        bitmapA.getPixels(pixelsA, 0, width, 0, 0, width, height)
        bitmapB.getPixels(pixelsB, 0, width, 0, 0, width, height)

        var diffCount = 0
        for (i in 0 until size) {
            if (pixelsA[i] != pixelsB[i]) {
                diffCount++
            }
        }

        return diffCount.toFloat() / size.toFloat()
    }
}
