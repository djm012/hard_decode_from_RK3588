#!/usr/bin/env python3
# rkmpp_video_player_optimized.py
# RK3588 高性能播放（硬解码 NV12 -> Python 统计/按间隔保存）
# 要点：
#  - XInitThreads() 在最开始
#  - 使用 mppvideodec 输出 NV12，减少拷贝
#  - appsink 在回调中只做快速拷贝入队列
#  - 工作线程只做计数，按需低频保存图片
#  - 队列满时丢弃最旧帧，保证实时性

import ctypes
# 在导入 gi / cv2 之前初始化 X11 线程支持，防止 xcb 多线程崩溃
for lib in ("libX11.so.6", "libX11.so"):
    try:
        ctypes.CDLL(lib).XInitThreads()
        print(f"✅ XInitThreads() 初始化成功 ({lib})")
        break
    except Exception as e:
        print(f"⚠️ XInitThreads() 加载 {lib} 失败: {e}")

import sys
import time
import threading
import queue
import signal
import numpy as np

import gi
gi.require_version('Gst', '1.0')
gi.require_version('GstApp', '1.0')
from gi.repository import Gst, GLib, GstApp

import cv2

Gst.init(None)

# ---------- 配置 ----------
RTSP_URL_DEFAULT = "rtsp://172.10.50.34:554/rtp/34020000001110000009_34020000001310000001?originTypeStr=rtp_push"
LATENCY_MS = 100               # rtspsrc latency
QUEUE_MAXSIZE = 2              # 队列缓冲，max 2 帧
SAVE_FRAME_INTERVAL = 300      # 每隔多少解码帧保存一张，0 表示不保存
SAVE_FRAME_DIR = "."           # 保存目录
# -----------------------

class RKMPPOptimizedPlayer:
    def __init__(self, rtsp_url):
        self.rtsp_url = rtsp_url
        self.pipeline = None
        self.appsink = None
        self.frame_q = queue.Queue(maxsize=QUEUE_MAXSIZE)  # 存 NV12 ndarray (h*3/2, w)
        self.running = threading.Event()
        self.stop_event = threading.Event()

        # 统计
        self.decode_count = 0
        self.process_count = 0
        self.decode_start = time.time()
        self.saved_count = 0

        # threads
        self.gst_thread = None
        self.process_thread = None

    def build_pipeline(self):
        # mppvideodec 输出 NV12，appsink 取 NV12 原始数据
        pipeline_str = (
            f"rtspsrc location={self.rtsp_url} latency={LATENCY_MS} ! "
            "rtph264depay ! h264parse ! "
            "mppvideodec ! "
            "video/x-raw,format=NV12 ! "
            "appsink name=sink emit-signals=true max-buffers=1 drop=true sync=false"
        )
        print("🔧 创建管线:")
        print(pipeline_str)
        self.pipeline = Gst.parse_launch(pipeline_str)
        if not self.pipeline:
            raise RuntimeError("无法创建 GStreamer 管线")

        self.appsink = self.pipeline.get_by_name("sink")
        if not self.appsink:
            raise RuntimeError("找不到 appsink (name=sink)")

        # 设置 appsink 属性
        self.appsink.set_property("emit-signals", True)
        self.appsink.set_property("max-buffers", 1)
        self.appsink.set_property("drop", True)
        self.appsink.set_property("sync", False)
        # 连接 new-sample 回调
        self.appsink.connect("new-sample", self.on_new_sample)

        # 绑定 bus 消息
        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self.on_bus_message)

    def on_bus_message(self, bus, message):
        t = message.type
        if t == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            print(f"❌ GStreamer ERROR: {err}, {debug}")
            self.stop()
        elif t == Gst.MessageType.EOS:
            print("📺 GStreamer EOS")
            self.stop()
        elif t == Gst.MessageType.STATE_CHANGED:
            try:
                if message.src == self.pipeline:
                    old, new, pending = message.parse_state_changed()
                    print(f"🔄 Pipeline state: {old.value_nick} -> {new.value_nick}")
            except Exception:
                pass

    def on_new_sample(self, sink) -> Gst.FlowReturn:
        """appsink 的回调：快速把 NV12 数据拷贝到队列并返回"""
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.OK

        buf = sample.get_buffer()
        caps = sample.get_caps()
        if not caps:
            return Gst.FlowReturn.OK

        s = caps.get_structure(0)
        try:
            width = s.get_int('width')[1]
            height = s.get_int('height')[1]
        except Exception:
            return Gst.FlowReturn.OK

        success, mapinfo = buf.map(Gst.MapFlags.READ)
        if not success:
            return Gst.FlowReturn.OK

        try:
            data = mapinfo.data  # bytes-like
            expected = int(height * width * 3 / 2)
            if len(data) < expected:
                # 数据长度不足，丢弃
                print(f"⚠️ NV12 数据长度 {len(data)} < 期望 {expected}, 丢弃")
                buf.unmap(mapinfo)
                return Gst.FlowReturn.OK

            # 将 NV12 bytes 转为 numpy uint8 并 reshape 为 (h*3/2, w)
            arr = np.frombuffer(data[:expected], dtype=np.uint8).copy()
            nv12 = arr.reshape((height * 3 // 2, width))
        except Exception as e:
            print(f"⚠️ 处理 NV12 数据异常: {e}")
            buf.unmap(mapinfo)
            return Gst.FlowReturn.OK
        finally:
            buf.unmap(mapinfo)

        # 非阻塞入队：队满则丢弃最旧一帧后再入队
        try:
            self.frame_q.put_nowait(nv12)
        except queue.Full:
            try:
                _ = self.frame_q.get_nowait()
            except queue.Empty:
                pass
            try:
                self.frame_q.put_nowait(nv12)
            except queue.Full:
                pass

        # 解码统计
        self.decode_count += 1
        return Gst.FlowReturn.OK

    def gst_worker(self):
        try:
            self.build_pipeline()
        except Exception as e:
            print(f"❌ build_pipeline 失败: {e}")
            return

        ret = self.pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            print("❌ 无法把 pipeline 设为 PLAYING")
            return
        print("🚀 pipeline -> PLAYING")
        self.running.set()

        # 保留线程运行，bus 回调与 appsink 回调在内部线程处理
        while not self.stop_event.is_set():
            time.sleep(0.1)

        try:
            self.pipeline.set_state(Gst.State.NULL)
        except Exception:
            pass
        print("🛑 gst_worker 退出")

    def process_worker(self):
        last_report = time.time()
        last_process_count = 0
        while not self.stop_event.is_set():
            try:
                nv12 = self.frame_q.get(timeout=0.5)
            except queue.Empty:
                continue

            self.process_count += 1

            # 低频保存一帧用于确认解码结果，避免频繁转换/写盘拖慢解码
            if SAVE_FRAME_INTERVAL > 0 and self.process_count % SAVE_FRAME_INTERVAL == 0:
                try:
                    bgr = cv2.cvtColor(nv12, cv2.COLOR_YUV2BGR_NV12)
                    self.saved_count += 1
                    path = f"{SAVE_FRAME_DIR}/frame_{self.process_count:06d}.jpg"
                    cv2.imwrite(path, bgr)
                    print(f"💾 保存: {path}")
                except Exception as e:
                    print(f"⚠️ 保存帧失败: {e}")

            now = time.time()
            if now - last_report >= 1.0:
                process_fps = self.process_count - last_process_count
                decode_fps_avg = self.decode_count / (now - self.decode_start) if self.decode_count > 0 else 0.0
                print(f"📊 decode_total_avg={decode_fps_avg:.2f} fps, process_fps(this_sec)={process_fps}, queue={self.frame_q.qsize()}, saved={self.saved_count}")
                last_report = now
                last_process_count = self.process_count

        print("🛑 process_worker 退出")

    def start(self):
        # start gst thread
        self.gst_thread = threading.Thread(target=self.gst_worker, daemon=True)
        self.gst_thread.start()

        # wait until running or timeout
        start_wait = time.time()
        while not self.running.is_set():
            if time.time() - start_wait > 5.0:
                print("⚠️ GST pipeline 未能在 5s 内变为 PLAYING")
                break
            time.sleep(0.05)

        # start process thread
        self.process_thread = threading.Thread(target=self.process_worker, daemon=True)
        self.process_thread.start()

    def stop(self):
        print("⏹ 停止播放器...")
        self.stop_event.set()
        self.running.clear()
        try:
            if self.pipeline:
                self.pipeline.set_state(Gst.State.NULL)
        except Exception:
            pass

    def wait(self):
        try:
            while not self.stop_event.is_set():
                time.sleep(0.2)
        except KeyboardInterrupt:
            print("\n🛑 捕获 Ctrl+C，准备退出")
            self.stop()

        # join threads
        if self.gst_thread:
            self.gst_thread.join(timeout=1.0)
        if self.process_thread:
            self.process_thread.join(timeout=1.0)

if __name__ == "__main__":
    url = RTSP_URL_DEFAULT
    if len(sys.argv) > 1:
        url = sys.argv[1]
    print("使用流:", url)

    player = RKMPPOptimizedPlayer(url)
    player.start()
    player.wait()
    print("程序退出。")


# 多路视频rtsp地址：
# rtsp://172.10.50.34:554/rtp/34020000001110000009_34020000001310000001?originTypeStr=rtp_push
# rtsp://172.10.50.34:554/rtp/34020000001110000008_34020000001310000002?originTypeStr=rtp_push