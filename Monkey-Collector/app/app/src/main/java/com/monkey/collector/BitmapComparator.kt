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

    /**
     * Compute a lightweight perceptual hash for oscillation detection.
     *
     * Divides the bitmap into an 8×8 grid and computes the average
     * luminance for each cell, producing a 64-byte fingerprint.
     * Two frames with the same hash are considered visually identical
     * for oscillation-detection purposes.
     */
    fun computeFrameHash(bitmap: Bitmap, gridSize: Int = 8): ByteArray {
        val w = bitmap.width
        val h = bitmap.height
        val pixels = IntArray(w * h)
        bitmap.getPixels(pixels, 0, w, 0, 0, w, h)

        val cellW = maxOf(w / gridSize, 1)
        val cellH = maxOf(h / gridSize, 1)
        val hash = ByteArray(gridSize * gridSize)

        for (gy in 0 until gridSize) {
            for (gx in 0 until gridSize) {
                var sum = 0L
                var count = 0
                val yStart = gy * cellH
                val yEnd = minOf((gy + 1) * cellH, h)
                val xStart = gx * cellW
                val xEnd = minOf((gx + 1) * cellW, w)
                for (y in yStart until yEnd) {
                    for (x in xStart until xEnd) {
                        val pixel = pixels[y * w + x]
                        val r = (pixel shr 16) and 0xFF
                        val g = (pixel shr 8) and 0xFF
                        val b = pixel and 0xFF
                        sum += (r + g + b) / 3
                        count++
                    }
                }
                hash[gy * gridSize + gx] = if (count > 0) (sum / count).toByte() else 0
            }
        }
        return hash
    }
}
