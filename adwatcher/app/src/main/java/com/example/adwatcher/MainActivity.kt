package com.example.adwatcher

import android.Manifest
import android.accessibilityservice.AccessibilityServiceInfo
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Bundle
import android.provider.Settings
import android.text.method.ScrollingMovementMethod
import android.view.accessibility.AccessibilityManager
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import com.example.adwatcher.databinding.ActivityMainBinding

/**
 * 主界面：权限引导 + 服务状态展示 + 实时日志。
 */
class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding

    /** 通知权限申请回调（Android 13+） */
    private val notifPermissionLauncher =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            if (!granted) {
                LogStore.log("通知权限被拒绝：前台服务通知将无法显示")
            }
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        // 让日志 TextView 可滚动
        binding.logView.movementMethod = ScrollingMovementMethod.getInstance()

        // 监听日志
        LogStore.liveLog.observe(this) { text ->
            binding.logView.text = text
        }

        // 按钮：跳转无障碍设置
        binding.btnOpenAccessibility.setOnClickListener {
            startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
        }

        // 按钮：跳转应用详情（授予通知权限的兜底入口）
        binding.btnOpenNotifSettings.setOnClickListener {
            requestNotificationPermission()
        }

        // 按钮：清空日志
        binding.btnClearLog.setOnClickListener {
            LogStore.clear()
        }

        // 按钮：通过 adb 启用服务（开发者备用入口，免跳转系统设置）
        binding.btnAdbHint.setOnClickListener {
            val intent = Intent(Intent.ACTION_VIEW, Uri.parse("https://developer.android.com/studio/command-line/adb"))
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            try {
                startActivity(intent)
            } catch (_: Exception) {
                LogStore.log("无浏览器可用，请查阅 adb 命令文档")
            }
        }

        LogStore.log("App 已启动")
    }

    override fun onResume() {
        super.onResume()
        refreshServiceState()
        requestNotificationPermission()
    }

    /** 检查无障碍服务是否已启用，并更新界面 */
    private fun refreshServiceState() {
        val enabled = isAccessibilityServiceEnabled(this, AdAccessibilityService::class.java)
        binding.tvServiceState.text = if (enabled) {
            "无障碍服务：已启用 ✓"
        } else {
            "无障碍服务：未启用（点击下方按钮开启）"
        }
        binding.tvServiceState.setBackgroundColor(
            ContextCompat.getColor(
                this,
                if (enabled) android.R.color.holo_green_light else android.R.color.holo_orange_light
            )
        )
    }

    /** 申请通知权限（Android 13+ 显式申请，低版本直接授予） */
    private fun requestNotificationPermission() {
        if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.TIRAMISU) {
            val granted = ContextCompat.checkSelfPermission(
                this, Manifest.permission.POST_NOTIFICATIONS
            ) == PackageManager.PERMISSION_GRANTED
            if (!granted) {
                notifPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
            }
        }
    }

    companion object {
        /** 判断指定的无障碍服务是否在系统设置中已启用 */
        fun isAccessibilityServiceEnabled(
            context: Context,
            service: Class<out android.accessibilityservice.AccessibilityService>
        ): Boolean {
            val am = context.getSystemService(Context.ACCESSIBILITY_SERVICE) as AccessibilityManager
            val enabled = am.getEnabledAccessibilityServiceList(AccessibilityServiceInfo.FEEDBACK_GENERIC)
            val target = "${context.packageName}/${service.name}"
            return enabled.any { it.resolveInfo.serviceInfo.let { info ->
                "${info.packageName}/${info.name}" == target
            } }
        }
    }
}
