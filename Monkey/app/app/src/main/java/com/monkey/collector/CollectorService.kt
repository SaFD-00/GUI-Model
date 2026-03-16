package com.monkey.collector

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.AccessibilityServiceInfo
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.pm.ServiceInfo
import android.os.Build
import android.util.Log
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityWindowInfo
import androidx.core.app.NotificationCompat

class CollectorService : AccessibilityService() {

    companion object {
        private const val TAG = "CollectorService"
        private const val DEBOUNCE_MS = 300L
        private const val NOTIFICATION_CHANNEL_ID = "MonkeyCollector_Channel"
        private const val NOTIFICATION_ID = 1

        private val ALLOWED_PACKAGES = setOf(
            "com.android.systemui",
            "com.android.permissioncontroller",
            "com.monkey.collector"
        )

        var instance: CollectorService? = null
            private set
    }

    private var tcpClient: TcpClient? = null
    private var targetPackage: String = ""
    private var stepCount: Int = 0
    private var lastEventTime: Long = 0
    private var consecutiveBackCount: Int = 0
    private var isCollecting: Boolean = false
    private var screenStabilizer: ScreenStabilizer? = null

    override fun onServiceConnected() {
        super.onServiceConnected()
        instance = this

        serviceInfo = serviceInfo.apply {
            eventTypes = AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED or
                    AccessibilityEvent.TYPE_WINDOW_CONTENT_CHANGED
            feedbackType = AccessibilityServiceInfo.FEEDBACK_GENERIC
            flags = AccessibilityServiceInfo.FLAG_REPORT_VIEW_IDS or
                    AccessibilityServiceInfo.FLAG_RETRIEVE_INTERACTIVE_WINDOWS or
                    AccessibilityServiceInfo.FLAG_INCLUDE_NOT_IMPORTANT_VIEWS
            notificationTimeout = DEBOUNCE_MS
        }

        Log.i(TAG, "Service connected")
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        if (event == null || !isCollecting) return

        val eventType = event.eventType
        if (eventType != AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED &&
            eventType != AccessibilityEvent.TYPE_WINDOW_CONTENT_CHANGED
        ) return

        // Debounce
        val now = System.currentTimeMillis()
        if (now - lastEventTime < DEBOUNCE_MS) return
        lastEventTime = now

        // Get top package
        val topPackage = getTopPackage()
        if (topPackage.isNullOrEmpty()) return

        // Check for external app
        if (targetPackage.isNotEmpty() &&
            topPackage != targetPackage &&
            topPackage !in ALLOWED_PACKAGES
        ) {
            tcpClient?.sendExternalApp(topPackage, targetPackage)
            consecutiveBackCount++

            if (consecutiveBackCount >= 3) {
                try {
                    Runtime.getRuntime().exec(
                        arrayOf("am", "start", "-n",
                            "$targetPackage/${getMainActivity(targetPackage)}")
                    )
                } catch (e: Exception) {
                    Log.e(TAG, "Force launch failed: ${e.message}")
                }
                consecutiveBackCount = 0
            } else {
                performGlobalAction(GLOBAL_ACTION_BACK)
            }
            return
        }

        consecutiveBackCount = 0

        // Capture screenshot + XML
        val root = rootInActiveWindow ?: return

        Thread {
            try {
                // Step 1: Wait for screen to stabilize (visual bitmap comparison)
                val stabilizer = screenStabilizer
                if (stabilizer != null) {
                    val stabilized = stabilizer.waitForStable()
                    if (!stabilized) {
                        Log.w(TAG, "Screen stabilization timeout, capturing anyway")
                    }

                    // Step 2: Check for actual visual change
                    if (!stabilizer.hasVisualChange()) {
                        Log.d(TAG, "No visual change detected, skipping capture")
                        return@Thread
                    }
                }

                // Step 3: Take high-res screenshot (existing logic)
                val bitmap = ScreenCapture.takeSync(this)

                // Step 4: Dump XML (existing logic)
                val xml = XmlDumper.dumpNodeTree(root)

                // Step 5: Send data (existing logic)
                if (bitmap != null) {
                    tcpClient?.sendScreenshot(bitmap)
                    bitmap.recycle()
                }
                tcpClient?.sendXml(xml, topPackage, targetPackage)

                stepCount++
                Log.d(TAG, "Step $stepCount captured for $topPackage")

            } catch (e: Exception) {
                Log.e(TAG, "Capture error: ${e.message}")
            } finally {
                try { root.recycle() } catch (_: Exception) {}
            }
        }.start()
    }

    override fun onInterrupt() {
        Log.w(TAG, "Service interrupted")
    }

    override fun onDestroy() {
        super.onDestroy()
        instance = null
        stopCollection()
    }

    fun startCollection(
        serverIp: String,
        serverPort: Int,
        targetPkg: String,
        screenWidth: Int,
        screenHeight: Int,
        screenDensityDpi: Int
    ) {
        targetPackage = targetPkg
        stepCount = 0
        consecutiveBackCount = 0

        // Start foreground service (required before MediaProjection on API 29+)
        startForegroundService()

        // Initialize screen stabilizer with MediaProjection
        if (MediaProjectionHelper.isGranted) {
            val stabilizer = ScreenStabilizer(screenWidth, screenHeight, screenDensityDpi)
            stabilizer.initProjection(this)
            stabilizer.startCaptureSession(
                MediaProjectionHelper.resultCode,
                MediaProjectionHelper.resultData!!
            )
            screenStabilizer = stabilizer
            Log.i(TAG, "ScreenStabilizer initialized (${screenWidth}x${screenHeight})")
        } else {
            Log.w(TAG, "MediaProjection not granted, running without visual stabilization")
        }

        // Connect TCP client
        tcpClient = TcpClient(serverIp, serverPort)
        Thread {
            tcpClient?.connect()
            isCollecting = true
            Log.i(TAG, "Collection started: target=$targetPkg, server=$serverIp:$serverPort")
        }.start()
    }

    fun stopCollection() {
        isCollecting = false

        // Stop screen stabilizer
        screenStabilizer?.stopCaptureSession()
        screenStabilizer = null

        // Send finish and disconnect
        tcpClient?.sendFinish()
        Thread {
            Thread.sleep(500)
            tcpClient?.disconnect()
            tcpClient = null
        }.start()

        // Stop foreground
        stopForeground(STOP_FOREGROUND_REMOVE)

        Log.i(TAG, "Collection stopped. Steps: $stepCount")
    }

    private fun startForegroundService() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                NOTIFICATION_CHANNEL_ID,
                "MonkeyCollector Service",
                NotificationManager.IMPORTANCE_LOW
            )
            val manager = getSystemService(NotificationManager::class.java)
            manager.createNotificationChannel(channel)
        }

        val notification = NotificationCompat.Builder(this, NOTIFICATION_CHANNEL_ID)
            .setContentTitle("MonkeyCollector")
            .setContentText("Collecting UI data...")
            .setSmallIcon(android.R.drawable.ic_menu_camera)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .build()

        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                startForeground(
                    NOTIFICATION_ID,
                    notification,
                    ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION
                )
            } else {
                startForeground(NOTIFICATION_ID, notification)
            }
            Log.d(TAG, "Foreground service started")
        } catch (e: Exception) {
            Log.e(TAG, "Failed to start foreground service: ${e.message}")
        }
    }

    private fun getTopPackage(): String? {
        return try {
            windows
                ?.filter { it.type == AccessibilityWindowInfo.TYPE_APPLICATION }
                ?.mapNotNull { it.root?.packageName?.toString() }
                ?.firstOrNull { it !in ALLOWED_PACKAGES }
        } catch (e: Exception) {
            null
        }
    }

    private fun getMainActivity(packageName: String): String {
        return try {
            val intent = packageManager.getLaunchIntentForPackage(packageName)
            intent?.component?.className ?: ""
        } catch (e: Exception) {
            ""
        }
    }
}
