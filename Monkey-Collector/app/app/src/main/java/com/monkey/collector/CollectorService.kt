package com.monkey.collector

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.AccessibilityServiceInfo
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.pm.ServiceInfo
import android.os.Build
import android.util.Log
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import android.view.accessibility.AccessibilityWindowInfo
import androidx.core.app.NotificationCompat

class CollectorService : AccessibilityService() {

    companion object {
        private const val TAG = "CollectorService"
        private const val DEBOUNCE_MS = 300L
        private const val NOTIFICATION_CHANNEL_ID = "MonkeyCollector_Channel"
        private const val NOTIFICATION_ID = 1

        private val EXCLUDED_PACKAGES = setOf(
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

        // Check TCP connection
        if (tcpClient?.isConnected() != true) {
            Log.w(TAG, "TCP not connected, skipping capture")
            return
        }

        // Get top interactable window root (MobileGPT-V2 pattern)
        val topResult = getTopInteractableRoot()
        if (topResult == null) {
            Log.d(TAG, "No interactable window found")
            return
        }
        val (topPackage, root) = topResult

        // Check for external app
        if (targetPackage.isNotEmpty() &&
            topPackage != targetPackage &&
            topPackage !in EXCLUDED_PACKAGES
        ) {
            Thread {
                val sent = tcpClient?.sendExternalApp(topPackage, targetPackage) ?: false
                if (!sent) {
                    Log.w(TAG, "Failed to send external app event")
                }
            }.start()

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
            try { root.recycle() } catch (_: Exception) {}
            return
        }

        consecutiveBackCount = 0

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

                // Step 5: Send data with return value check
                if (bitmap != null) {
                    val screenshotSent = tcpClient?.sendScreenshot(bitmap) ?: false
                    if (!screenshotSent) {
                        Log.w(TAG, "Failed to send screenshot at step $stepCount")
                    }
                    bitmap.recycle()
                }

                val xmlSent = tcpClient?.sendXml(xml, topPackage, targetPackage) ?: false
                if (!xmlSent) {
                    Log.w(TAG, "Failed to send XML at step $stepCount")
                }

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
            val connected = tcpClient?.connect() ?: false
            if (connected) {
                isCollecting = true
                Log.i(TAG, "Collection started: target=$targetPkg, server=$serverIp:$serverPort")
            } else {
                Log.e(TAG, "TCP connection failed, collection NOT started")
                // isCollecting remains false — no data will be captured
            }
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

    /**
     * Get the top interactable application window's root node and package name.
     * Iterates through windows to find TYPE_APPLICATION windows, excluding system packages.
     * (MobileGPT-V2 getTopInteractableRoot pattern)
     */
    private fun getTopInteractableRoot(): Pair<String, AccessibilityNodeInfo>? {
        return try {
            val windowList = windows ?: return null

            if (Log.isLoggable(TAG, Log.DEBUG)) {
                for (w in windowList) {
                    Log.d(TAG, "Window: type=${w.type}, layer=${w.layer}, " +
                            "pkg=${w.root?.packageName}, active=${w.isActive}, " +
                            "focused=${w.isFocused}")
                }
            }

            for (w in windowList) {
                if (w.type != AccessibilityWindowInfo.TYPE_APPLICATION) continue
                val root = w.root ?: continue
                val pkg = root.packageName?.toString() ?: continue
                if (pkg in EXCLUDED_PACKAGES) {
                    root.recycle()
                    continue
                }
                return Pair(pkg, root)
            }
            null
        } catch (e: Exception) {
            Log.e(TAG, "getTopInteractableRoot error: ${e.message}")
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
