# RK3588 + WVP + ZLMediaKit + EasyGBD 复现步骤

## 1. 目标

复现这条链路：

`本地视频 -> RTSP Simulator -> EasyGBD(模拟 GB28181 设备) -> WVP -> ZLMediaKit -> RK3588 拉流硬解码`

单路和双路都按这套方法做。

## 2. 先确认当前实际配置

### 2.1 WVP 的实际启动方式

你现在实际用的是：

```bash
cd wvp-GB28181-pro/target
java -jar wvp-pro-2.7.4-09290743.jar
```

所以 WVP 以这个文件为准：

- `wvp-GB28181-pro/target/application.yml`

不是以源码目录下的 `src/main/resources` 为准。

### 2.2 当前 `target/application.yml` 里读到的关键参数

- WVP Web 端口：`18080`
- SIP 端口：`5060`
- SIP 域：`3402000000`
- SIP 平台 ID：`34020000002000000001`
- SIP 密码：`12345678`
- ZLM HTTP 端口：`9092`
- ZLM secret：`TWSYFgYJOQWB4ftgeYut8DW4wbs7pQnj`
- RTP 端口范围：`40000-45000`
- 发送端口范围：`50000-55000`
- 当前文件里的 `media.ip`：`172.10.15.24`

注意：你后面实际跑通、并且 WVP 上复制出来的 RTSP 地址是 `172.10.50.34`。所以如果板子的当前 IP 已经是 `172.10.50.34`，那就要先把 `wvp-GB28181-pro/target/application.yml` 里的 `media.ip` 改成 `172.10.50.34`，然后再启动 WVP。

下面步骤统一按实际测试 IP `172.10.50.34` 写。

### 2.3 ZLMediaKit 的实际启动方式

你现在实际是这样启动的：

```bash
cd ZLMediaKit/release/linux/Debug
./MediaServer
```

所以你真正要核对的是：

- `ZLMediaKit/release/linux/Debug/config.ini`

不是源码目录里的 `ZLMediaKit/conf/config.ini`。

至少要保证和 WVP 对齐：

```ini
[api]
secret=TWSYFgYJOQWB4ftgeYut8DW4wbs7pQnj

[http]
port=9092

[rtsp]
port=554

[rtmp]
port=1935

[rtp_proxy]
port_range=40000-45000

[protocol]
enable_rtsp=1
```

## 3. RK3588 端准备

### 3.1 安装依赖

```bash
sudo apt update
sudo apt install -y mysql-server redis-server openjdk-8-jre
sudo apt install -y gstreamer1.0-tools gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly
sudo apt install -y gstreamer1.0-rockchip
```

### 3.2 启动 MySQL 和 Redis

```bash
sudo systemctl enable mysql
sudo systemctl enable redis-server
sudo systemctl start mysql
sudo systemctl start redis-server
```

### 3.3 初始化 WVP 数据库

不要用 `wvp_backup.sql`。

直接用 WVP 工程自带的初始化 SQL，比如：

- `wvp-GB28181-pro/数据库/2.7.4/初始化-mysql-2.7.4.sql`

执行示例：

```bash
mysql -uroot -p
CREATE DATABASE IF NOT EXISTS wvp DEFAULT CHARACTER SET utf8mb4;
use wvp;
source /你的路径/wvp-GB28181-pro/数据库/2.7.4/初始化-mysql-2.7.4.sql;
```

## 4. 启动服务端

### 4.1 启动 ZLMediaKit

```bash
cd ZLMediaKit/release/linux/Debug
./MediaServer
```

如果启动时看到 `on_server_keepalive connection refused`，通常表示 WVP 还没起来。先不用管，等 WVP 启动后就正常了。

### 4.2 启动 WVP

```bash
cd wvp-GB28181-pro/target
java -jar wvp-pro-2.7.4-09290743.jar
```

浏览器访问：

```text
http://172.10.50.34:18080
```

## 5. Windows 端要准备什么

### 5.1 软件

Windows 端准备这几个：

- `EasyRTSPServer_Demo.exe`：把本地视频临时变成 RTSP
- `EasyGBD_Demo.exe`：模拟 GB28181 设备
- `VLC` 或 `ffplay`：验证 RTSP 是否正常

### 5.2 你当前用的视频文件

- `EasyDarwin.mp4`
- `sec_branch.mp4`

## 6. 单路复现步骤

### 6.1 先把本地视频变成 RTSP

在 Windows 上双击运行：

```text
EasyRTSPServer_Demo.exe
```

把 `EasyDarwin.mp4` 放到 exe 同目录。

然后先用播放器验证：

```text
rtsp://127.0.0.1:554/EasyDarwin.mp4
```

例如：

```bash
ffplay rtsp://127.0.0.1:554/EasyDarwin.mp4
```

### 6.2 配置 EasyGBD 单路参数

在 EasyGBD 里不要依赖“开启预览”找本地摄像头。

你现在这种场景，应该用“网络串流/网络流地址”方式，让 EasyGBD 去拉 RTSP。

单路推荐这样填：

- 服务器 IP：`172.10.50.34`
- 服务器端口：`5060`
- SIP 平台 ID：`34020000002000000001`
- 密码：`12345678`
- 本地端口：`15060`
- 设备 ID：`34020000001110000001`
- 通道 ID：`34020000001310000001`
- 网络串流：`rtsp://127.0.0.1:554/EasyDarwin.mp4`
- 注册协议：`UDP`

### 6.3 在 WVP 上验证设备上线并点播

打开：

```text
http://172.10.50.34:18080
```

确认：

- 设备在线
- 通道在线
- 点击播放后能正常生成流

### 6.4 获取 ZLM 输出 RTSP

从 WVP 页面复制 RTSP 地址。

你之前已经实际拿到过这种格式：

```text
rtsp://172.10.50.34:554/rtp/34020000001110000009_34020000001310000001?originTypeStr=rtp_push
```

实际以 WVP 页面复制出来的为准。

### 6.5 在 RK3588 上验证拉流和硬解码

先验证 GStreamer 插件：

```bash
gst-inspect-1.0 | grep -E "mppvideodec|rkmppvideodec"
```

单路 H.264 验证：

```bash
gst-launch-1.0 rtspsrc location="你的RTSP地址" latency=100 ! rtph264depay ! h264parse ! mppvideodec ! fakesink sync=false
```

如果你的环境插件名是 `rkmppvideodec`，就把 `mppvideodec` 换成 `rkmppvideodec`。

你也可以直接跑脚本：

```bash
python3 rkmpp_video_player_optimized.py "你的RTSP地址"
```

## 7. 双路复现步骤

### 7.1 先在 Windows 上同时准备两路 RTSP

还是运行：

```text
EasyRTSPServer_Demo.exe
```

把两个文件都放到 exe 同目录：

- `EasyDarwin.mp4`
- `sec_branch.mp4`

两路 RTSP 地址分别是：

```text
rtsp://127.0.0.1:554/EasyDarwin.mp4
rtsp://127.0.0.1:554/sec_branch.mp4
```

先分别验证：

```bash
ffplay rtsp://127.0.0.1:554/EasyDarwin.mp4
ffplay rtsp://127.0.0.1:554/sec_branch.mp4
```

### 7.2 启动两个 EasyGBD 实例

单设备版 EasyGBD 一次只适合对应一个设备，所以双路时最稳妥的方法是复制两份程序目录，分别启动。

例如：

- `EasyGBD_1`
- `EasyGBD_2`

### 7.3 EasyGBD 实例 1 参数

- 服务器 IP：`172.10.50.34`
- 服务器端口：`5060`
- SIP 平台 ID：`34020000002000000001`
- 密码：`12345678`
- 本地端口：`15060`
- 设备 ID：`34020000001110000001`
- 通道 ID：`34020000001310000001`
- 网络串流：`rtsp://127.0.0.1:554/EasyDarwin.mp4`

### 7.4 EasyGBD 实例 2 参数

- 服务器 IP：`172.10.50.34`
- 服务器端口：`5060`
- SIP 平台 ID：`34020000002000000001`
- 密码：`12345678`
- 本地端口：`15061`
- 设备 ID：`34020000001110000002`
- 通道 ID：`34020000001310000002`
- 网络串流：`rtsp://127.0.0.1:554/sec_branch.mp4`

### 7.5 双路在 WVP 上保持可用的关键点

如果你发现 WVP 上经常一条流能用、另一条流失效，优先检查这 4 件事：

1. 两个 EasyGBD 是否一直都在运行
2. 两个 EasyGBD 的本地端口/设备 ID/通道 ID 是否完全不同
3. WVP 页面里两路通道是否都在线
4. 两路通道是否都分别点击了“播放”

很多时候不是“注册掉了”，而是第二路没有维持住点播状态，所以 ZLM 侧没有持续保留对应 RTSP。

### 7.6 双路 RTSP 输出

你现在已经实际拿到过这两路：

```text
rtsp://172.10.50.34:554/rtp/34020000001110000001_34020000001310000001?originTypeStr=rtp_push
rtsp://172.10.50.34:554/rtp/34020000001110000002_34020000001310000002?originTypeStr=rtp_push
```

### 7.7 双路拉流与硬解码测试

先分别用 `ffplay` 验证两路都能拉到：

```bash
ffplay "rtsp://172.10.50.34:554/rtp/34020000001110000001_34020000001310000001?originTypeStr=rtp_push"
ffplay "rtsp://172.10.50.34:554/rtp/34020000001110000002_34020000001310000002?originTypeStr=rtp_push"
```

再在 RK3588 上做双路硬解码验证。

#### 方式 1：两个独立进程，各跑一路

```bash
python3 rkmpp_video_player_optimized.py "rtsp://172.10.50.34:554/rtp/34020000001110000001_34020000001310000001?originTypeStr=rtp_push" > stream1.log 2>&1 &
python3 rkmpp_video_player_optimized.py "rtsp://172.10.50.34:554/rtp/34020000001110000002_34020000001310000002?originTypeStr=rtp_push" > stream2.log 2>&1 &
```

查看日志：

```bash
tail -f stream1.log
tail -f stream2.log
```

#### 方式 2：一次传两个 RTSP 地址

如果你板子上的脚本已经是支持多参数的版本，可以这样：

```bash
python3 rkmpp_video_player_optimized.py \
  "rtsp://172.10.50.34:554/rtp/34020000001110000001_34020000001310000001?originTypeStr=rtp_push" \
  "rtsp://172.10.50.34:554/rtp/34020000001110000002_34020000001310000002?originTypeStr=rtp_push"
```

### 7.8 双路是否成功，看什么

看这几个结果：

- WVP 上两路设备/通道都在线
- 两路都能点播
- 两路都能从 WVP 复制出 RTSP
- 两路 `ffplay` 都能打开
- RK3588 上两路日志都持续刷新 `decode_total_avg` / `process_fps`

## 8. 常见问题

### 8.1 `ffplay` 报 404 Stream Not Found

说明这一路当前没有被 WVP 成功点播出来，或者 ZLM 上已经没有这一路活跃流。

先回到 WVP 页面重新点这一路播放，再复制最新 RTSP 地址测试。

### 8.2 ZLM 启动时 hook keepalive connection refused

这是因为 WVP 还没起来，或者 WVP 的 hook 服务端口当时还没监听。通常先启动 ZLM、后启动 WVP，就会在 WVP 启动后恢复正常。

### 8.3 OpenCV `cvNamedWindow` 报错

那不是硬解码失败，是当前 OpenCV 没有 GUI 支持。你现在更适合用无窗口方式，只看拉流、解码、抽帧和 FPS 日志。

### 8.4 什么叫“多路”

这里的多路，就是两路或多路独立视频同时处理。不是一个视频重复显示，而是两个视频源分别走完整链路，再在 RK3588 同时拉流和硬解码。
