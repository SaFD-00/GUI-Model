package com.monkey.collector

import android.view.accessibility.AccessibilityNodeInfo

object XmlDumper {

    fun dumpNodeTree(root: AccessibilityNodeInfo?): String {
        if (root == null) return "<hierarchy rotation=\"0\" />"

        val sb = StringBuilder()
        sb.append("<?xml version=\"1.0\" encoding=\"UTF-8\"?>")
        sb.append("<hierarchy rotation=\"0\">")
        dumpNode(root, sb, 0)
        sb.append("</hierarchy>")
        return sb.toString()
    }

    private fun dumpNode(node: AccessibilityNodeInfo, sb: StringBuilder, index: Int) {
        sb.append("<node")

        // Attributes
        sb.appendAttr("index", index.toString())
        sb.appendAttr("text", sanitize(node.text?.toString() ?: ""))
        sb.appendAttr("resource-id", node.viewIdResourceName ?: "")
        sb.appendAttr("class", node.className?.toString() ?: "")
        sb.appendAttr("package", node.packageName?.toString() ?: "")
        sb.appendAttr("content-desc", sanitize(node.contentDescription?.toString() ?: ""))
        sb.appendAttr("checkable", node.isCheckable.toString())
        sb.appendAttr("checked", node.isChecked.toString())
        sb.appendAttr("clickable", node.isClickable.toString())
        sb.appendAttr("enabled", node.isEnabled.toString())
        sb.appendAttr("focusable", node.isFocusable.toString())
        sb.appendAttr("focused", node.isFocused.toString())
        sb.appendAttr("scrollable", node.isScrollable.toString())
        sb.appendAttr("long-clickable", node.isLongClickable.toString())
        sb.appendAttr("password", node.isPassword.toString())
        sb.appendAttr("selected", node.isSelected.toString())
        sb.appendAttr("visible-to-user", node.isVisibleToUser.toString())

        // Bounds
        val rect = android.graphics.Rect()
        node.getBoundsInScreen(rect)
        sb.appendAttr("bounds", "[${rect.left},${rect.top}][${rect.right},${rect.bottom}]")

        // Important for accessibility
        sb.appendAttr("important", node.isImportantForAccessibility.toString())

        val childCount = node.childCount
        if (childCount > 0) {
            sb.append(">")
            for (i in 0 until childCount) {
                val child = node.getChild(i)
                if (child != null) {
                    if (child.isVisibleToUser) {
                        dumpNode(child, sb, i)
                    }
                    child.recycle()
                }
            }
            sb.append("</node>")
        } else {
            sb.append(" />")
        }
    }

    private fun StringBuilder.appendAttr(name: String, value: String) {
        append(" $name=\"${escapeXml(value)}\"")
    }

    private fun escapeXml(text: String): String {
        return text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("\"", "&quot;")
            .replace("'", "&apos;")
    }

    private fun sanitize(text: String): String {
        return text.replace("\n", " ").replace("\r", "").trim()
    }
}
