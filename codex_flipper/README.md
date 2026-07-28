# Codex Flipper Monitor

一个由两部分组成的原型：

- `raspberry_pi/`：在树莓派上启动 `codex app-server`，读取实时线程状态、额度和审批请求，并通过 BLE SerialSvc 发给 Flipper。
- `flipper/`：独立 FAP，显示状态与额度；待审批时闪 LED，短按 OK 仅批准当前一次，长按 OK 拒绝。

## 重要边界

Flipper Zero 官方固件的 BLE SerialSvc 同时被 RPC 使用。此 FAP 在运行期间临时接管 SerialSvc callback，因此应先退出 qFlipper/手机 App；退出 FAP 后 callback 会释放。不同固件 SDK 的蓝牙内部 API 可能变化，因此 FAP 必须用与你设备固件匹配的 uFBT 构建。

审批是安全操作：短按 OK 只发送 `accept`，不会选择“本会话始终允许”或写入永久规则。屏幕会显示命令/原因的截断摘要，确认前请阅读 Codex 主界面中的完整内容。

## 树莓派安装

要求：Python 3.11+、BlueZ、Codex CLI 0.144 或兼容版本。

```bash
cd codex_flipper
python3 -m venv .venv
. .venv/bin/activate
pip install -r raspberry_pi/requirements.txt
python -m raspberry_pi.main --scan
```

先在 Flipper 上打开 FAP，再运行：

```bash
python -m raspberry_pi.main --device "Flipper Name"
```

调试时无需 Flipper：

```bash
python -m raspberry_pi.main --stdio
```

`--stdio` 会逐行打印发往 Flipper 的小型 JSON，并从标准输入接受 `{"op":"approve"}` 或 `{"op":"decline"}`。

## 已生成的成品

- 桌面启动文件：`release/Codex-Flipper-Monitor.desktop`
- 桌面启动脚本：`release/启动桌面软件.sh`
- Flipper 应用：`release/flipper_sd/apps/Tools/Codex_Monitor.fap`

`.fap` 使用官方固件 1.4.3 SDK 构建，Target 7，API 87.1。把
`release/flipper_sd` 目录里的内容按原目录结构复制到 SD 卡根目录，
然后在 Flipper 的 `Apps -> Tools -> Codex Monitor` 中启动。

如果旧版出现 `failed to discover services, device disconnected`，请覆盖为
最新的 `Codex_Monitor.fap`。新版使用应用专属、非绑定 BLE profile，不再依赖
Linux 桌面的 PIN 配对代理。它只在该 FAP 打开期间广播，并且 BLE 同时只允许
一个树莓派连接；不要在公共场所保持审批桥接应用长时间开启。

## 构建 FAP

安装 [uFBT](https://developer.flipper.net/flipperzero/doxygen/applications.html)，然后：

```bash
cd codex_flipper/flipper
ufbt
ufbt launch
```

生成文件位于 `dist/`。如果 SDK 报蓝牙符号不在外部 API 表中，说明你的官方固件版本不允许 FAP 接管 SerialSvc；这时需要把该 app 放进同版本 firmware 的 `applications_user/` 做固件内构建。

## 协议

每条消息是一行 UTF-8 JSON（最大 240 字节）：

```json
{"op":"state","status":"working","primary":82,"secondary":54,"summary":"turn running"}
{"op":"approval","kind":"command","summary":"sudo apt update"}
{"op":"approve"}
```

百分比表示**剩余额度**。树莓派端直接使用 Codex app-server 的
`thread/status/changed`、`account/rateLimits/updated` 和
`item/*/requestApproval`，没有读取或解析认证文件。
