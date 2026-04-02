package com.monkey.collector

import android.content.Context
import android.graphics.Color
import android.graphics.PixelFormat
import android.graphics.drawable.GradientDrawable
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.WindowManager
import android.widget.FrameLayout
import android.widget.TextView

/**
 * Floating overlay button for starting/stopping collection.
 *
 * Uses TYPE_ACCESSIBILITY_OVERLAY which is automatically available
 * to any active AccessibilityService (no SYSTEM_ALERT_WINDOW needed).
 * The overlay does NOT appear in the accessibility window list,
 * so it won't interfere with getTopInteractableRoot().
 */
class FloatingCollectorButton(private val service: CollectorService) {

    companion object {
        private const val TAG = "FloatingButton"
        private const val BUTTON_SIZE_DP = 48
        private const val CLICK_THRESHOLD = 10
    }

    private val windowManager: WindowManager =
        service.getSystemService(Context.WINDOW_SERVICE) as WindowManager

    private val handler = Handler(Looper.getMainLooper())
    private var layout: FrameLayout? = null
    private var button: TextView? = null
    private var isCollecting = false
    private var isAdded = false

    private val params = WindowManager.LayoutParams(
        dpToPx(BUTTON_SIZE_DP),
        dpToPx(BUTTON_SIZE_DP),
        WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
        WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE,
        PixelFormat.TRANSLUCENT
    ).apply {
        gravity = Gravity.END or Gravity.CENTER_VERTICAL
    }

    init {
        createLayout()
    }

    private fun createLayout() {
        val size = dpToPx(BUTTON_SIZE_DP)

        layout = FrameLayout(service).apply {
            layoutParams = FrameLayout.LayoutParams(size, size)
        }

        val bg = GradientDrawable().apply {
            shape = GradientDrawable.OVAL
            setColor(Color.parseColor("#4CAF50"))
        }

        button = TextView(service).apply {
            text = "▶"
            textSize = 20f
            setTextColor(Color.WHITE)
            gravity = Gravity.CENTER
            background = bg
            layoutParams = FrameLayout.LayoutParams(size, size)
        }

        button!!.setOnClickListener { onButtonClick() }
        setupDrag(layout!!)

        layout!!.addView(button)
    }

    private fun onButtonClick() {
        if (isCollecting) {
            stopCollection()
        } else {
            startCollection()
        }
    }

    private fun startCollection() {
        val prefs = service.getSharedPreferences("collector_settings", Context.MODE_PRIVATE)
        val ip = prefs.getString("server_ip", "") ?: ""
        val port = prefs.getInt("server_port", 12345)

        if (ip.isEmpty()) {
            Log.w(TAG, "Server IP not configured. Open MonkeyCollector app first.")
            return
        }

        // Auto-detect current foreground app as target
        val pkg = service.getCurrentForegroundPackage()
        if (pkg == null) {
            Log.w(TAG, "No foreground app detected. Open the target app first.")
            return
        }
        Log.i(TAG, "Auto-detected target app: $pkg")

        val metrics = service.resources.displayMetrics

        service.startCollection(
            serverIp = ip,
            serverPort = port,
            targetPkg = pkg,
            screenWidth = metrics.widthPixels,
            screenHeight = metrics.heightPixels,
            screenDensityDpi = metrics.densityDpi
        )

        isCollecting = true
        updateButtonState()
        Log.i(TAG, "Collection started via floating button: $pkg")
    }

    private fun stopCollection() {
        service.stopCollection()
        isCollecting = false
        updateButtonState()
        Log.i(TAG, "Collection stopped via floating button")
    }

    private fun updateButtonState() {
        handler.post {
            val btn = button ?: return@post
            if (isCollecting) {
                btn.text = "■"
                (btn.background as? GradientDrawable)?.setColor(Color.parseColor("#F44336"))
            } else {
                btn.text = "▶"
                (btn.background as? GradientDrawable)?.setColor(Color.parseColor("#4CAF50"))
            }
        }
    }

    fun show() {
        handler.post {
            layout?.visibility = View.VISIBLE
        }
    }

    fun dismiss() {
        handler.post {
            layout?.visibility = View.GONE
        }
    }

    fun addToWindow() {
        if (isAdded) return
        handler.post {
            try {
                windowManager.addView(layout, params)
                isAdded = true
                Log.d(TAG, "Floating button added")
            } catch (e: Exception) {
                Log.e(TAG, "Failed to add floating button: ${e.message}")
            }
        }
    }

    fun remove() {
        if (!isAdded) return
        handler.post {
            try {
                windowManager.removeView(layout)
                isAdded = false
                Log.d(TAG, "Floating button removed")
            } catch (e: Exception) {
                Log.e(TAG, "Failed to remove floating button: ${e.message}")
            }
        }
    }

    fun onCollectionStopped() {
        isCollecting = false
        updateButtonState()
    }

    private fun setupDrag(view: View) {
        var initialX = 0
        var initialY = 0
        var initialTouchX = 0f
        var initialTouchY = 0f
        var isDragging = false

        view.setOnTouchListener { _, event ->
            when (event.action) {
                MotionEvent.ACTION_DOWN -> {
                    initialX = params.x
                    initialY = params.y
                    initialTouchX = event.rawX
                    initialTouchY = event.rawY
                    isDragging = false
                    true
                }

                MotionEvent.ACTION_MOVE -> {
                    val dx = (event.rawX - initialTouchX).toInt()
                    val dy = (event.rawY - initialTouchY).toInt()
                    if (Math.abs(dx) > CLICK_THRESHOLD || Math.abs(dy) > CLICK_THRESHOLD) {
                        isDragging = true
                    }
                    if (isDragging) {
                        params.x = initialX - dx
                        params.y = initialY + dy
                        windowManager.updateViewLayout(layout, params)
                    }
                    true
                }

                MotionEvent.ACTION_UP -> {
                    if (!isDragging) {
                        view.performClick()
                        button?.performClick()
                    }
                    true
                }

                else -> false
            }
        }
    }

    private fun dpToPx(dp: Int): Int {
        return (dp * service.resources.displayMetrics.density).toInt()
    }
}
