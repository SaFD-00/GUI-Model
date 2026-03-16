package com.monkey.collector

import android.graphics.Bitmap
import android.util.Log
import java.io.ByteArrayOutputStream
import java.io.DataOutputStream
import java.io.IOException
import java.net.Socket
import java.nio.charset.StandardCharsets
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.atomic.AtomicBoolean

class TcpClient(
    private val serverIp: String,
    private val serverPort: Int
) {
    companion object {
        private const val TAG = "TcpClient"
    }

    private var socket: Socket? = null
    private var dos: DataOutputStream? = null
    private val connected = AtomicBoolean(false)
    private val sendQueue = LinkedBlockingQueue<ByteArray>()
    private var senderThread: Thread? = null

    fun connect() {
        try {
            socket = Socket(serverIp, serverPort)
            dos = DataOutputStream(socket!!.getOutputStream())
            connected.set(true)
            startSenderThread()
            Log.i(TAG, "Connected to $serverIp:$serverPort")
        } catch (e: IOException) {
            Log.e(TAG, "Connection failed: ${e.message}")
        }
    }

    fun disconnect() {
        connected.set(false)
        try {
            dos?.close()
            socket?.close()
        } catch (e: IOException) {
            Log.e(TAG, "Disconnect error: ${e.message}")
        }
    }

    fun sendScreenshot(bitmap: Bitmap) {
        if (!connected.get()) return
        try {
            val baos = ByteArrayOutputStream()
            bitmap.compress(Bitmap.CompressFormat.JPEG, 90, baos)
            val imageBytes = baos.toByteArray()

            val header = ByteArrayOutputStream()
            header.write('S'.code)
            header.write("${imageBytes.size}\n".toByteArray(StandardCharsets.UTF_8))

            val message = header.toByteArray() + imageBytes
            sendQueue.put(message)
        } catch (e: Exception) {
            Log.e(TAG, "sendScreenshot error: ${e.message}")
        }
    }

    fun sendXml(xml: String, topPackage: String, targetPackage: String) {
        if (!connected.get()) return
        try {
            val xmlBytes = xml.toByteArray(StandardCharsets.UTF_8)

            val baos = ByteArrayOutputStream()
            baos.write('X'.code)
            baos.write("$topPackage\n".toByteArray(StandardCharsets.UTF_8))
            baos.write("$targetPackage\n".toByteArray(StandardCharsets.UTF_8))
            baos.write("${xmlBytes.size}\n".toByteArray(StandardCharsets.UTF_8))
            baos.write(xmlBytes)

            sendQueue.put(baos.toByteArray())
        } catch (e: Exception) {
            Log.e(TAG, "sendXml error: ${e.message}")
        }
    }

    fun sendExternalApp(topPackage: String, targetPackage: String) {
        if (!connected.get()) return
        try {
            val json = """{"detected_package":"$topPackage","target_package":"$targetPackage"}"""
            val baos = ByteArrayOutputStream()
            baos.write('E'.code)
            baos.write("$json\n".toByteArray(StandardCharsets.UTF_8))
            sendQueue.put(baos.toByteArray())
        } catch (e: Exception) {
            Log.e(TAG, "sendExternalApp error: ${e.message}")
        }
    }

    fun sendFinish() {
        if (!connected.get()) return
        try {
            sendQueue.put(byteArrayOf('F'.code.toByte()))
        } catch (e: Exception) {
            Log.e(TAG, "sendFinish error: ${e.message}")
        }
    }

    private fun startSenderThread() {
        senderThread = Thread {
            while (connected.get()) {
                try {
                    val data = sendQueue.poll(1, java.util.concurrent.TimeUnit.SECONDS)
                    if (data != null) {
                        dos?.write(data)
                        dos?.flush()
                    }
                } catch (e: InterruptedException) {
                    break
                } catch (e: IOException) {
                    Log.e(TAG, "Send error: ${e.message}")
                    connected.set(false)
                    break
                }
            }
        }.apply {
            isDaemon = true
            start()
        }
    }
}
