package com.monkey.collector

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.media.projection.MediaProjectionManager
import android.os.Bundle
import android.provider.Settings
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity

class MainActivity : AppCompatActivity() {

    private lateinit var etServerIp: EditText
    private lateinit var etServerPort: EditText
    private lateinit var tvStatus: TextView
    private lateinit var btnSave: Button
    private lateinit var btnAccessibility: Button

    private lateinit var mediaProjectionManager: MediaProjectionManager

    private val mediaProjectionLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { result ->
        if (result.resultCode == Activity.RESULT_OK && result.data != null) {
            MediaProjectionHelper.saveResult(result.resultCode, result.data)
            Toast.makeText(
                this,
                "Ready! Open the target app and tap the floating ▶ button to start.",
                Toast.LENGTH_LONG
            ).show()
            finish()
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
            text = "Status: Configure server and tap Save & Ready"
            textSize = 18f
        }
        layout.addView(tvStatus)

        // Load saved settings
        val prefs = getSharedPreferences("collector_settings", MODE_PRIVATE)

        etServerIp = EditText(this).apply {
            hint = "Server IP (e.g., 10.0.2.2)"
            setText(prefs.getString("server_ip", "10.0.2.2"))
        }
        layout.addView(etServerIp)

        etServerPort = EditText(this).apply {
            hint = "Server Port"
            setText(prefs.getInt("server_port", 12345).toString())
            inputType = android.text.InputType.TYPE_CLASS_NUMBER
        }
        layout.addView(etServerPort)

        // Info text
        layout.addView(TextView(this).apply {
            text = "Target app is auto-detected when you press the floating ▶ button."
            textSize = 14f
            setPadding(0, 16, 0, 16)
        })

        btnAccessibility = Button(this).apply {
            text = "Open Accessibility Settings"
            setOnClickListener {
                startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
            }
        }
        layout.addView(btnAccessibility)

        btnSave = Button(this).apply {
            text = "Save & Ready"
            setOnClickListener { saveAndReady() }
        }
        layout.addView(btnSave)

        setContentView(layout)
    }

    private fun saveAndReady() {
        val service = CollectorService.instance
        if (service == null) {
            Toast.makeText(this, "Enable Accessibility Service first", Toast.LENGTH_LONG).show()
            return
        }

        val ip = etServerIp.text.toString().trim()
        val port = etServerPort.text.toString().trim().toIntOrNull() ?: 12345

        if (ip.isEmpty()) {
            Toast.makeText(this, "Enter server IP", Toast.LENGTH_SHORT).show()
            return
        }

        // Save server settings to SharedPreferences
        getSharedPreferences("collector_settings", MODE_PRIVATE).edit()
            .putString("server_ip", ip)
            .putInt("server_port", port)
            .apply()

        tvStatus.text = "Status: Requesting permission..."
        mediaProjectionLauncher.launch(
            mediaProjectionManager.createScreenCaptureIntent()
        )
    }
}
