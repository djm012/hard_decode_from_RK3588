# RK3588 + WVP + EasyGBD 步骤

## 1. RK3588 先启动服务

启动 ZLM：

```bash
cd ZLMediaKit/release/linux/Debug
./MediaServer
```

启动 WVP：

```bash
cd wvp-GB28181-pro/target
java -jar wvp-pro-2.7.4-09290743.jar
```

WVP 页面：

```text
http://172.10.50.34:18080
```

注意：

- WVP 实际配置看 `wvp-GB28181-pro/target/application.yml`
- 配置中改成板子上的ip
- ZLM 实际配置看 `ZLMediaKit/release/linux/Debug/config.ini`

## 2. Windows 先把视频变成 RTSP

双击运行：

```text
EasyRTSPServer_Demo.exe
```

把视频放到 exe 同目录。

单路示例：

- `EasyDarwin.mp4`
- RTSP：`rtsp://127.0.0.1:554/EasyDarwin.mp4`

双路示例：

- `EasyDarwin.mp4` -> `rtsp://127.0.0.1:554/EasyDarwin.mp4`
- `sec_branch.mp4` -> `rtsp://127.0.0.1:554/sec_branch.mp4`

先用 `ffplay` 或 VLC 验证 RTSP 能播。

## 3. 用 EasyGBD 模拟 GB28181 设备

单路时填：

- 服务器 IP：`172.10.50.34`
- 服务器端口：`5060`
- SIP 平台 ID：`34020000002000000001`
- 密码：`12345678`
- 本地端口：`15060`
- 设备 ID：`34020000001110000001`
- 通道 ID：`34020000001310000001`
- 网络串流：`rtsp://127.0.0.1:554/EasyDarwin.mp4`

注意：不要依赖“开启预览”找本地摄像头，你这个场景是走“网络串流”。

## 4. 双路时怎么做

复制两份 EasyGBD，分别启动：

- 实例 1：
  - 本地端口 `15060`
  - 设备 ID `34020000001110000001`
  - 通道 ID `34020000001310000001`
  - 网络串流 `rtsp://127.0.0.1:554/EasyDarwin.mp4`
- 实例 2：
  - 本地端口 `15061`
  - 设备 ID `34020000001110000002`
  - 通道 ID `34020000001310000002`
  - 网络串流 `rtsp://127.0.0.1:554/sec_branch.mp4`

关键：

- 两个实例都要一直运行
- 本地端口不能相同
- 设备 ID 不能相同
- 通道 ID 不能相同
- WVP 里两路都要分别点“播放”

## 5. 从 WVP 复制 RTSP 地址

双路时当前已经拿到：

```text
rtsp://172.10.50.34:554/rtp/34020000001110000001_34020000001310000001?originTypeStr=rtp_push
rtsp://172.10.50.34:554/rtp/34020000001110000002_34020000001310000002?originTypeStr=rtp_push
```

## 6. RK3588 上测试拉流和硬解码

先测单路：

```bash
ffplay "你的RTSP地址"
```

再测 GStreamer 硬解码：

```bash
gst-launch-1.0 rtspsrc location="RTSP地址" latency=100 ! rtph264depay ! h264parse ! mppvideodec ! fakesink sync=false
```

再测 Python 脚本：

```bash
python3 rkmpp_video_player_optimized.py "RTSP地址"
```

双路两种方式：

### 方式 1：两个进程

```bash
python3 rkmpp_video_player_optimized.py "rtsp://172.10.50.34:554/rtp/34020000001110000001_34020000001310000001?originTypeStr=rtp_push" > stream1.log 2>&1 &
python3 rkmpp_video_player_optimized.py "rtsp://172.10.50.34:554/rtp/34020000001110000002_34020000001310000002?originTypeStr=rtp_push" > stream2.log 2>&1 &
```

### 方式 2：一个进程传两个流

```bash
python3 rkmpp_video_player_optimized.py \
  "rtsp://172.10.50.34:554/rtp/34020000001110000001_34020000001310000001?originTypeStr=rtp_push" \
  "rtsp://172.10.50.34:554/rtp/34020000001110000002_34020000001310000002?originTypeStr=rtp_push"
```

## 7. 成功标准

- WVP 上设备在线
- WVP 上通道在线
- 点击播放后能生成 RTSP
- `ffplay` 能打开 RTSP
- RK3588 日志持续输出 `decode_total_avg` 和 `process_fps`
