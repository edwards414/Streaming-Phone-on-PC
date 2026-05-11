# ADB Phone Screen Streamer

透過 USB + ADB + FFmpeg 將 Android 手機畫面即時串流到電腦，並支援滑鼠點擊、滑動、滾輪同步回手機。

---

## 功能特色

| 功能 | 說明 |
|------|------|
| 即時串流 | H264 硬體加速，一般可達 **15–30 FPS** |
| 點擊同步 | 在電腦上點擊 → 同步發送 `input tap` 到手機 |
| 滑動同步 | 滑鼠拖曳 → 同步發送 `input swipe` 到手機 |
| 滾輪同步 | 滾輪上下 → 換算為手機滾動手勢 |
| 自動重連 | 串流中斷（如 3 分鐘截止）自動重啟管道 |
| 低延遲優化 | 手機端先縮放輸出，接收端只保留最新畫面 |
| 縮放調整 | 即時調整顯示比例並重啟串流 |
| 截圖存檔 | 一鍵截圖存為 PNG |
| Android 按鍵 | 鍵盤 B / H 對應手機返回 / 主畫面鍵 |

---

## 系統需求

- **OS**：Windows / macOS / Linux
- **Python**：3.10+
- **ADB**：已安裝並加入 PATH（Android SDK Platform Tools）
- **FFmpeg**：已安裝並加入 PATH
- **Android**：已開啟**開發者模式** + **USB 偵錯**，並透過 USB 連接

---

## 安裝步驟

### 1. 安裝 ADB

```powershell
# Windows（使用 winget）
winget install Google.PlatformTools
```

或至官網下載：https://developer.android.com/tools/releases/platform-tools

### 2. 安裝 FFmpeg

```powershell
# Windows
winget install Gyan.FFmpeg
```

或至官網下載：https://ffmpeg.org/download.html

> 安裝後請重新開啟終端以使 PATH 生效，並用 `ffmpeg -version` 確認。

### 3. 安裝 Python 依賴

```powershell
# 建議使用虛擬環境
python -m venv venv
.\venv\Scripts\activate

pip install -r requirements.txt
```

`requirements.txt`：
```
opencv-python>=4.9.0
numpy>=1.26.0
```

### 4. 手機設定

1. 進入 **設定 → 關於手機**，連點「版本號碼」7 次啟用開發者模式
2. 進入 **設定 → 開發人員選項**，開啟「**USB 偵錯**」
3. 用 USB 連接電腦，手機上彈出授權視窗選擇「允許」

確認裝置已就緒：

```powershell
adb devices
# 應看到：<裝置ID>   device
```

---

## 執行

```powershell
python stream.py
```

---

## 操作快捷鍵

| 操作 | 功能 |
|------|------|
| **左鍵點擊** | 同步點擊手機對應位置 |
| **左鍵拖曳** | 同步滑動（位移 > 12px 判定為 swipe） |
| **滾輪** | 同步滾動（換算為 300px swipe） |
| `S` | 截圖存為 `screenshot_XXXX.png` |
| `+` / `=` | 放大顯示（每次 +5%，最大 150%） |
| `-` | 縮小顯示（每次 -5%，最小 15%） |
| `B` | 手機返回鍵（`KEYCODE_BACK`） |
| `H` | 手機主畫面鍵（`KEYCODE_HOME`） |
| `R` | 手動重啟串流管道 |
| `Q` / `ESC` | 退出程式 |

---

## 技術架構

### 串流管道

```
Android 手機
    └─ screenrecord --output-format=h264 -   ← 產生原始 H264 位元流
         │  (透過 ADB exec-out 傳輸)
         ▼
   adb exec-out (USB)
         │
         ▼
      FFmpeg
    └─ 接收 pipe:0 → 低延遲解碼 H264 → 輸出 raw BGR24 到 pipe:1
         │
         ▼
   Python (numpy)
    └─ 讀取固定大小位元組 (W × H × 3)
    └─ reshape 成 ndarray
         │
         ▼
   OpenCV imshow
    └─ 顯示視窗 + HUD 疊加
```

### 座標轉換

點擊座標需從「視窗像素」換算回「手機實際像素」：

```python
# 視窗座標 (sx, sy)
adj_y = sy - HUD_TOP          # 去除頂部 HUD 偏移
phone_x = int(sx / SCALE_FACTOR)
phone_y = int(adj_y / SCALE_FACTOR)
```

### 點擊 vs 滑動判斷

滑鼠按下到放開的位移距離作為判斷依據：

```
dist < 12px  →  adb shell input tap x y
dist ≥ 12px  →  adb shell input swipe x1 y1 x2 y2 [duration_ms]
```

### 自動重連機制

`screenrecord` 預設最長錄製 **3 分鐘**後會自動停止。  
`capture_loop` 每次讀取前都會檢查 FFmpeg 子程序是否仍在運行：

```python
if self.ff_proc is None or self.ff_proc.poll() is not None:
    self.start_stream()   # 自動重啟整條管道
```

### 執行緒設計

| 執行緒 | 職責 |
|--------|------|
| 主執行緒 | OpenCV 視窗渲染、鍵盤/滑鼠事件處理 |
| `capture_loop`（daemon） | 從 FFmpeg stdout 持續讀取 raw frame，若累積多幀則只保留最新幀 |

兩者透過 `threading.Lock` 保護共享的 `self.frame`，避免 race condition。

---

## 設定參數

位於 `stream.py` 頂部，可依需求調整：

```python
SCALE_FACTOR = 0.45   # 顯示縮放比 (0.15 ~ 1.5)
BITRATE      = "6M"   # H264 位元率（越高越清晰，越吃 USB 頻寬）
HUD_TOP      = 36     # 頂部 HUD 高度（px）
HUD_BOTTOM   = 26     # 底部 HUD 高度（px）
ADB_CMD      = "adb"  # ADB 執行檔路徑
FFMPEG_CMD   = "ffmpeg"  # FFmpeg 執行檔路徑
```

| 參數 | 建議值 | 說明 |
|------|--------|------|
| `SCALE_FACTOR` | `0.4` ~ `0.6` | 越小越流暢，越大越清晰 |
| `BITRATE` | `4M` ~ `12M` | USB 2.0 建議從 `6M` 開始；USB 3.0 可逐步拉高 |

---

## 常見問題

### `❌ 找不到 ADB 裝置`

- 確認 USB 已連接，手機已解鎖
- 手機彈出「允許 USB 偵錯」→ 點選「允許」
- 執行 `adb kill-server && adb start-server` 重置

### `❌ 找不到 FFmpeg`

- 安裝後需**重新開啟終端**
- 確認 `ffmpeg -version` 可正常執行

### FPS 很低（< 5）

- 嘗試降低 `BITRATE`（如 `"4M"`）
- 嘗試降低 `SCALE_FACTOR`（如 `0.3`）
- 確認使用 USB 3.0 連接埠

### 串流延遲高

ADB USB 串流本身有約 **100–300ms** 的固定延遲，這是 `screenrecord` 的編碼緩衝所致，無法完全消除。

---

## 依賴版本

| 套件 | 最低版本 |
|------|----------|
| `opencv-python` | 4.9.0 |
| `numpy` | 1.26.0 |
| FFmpeg | 5.0+ |
| ADB | 34.0+ |
| Python | 3.10+ |
