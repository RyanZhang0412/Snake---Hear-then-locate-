# ESP32 采集固件

ESP32 用两个 I²S 接收器采集两块 XVF3800 的原始麦克风信号。上板提供两块板共享的 BCLK 和 LRCK。Windows 界面通过串口配置音频通道，并从 ESP32 的 UDP 54322 端口接收数据。

| 信号 | ESP32 | 上板 A | 下板 B |
| --- | --- | --- | --- |
| BCLK | GPIO26 | 输出 | 输入 |
| LRCK | GPIO25 | 输出 | 输入 |
| DATA1 | GPIO34 | 输出 | |
| DATA1 | GPIO35 | | 输出 |
| SDA / SCL | GPIO21 / GPIO22 | 控制 | |
| SDA / SCL | GPIO32 / GPIO33 | | 控制 |
| GND | GND | GND | GND |

配套 XVF3800 固件是上板的 Seeed I²S master 1.0.9（`inthost-lr48-sqr-i2c`）和下板的 XMOS I²S slave 3.2.1（`intdev-lr48-sqr-i2c`）。本仓库提供 ESP32 采集源码。XVF3800 厂商镜像可查阅 [Seeed I²S 固件](https://github.com/respeaker/reSpeaker_XVF3800_USB_4MIC_ARRAY/tree/master/xmos_firmwares/i2s) 和 [XMOS XVF3800](https://www.xmos.com/xvf3800/)。

在 ESP-IDF 命令行中切换到本目录后构建：

```powershell
idf.py set-target esp32
idf.py build
```

发布版 ESP32 源码采用 Wi‑Fi station 模式，串口连接后由 Windows 启动脚本提供 Wi‑Fi 配置。测试现有设备时可继续使用已刷好的采集固件。串口使用 115200 波特率。
