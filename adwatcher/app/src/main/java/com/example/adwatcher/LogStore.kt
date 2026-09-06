package com.example.adwatcher

import androidx.lifecycle.MutableLiveData
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * 进程内的日志缓冲单例。
 *
 * 无障碍服务在子线程中写入日志，主界面通过 [liveLog] 观察并展示。
 * 使用容量受限的环形缓冲，避免日志无限增长导致内存问题。
 */
object LogStore {
    private const val MAX_LINES = 500
    private val formatter = SimpleDateFormat("HH:mm:ss", Locale.getDefault())

    private val buffer = ArrayDeque<String>()
    private val lock = Any()

    /** 主界面观察的日志文本（拼接好的多行字符串） */
    val liveLog = MutableLiveData<String>()

    fun log(msg: String) {
        val line = "${formatter.format(Date())}  $msg"
        synchronized(lock) {
            buffer.addLast(line)
            while (buffer.size > MAX_LINES) buffer.removeFirst()
        }
        liveLog.postValue(buildString())
    }

    fun clear() {
        synchronized(lock) { buffer.clear() }
        liveLog.postValue("")
    }

    fun buildString(): String = synchronized(lock) {
        buffer.joinToString("\n")
    }
}
