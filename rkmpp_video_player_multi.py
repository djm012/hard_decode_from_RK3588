#!/usr/bin/env python3
# rkmpp_video_player_optimized.py
# RK3588 高性能播放（硬解码 NV12 -> Python 统计/按间隔保存）
# 要点：
#  - 使用 mppvideodec 输出 NV12，减少拷贝
#  - appsink 在回调中只做快速拷贝入队列
#  - 工作线程只做计数，按需低频保存图片
#  - 队列满时丢弃最旧帧，保证实时性
#  - 支持一次传入多个 RTSP 地址，每路独立统计

import ctypes
import os
import sys
import time
import threading
import queue
import numpy as np

# 在导入 gi / cv2 之前初始化 X11 线程支持，防止 xcb 多线程崩溃
for lib in ("libX11.so.6", "libX11.so"):
    try:
        ctypes.CDLL(lib).XInitThreads()
        print(f"✅ XInitThreads() 初始化成功 ({lib})")
        break
    except Exception as e:
        print(f"⚠️ XInitThreads() 加载 {lib} 失败: {e}")

import gi
gi.require_version('Gst', '1.0')
gi.require_version('GstApp', '1.0')
from gi.repository import Gst

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
    def __init__(self, rtsp_url, name="stream"):
        self.rtsp_url = rtsp_url
        self.name = name
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

    def log(self, msg):
        print(f"[{self.name}] {msg}")

    def build_pipeline(self):
        pipeline_str = (
            f"rtspsrc location={self.rtsp_url} latency={LATENCY_MS} ! "
            "rtph264depay ! h264parse ! "
            "mppvideodec ! "
            "video/x-raw,format=NV12 ! "
            "appsink name=sink emit-signals=true max-buffers=1 drop=true sync=false"
        )
        self.log("🔧 创建管线:")
        print(pipeline_str)

        self.pipeline = Gst.parse_launch(pipeline_str)
        if not self.pipeline:
            raise RuntimeError("无法创建 GStreamer 管线")

        self.appsink = self.pipeline.get_by_name("sink")
        if not self.appsink:
            raise RuntimeError("找不到 appsink (name=sink)")

        self.appsink.set_property("emit-signals", True)
        self.appsink.set_property("max-buffers", 1)
        self.appsink.set_property("drop", True)
        self.appsink.set_property("sync", False)
        self.appsink.connect("new-sample", self.on_new_sample)

        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self.on_bus_message)

    def on_bus_message(self, bus, message):
        msg_type = message.type
        if msg_type == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            self.log(f"❌ GStreamer ERROR: {err}, {debug}")
            self.stop()
        elif msg_type == Gst.MessageType.EOS:
            self.log("📺 GStreamer EOS")
            self.stop()
        elif msg_type == Gst.MessageType.STATE_CHANGED:
            try:
                if message.src == self.pipeline:
                    old, new, pending = message.parse_state_changed()
                    self.log(f"🔄 Pipeline state: {old.value_nick} -> {new.value_nick}")
            except Exception:
                pass

    def on_new_sample(self, sink):
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.OK

        buf = sample.get_buffer()
        caps = sample.get_caps()
        if not caps:
            return Gst.FlowReturn.OK

        structure = caps.get_structure(0)
        try:
            width = structure.get_int('width')[1]
            height = structure.get_int('height')[1]
        except Exception:
            return Gst.FlowReturn.OK

        success, mapinfo = buf.map(Gst.MapFlags.READ)
        if not success:
            return Gst.FlowReturn.OK

        try:
            data = mapinfo.data
            expected = int(height * width * 3 / 2)
            if len(data) < expected:
                self.log(f"⚠️ NV12 数据长度 {len(data)} < 期望 {expected}, 丢弃")
                return Gst.FlowReturn.OK

            arr = np.frombuffer(data[:expected], dtype=np.uint8).copy()
            nv12 = arr.reshape((height * 3 // 2, width))
        except Exception as e:
            self.log(f"⚠️ 处理 NV12 数据异常: {e}")
            return Gst.FlowReturn.OK
        finally:
            buf.unmap(mapinfo)

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

        self.decode_count += 1
        return Gst.FlowReturn.OK

    def gst_worker(self):
        try:
            self.build_pipeline()
        except Exception as e:
            self.log(f"❌ build_pipeline 失败: {e}")
            return

        ret = self.pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            self.log("❌ 无法把 pipeline 设为 PLAYING")
            return

        self.log("🚀 pipeline -> PLAYING")
        self.running.set()

        while not self.stop_event.is_set():
            time.sleep(0.1)

        try:
            self.pipeline.set_state(Gst.State.NULL)
        except Exception:
            pass
        self.log("🛑 gst_worker 退出")

    def process_worker(self):
        last_report = time.time()
        last_process_count = 0

        while not self.stop_event.is_set():
            try:
                nv12 = self.frame_q.get(timeout=0.5)
            except queue.Empty:
                continue

            self.process_count += 1

            if SAVE_FRAME_INTERVAL > 0 and self.process_count % SAVE_FRAME_INTERVAL == 0:
                try:
                    bgr = cv2.cvtColor(nv12, cv2.COLOR_YUV2BGR_NV12)
                    self.saved_count += 1
                    path = os.path.join(SAVE_FRAME_DIR, f"{self.name}_frame_{self.process_count:06d}.jpg")
                    cv2.imwrite(path, bgr)
                    self.log(f"💾 保存: {path}")
                except Exception as e:
                    self.log(f"⚠️ 保存帧失败: {e}")

            now = time.time()
            if now - last_report >= 1.0:
                process_fps = self.process_count - last_process_count
                decode_fps_avg = self.decode_count / (now - self.decode_start) if self.decode_count > 0 else 0.0
                self.log(
                    f"📊 decode_total_avg={decode_fps_avg:.2f} fps, "
                    f"process_fps(this_sec)={process_fps}, queue={self.frame_q.qsize()}, saved={self.saved_count}"
                )
                last_report = now
                last_process_count = self.process_count

        self.log("🛑 process_worker 退出")

    def start(self):
        self.gst_thread = threading.Thread(target=self.gst_worker, daemon=True)
        self.gst_thread.start()

        start_wait = time.time()
        while not self.running.is_set():
            if time.time() - start_wait > 5.0:
                self.log("⚠️ GST pipeline 未能在 5s 内变为 PLAYING")
                break
            time.sleep(0.05)

        self.process_thread = threading.Thread(target=self.process_worker, daemon=True)
        self.process_thread.start()

    def stop(self):
        if self.stop_event.is_set():
            return
        self.log("⏹ 停止播放器...")
        self.stop_event.set()
        self.running.clear()
        try:
            if self.pipeline:
                self.pipeline.set_state(Gst.State.NULL)
        except Exception:
            pass

    def wait(self):
        if self.gst_thread:
            self.gst_thread.join(timeout=1.0)
        if self.process_thread:
            self.process_thread.join(timeout=1.0)


if __name__ == "__main__":
    urls = sys.argv[1:] if len(sys.argv) > 1 else [RTSP_URL_DEFAULT]
    players = []

    for idx, url in enumerate(urls, start=1):
        name = f"stream{idx}"
        print(f"使用流[{name}]: {url}")
        players.append(RKMPPOptimizedPlayer(url, name=name))

    for player in players:
        player.start()

    try:
        while True:
            time.sleep(0.5)
            if any(player.stop_event.is_set() for player in players):
                break
    except KeyboardInterrupt:
        print("\n🛑 捕获 Ctrl+C，准备退出")
    finally:
        for player in players:
            player.stop()
        for player in players:
            player.wait()

    print("程序退出。")
