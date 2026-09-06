package com.example.adwatcher

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.graphics.Path
import android.os.Build
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import androidx.core.app.NotificationCompat
import java.util.regex.Pattern

/**
 * 监听微信小程序广告读秒的无障碍服务。
 *
 * 工作流程：
 * 1. 微信（含小程序进程）触发窗口内容变化事件。
 * 2. 服务遍历当前活动窗口的节点树，匹配广告读秒/跳过文本。
 * 3. 解析出剩余秒数，并尝试定位"跳过"按钮。
 * 4. 当读秒结束或跳过按钮出现可点击节点时，自动点击跳过。
 * 5. 将识别与点击过程记录到 [LogStore]，供主界面展示。
 */
class AdAccessibilityService : AccessibilityService() {

    companion object {
        private const val CHANNEL_ID = "ad_watcher_foreground"
        private const val NOTI_ID = 1001

        /** 微信相关包名前缀，用于快速过滤无关事件 */
        private const val WECHAT_PREFIX = "com.tencent.mm"

        /** 跳过按钮文本正则：覆盖 "跳过 5s"、"跳过5秒"、"5s 跳过"、"跳过广告"、"Skip 3s" 等常见形态 */
        private val SKIP_PATTERN: Pattern = Pattern.compile(
            "(跳过广告|关闭广告|跳过|Skip\\s*Ad|Skip).*(\\d+)\\s*(s|秒|ｓ|S)" +
                "|(\\d+)\\s*(s|秒|ｓ|S).*(跳过广告|关闭广告|跳过|Skip\\s*Ad|Skip)"
        )

        /** 纯读秒文本（无"跳过"字样但出现在广告区域），如 "5s"、"3 秒" */
        private val COUNTDOWN_PATTERN: Pattern = Pattern.compile("^\\s*(\\d+)\\s*(s|秒|ｓ|S)\\s*\$")

        /** 可点击的跳过按钮文本（不再带秒数，表示可立即跳过） */
        private val SKIP_READY_PATTERN: Pattern =
            Pattern.compile("^(跳过广告|关闭广告|跳过|Skip\\s*Ad|Skip)\$")
    }

    /** 最近一次匹配到的读秒值，用于判断"是否读秒结束" */
    @Volatile private var lastSeconds: Int = Int.MAX_VALUE
    /** 节流：同一广告场景下，避免重复点击，记录最近一次点击时间 */
    @Volatile private var lastClickTimeMs: Long = 0L

    override fun onServiceConnected() {
        super.onServiceConnected()
        LogStore.log("无障碍服务已启用，开始监听微信广告读秒")
        startForegroundIfNeeded()
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent) {
        val pkg = event.packageName?.toString() ?: return
        // 只处理微信及其小程序进程的事件
        if (!pkg.startsWith(WECHAT_PREFIX)) return

        val source = event.source ?: return
        try {
            handleNode(source)
        } catch (e: Exception) {
            // 抓取异常避免服务被系统杀掉
            LogStore.log("处理事件异常: ${e.message}")
        }
    }

    /**
     * 遍历节点树，匹配广告读秒与跳过按钮。
     * 采用 BFS 避免深度过深导致栈溢出，同时限制最大节点数防止性能问题。
     */
    private fun handleNode(root: AccessibilityNodeInfo) {
        val queue = ArrayDeque<AccessibilityNodeInfo>()
        queue.add(root)
        var visited = 0
        val maxNodes = 500

        var skipNode: AccessibilityNodeInfo? = null
        var countdownText: String? = null

        while (queue.isNotEmpty() && visited < maxNodes) {
            val node = queue.removeFirst()
            visited++

            val text = node.text?.toString().orEmpty()
            val desc = node.contentDescription?.toString().orEmpty()
            val combined = if (text.isNotEmpty()) text else desc

            if (combined.isNotEmpty()) {
                // 1) 优先匹配带秒数的跳过按钮，如 "跳过 5s"
                val m = SKIP_PATTERN.matcher(combined)
                if (m.find()) {
                    val seconds = extractSeconds(combined)
                    if (seconds != null) {
                        countdownText = combined
                        LogStore.log("识别到广告读秒: $combined（剩余 ${seconds}s），立即尝试跳过")
                        // 检测到读秒即尝试点击，不等待自然倒数
                        skipNode = findClickableAncestorOrSelf(node) ?: node
                    }
                }

                // 2) 匹配可立即点击的"跳过"按钮
                if (skipNode == null && SKIP_READY_PATTERN.matcher(combined).matches()) {
                    LogStore.log("识别到可点击的跳过按钮: $combined")
                    skipNode = findClickableAncestorOrSelf(node) ?: node
                }

                // 3) 纯读秒文本（如广告中部的 "5s"）
                if (countdownText == null && COUNTDOWN_PATTERN.matcher(combined).matches()) {
                    countdownText = combined
                }
            }

            // 继续遍历子节点
            for (i in 0 until node.childCount) {
                node.getChild(i)?.let { queue.add(it) }
            }
        }

        // 如果只识别到读秒、还没出现跳过按钮，记录进度
        if (countdownText != null && skipNode == null) {
            val s = extractSeconds(countdownText!!)
            if (s != null) {
                lastSeconds = s.coerceAtLeast(0)
            }
            return
        }

        // 找到跳过按钮 → 点击
        if (skipNode != null) {
            performSkip(skipNode)
        }
    }

    /** 从文本中提取秒数 */
    private fun extractSeconds(text: String): Int? {
        val m = Pattern.compile("(\\d+)").matcher(text)
        return if (m.find()) m.group(1)?.toIntOrNull() else null
    }

    /**
     * 查找节点本身或最近的"可点击"祖先。
     * 微信小程序中跳过按钮的文本节点往往没有 clickable 属性，
     * 真正接收点击的是它的父容器。
     */
    private fun findClickableAncestorOrSelf(node: AccessibilityNodeInfo): AccessibilityNodeInfo? {
        var current: AccessibilityNodeInfo? = node
        var depth = 0
        while (current != null && depth < 10) {
            if (current.isClickable) return current
            current = current.parent
            depth++
        }
        return null
    }

    /**
     * 执行跳过点击。优先用 ACTION_CLICK，
     * 如果节点不可点击则回退到在节点中心做手势点击。
     * 同时做节流（≥2s 内不重复点击同一类按钮）。
     */
    private fun performSkip(node: AccessibilityNodeInfo) {
        val now = System.currentTimeMillis()
        if (now - lastClickTimeMs < 2000) {
            return
        }

        val clicked = node.performAction(AccessibilityNodeInfo.ACTION_CLICK)
        if (clicked) {
            lastClickTimeMs = now
            LogStore.log("已点击跳过按钮（ACTION_CLICK）")
        } else {
            // 回退：在节点中心坐标执行手势点击
            val rect = android.graphics.Rect()
            node.getBoundsInScreen(rect)
            val cx = rect.exactCenterX()
            val cy = rect.exactCenterY()
            val dispatched = dispatchTap(cx, cy)
            if (dispatched) {
                lastClickTimeMs = now
                LogStore.log("已点击跳过按钮（手势 @(${cx.toInt()},${cy.toInt()})）")
            } else {
                LogStore.log("跳过按钮点击失败")
            }
        }
    }

    /** 用 GestureDescription 在屏幕坐标点一次点击 */
    private fun dispatchTap(x: Float, y: Float): Boolean {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.N) return false
        val path = Path().apply { moveTo(x, y) }
        val stroke = GestureDescription.StrokeDescription(path, 0L, 80L)
        val gesture = GestureDescription.Builder().addStroke(stroke).build()
        return dispatchGesture(gesture, null, null)
    }

    override fun onInterrupt() {
        LogStore.log("无障碍服务被中断")
    }

    override fun onUnbind(intent: android.content.Intent?): Boolean {
        LogStore.log("无障碍服务已解绑")
        return super.onUnbind(intent)
    }

    /** 启动前台通知，避免服务被系统回收（Android 10+ 需前台服务通知） */
    private fun startForegroundIfNeeded() {
        val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                CHANNEL_ID,
                getString(R.string.noti_channel_name),
                NotificationManager.IMPORTANCE_LOW
            ).apply { description = getString(R.string.noti_channel_desc) }
            nm.createNotificationChannel(channel)
        }

        val noti = NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle(getString(R.string.app_name))
            .setContentText(getString(R.string.noti_running))
            .setSmallIcon(android.R.drawable.ic_media_play)
            .setOngoing(true)
            .build()

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            // Android 14 需声明前台服务类型
            startForeground(NOTI_ID, noti, android.content.pm.ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        } else {
            startForeground(NOTI_ID, noti)
        }
    }
}
