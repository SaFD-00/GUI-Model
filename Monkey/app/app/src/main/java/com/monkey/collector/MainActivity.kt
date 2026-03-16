package com.monkey.collector

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.media.projection.MediaProjectionManager
import android.os.Bundle
import android.provider.Settings
import android.util.DisplayMetrics
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity

class MainActivity : AppCompatActivity() {

    private lateinit var etServerIp: EditText
    private lateinit var etServerPort: EditText
    private lateinit var etTargetPackage: EditText
    private lateinit var tvStatus: TextView
    private lateinit var btnStart: Button
    private lateinit var btnStop: Button
    private lateinit var btnAccessibility: Button

    private lateinit var mediaProjectionManager: MediaProjectionManager

    // Pending collection params (saved before MediaProjection request)
    private var pendingIp: String = ""
    private var pendingPort: Int = 12345
    private var pendingPkg: String = ""

    private val mediaProjectionLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { result ->
        if (result.resultCode == Activity.RESULT_OK && result.data != null) {
            MediaProjectionHelper.saveResult(result.resultCode, result.data)
            launchCollection()
        } else {
            Toast.makeText(this, "MediaProjection permission denied", Toast.LENGTH_LONG).show()
            tvStatus.text = "Status: Permission denied"
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        mediaProjectionManager =
            getSystemService(Context.MEDIA_PROJECTION_SERVICE) as MediaProjectionManager

        val layout = android.widget.LinearLayout(this).apply {
            orientation = android.widget.LinearLayout.VERTICAL
            setPadding(32, 32, 32, 32)
        }

        tvStatus = TextView(this).apply {
            text = "Status: Idle"
            textSize = 18f
        }
        layout.addView(tvStatus)

        etServerIp = EditText(this).apply {
            hint = "Server IP (e.g., 10.0.2.2)"
            setText("10.0.2.2")
        }
        layout.addView(etServerIp)

        etServerPort = EditText(this).apply {
            hint = "Server Port"
            setText("12345")
            inputType = android.text.InputType.TYPE_CLASS_NUMBER
        }
        layout.addView(etServerPort)

        etTargetPackage = EditText(this).apply {
            hint = "Target Package (e.g., com.android.calculator2)"
            setText("com.android.calculator2")
        }
        layout.addView(etTargetPackage)

        btnAccessibility = Button(this).apply {
            text = "Open Accessibility Settings"
            setOnClickListener {
                startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
            }
        }
        layout.addView(btnAccessibility)

        btnStart = Button(this).apply {
            text = "Start Collection"
            setOnClickListener { startCollection() }
        }
        layout.addView(btnStart)

        btnStop = Button(this).apply {
            text = "Stop Collection"
            isEnabled = false
            setOnClickListener { stopCollection() }
        }
        layout.addView(btnStop)

        setContentView(layout)
    }

    private fun startCollection() {
        val service = CollectorService.instance
        if (service == null) {
            Toast.makeText(this, "Enable Accessibility Service first", Toast.LENGTH_LONG).show()
            return
        }

        val ip = etServerIp.text.toString().trim()
        val port = etServerPort.text.toString().trim().toIntOrNull() ?: 12345
        val pkg = etTargetPackage.text.toString().trim()

        if (ip.isEmpty() || pkg.isEmpty()) {
            Toast.makeText(this, "Fill in all fields", Toast.LENGTH_SHORT).show()
            return
        }

        // Save params and request MediaProjection permission
        pendingIp = ip
        pendingPort = port
        pendingPkg = pkg

        tvStatus.text = "Status: Requesting permission..."
        mediaProjectionLauncher.launch(
            mediaProjectionManager.createScreenCaptureIntent()
        )
    }

    private fun launchCollection() {
        val service = CollectorService.instance ?: return

        // Get screen metrics
        val metrics = DisplayMetrics()
        @Suppress("DEPRECATION")
        windowManager.defaultDisplay.getMetrics(metrics)

        service.startCollection(
            serverIp = pendingIp,
            serverPort = pendingPort,
            targetPkg = pendingPkg,
            screenWidth = metrics.widthPixels,
            screenHeight = metrics.heightPixels,
            screenDensityDpi = metrics.densityDpi
        )

        tvStatus.text = "Status: Collecting ($pendingPkg)"
        btnStart.isEnabled = false
        btnStop.isEnabled = true
    }

    private fun stopCollection() {
        CollectorService.instance?.stopCollection()
        tvStatus.text = "Status: Stopped"
        btnStart.isEnabled = true
        btnStop.isEnabled = false
    }
}
