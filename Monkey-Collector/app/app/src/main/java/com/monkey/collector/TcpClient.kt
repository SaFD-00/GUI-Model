package com.monkey.collector

import android.graphics.Bitmap
import android.util.Log
import java.io.ByteArrayOutputStream
import java.io.DataOutputStream
import java.io.IOException
import java.net.Socket
import java.nio.charset.StandardCharsets

class TcpClient(
    private val serverIp: String,
    private val serverPort: Int
) {
    companion object {
        private const val TAG = "TcpClient"
        private const val MAX_RETRIES = 3
        private const val RETRY_DELAY_MS = 2000L
    }

    private var socket: Socket? = null
    private var dos: DataOutputStream? = null
    private val writeLock = Any()

    @Volatile
    private var connected = false

    fun connect(): Boolean {
        for (attempt in 1..MAX_RETRIES) {
            try {
                socket = Socket(serverIp, serverPort)
                dos = DataOutputStream(socket!!.getOutputStream())
                connected = true
                Log.i(TAG, "Connected to $serverIp:$serverPort (attempt $attempt)")
                return true
            } catch (e: IOException) {
                Log.e(TAG, "Connection attempt $attempt/$MAX_RETRIES failed: ${e.message}")
                if (attempt < MAX_RETRIES) {
                    try {
                        Thread.sleep(RETRY_DELAY_MS)
                    } catch (_: InterruptedException) {
                        break
                    }
                }
            }
        }
        Log.e(TAG, "Failed to connect after $MAX_RETRIES attempts")
        return false
    }

    fun disconnect() {
        connected = false
        synchronized(writeLock) {
            try {
                dos?.close()
                socket?.close()
            } catch (e: IOException) {
                Log.e(TAG, "Disconnect error: ${e.message}")
            } finally {
                dos = null
                socket = null
            }
        }
    }

    fun isConnected(): Boolean = connected

    fun sendScreenshot(bitmap: Bitmap): Boolean {
        if (!connected) return false
        return try {
            val baos = ByteArrayOutputStream()
            bitmap.compress(Bitmap.CompressFormat.JPEG, 90, baos)
            val imageBytes = baos.toByteArray()

            synchronized(writeLock) {
                val out = dos ?: return false
                out.writeByte('S'.code)
                out.write("${imageBytes.size}\n".toByteArray(StandardCharsets.UTF_8))
                out.write(imageBytes)
                out.flush()
            }
            Log.d(TAG, "Screenshot sent: ${imageBytes.size} bytes")
            true
        } catch (e: IOException) {
            Log.e(TAG, "sendScreenshot failed: ${e.message}")
            connected = false
            false
        } catch (e: Exception) {
            Log.e(TAG, "sendScreenshot error: ${e.message}")
            false
        }
    }

    fun sendXml(xml: String, topPackage: String, activityName: String, targetPackage: String, isFirstScreen: Boolean = false): Boolean {
        if (!connected) return false
        return try {
            val xmlBytes = xml.toByteArray(StandardCharsets.UTF_8)

            synchronized(writeLock) {
                val out = dos ?: return false
                out.writeByte('X'.code)
                out.write("$topPackage\n".toByteArray(StandardCharsets.UTF_8))
                out.write("$activityName\n".toByteArray(StandardCharsets.UTF_8))
                out.write("$targetPackage\n".toByteArray(StandardCharsets.UTF_8))
                out.write("${if (isFirstScreen) "1" else "0"}\n".toByteArray(StandardCharsets.UTF_8))
                out.write("${xmlBytes.size}\n".toByteArray(StandardCharsets.UTF_8))
                out.write(xmlBytes)
                out.flush()
            }
            Log.d(TAG, "XML sent: ${xmlBytes.size} bytes (top=$topPackage, activity=$activityName)")
            true
        } catch (e: IOException) {
            Log.e(TAG, "sendXml failed: ${e.message}")
            connected = false
            false
        } catch (e: Exception) {
            Log.e(TAG, "sendXml error: ${e.message}")
            false
        }
    }

    fun sendExternalApp(topPackage: String, targetPackage: String): Boolean {
        if (!connected) return false
        return try {
            val json = """{"detected_package":"$topPackage","target_package":"$targetPackage"}"""

            synchronized(writeLock) {
                val out = dos ?: return false
                out.writeByte('E'.code)
                out.write("$json\n".toByteArray(StandardCharsets.UTF_8))
                out.flush()
            }
            Log.d(TAG, "ExternalApp sent: $topPackage")
            true
        } catch (e: IOException) {
            Log.e(TAG, "sendExternalApp failed: ${e.message}")
            connected = false
            false
        } catch (e: Exception) {
            Log.e(TAG, "sendExternalApp error: ${e.message}")
            false
        }
    }

    fun sendPackageName(targetPackage: String): Boolean {
        if (!connected) return false
        return try {
            synchronized(writeLock) {
                val out = dos ?: return false
                out.writeByte('P'.code)
                out.write("$targetPackage\n".toByteArray(StandardCharsets.UTF_8))
                out.flush()
            }
            Log.d(TAG, "PackageName sent: $targetPackage")
            true
        } catch (e: IOException) {
            Log.e(TAG, "sendPackageName failed: ${e.message}")
            connected = false
            false
        } catch (e: Exception) {
            Log.e(TAG, "sendPackageName error: ${e.message}")
            false
        }
    }

    fun sendFinish(): Boolean {
        if (!connected) return false
        return try {
            synchronized(writeLock) {
                val out = dos ?: return false
                out.writeByte('F'.code)
                out.flush()
            }
            Log.d(TAG, "Finish signal sent")
            true
        } catch (e: IOException) {
            Log.e(TAG, "sendFinish failed: ${e.message}")
            connected = false
            false
        } catch (e: Exception) {
            Log.e(TAG, "sendFinish error: ${e.message}")
            false
        }
    }

    fun sendNoChange(): Boolean {
        if (!connected) return false
        return try {
            synchronized(writeLock) {
                val out = dos ?: return false
                out.writeByte('N'.code)
                out.flush()
            }
            Log.d(TAG, "NoChange signal sent")
            true
        } catch (e: IOException) {
            Log.e(TAG, "sendNoChange failed: ${e.message}")
            connected = false
            false
        } catch (e: Exception) {
            Log.e(TAG, "sendNoChange error: ${e.message}")
            false
        }
    }
}
