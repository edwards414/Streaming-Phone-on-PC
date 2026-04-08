"""
ADB Phone Screen Streamer — FFmpeg H264 高效能版
功能：即時串流 + 滑鼠點擊 / 滑動 / 滾輪同步到手機
需求：adb, ffmpeg 需在 PATH 中
"""

import subprocess
import threading
import time
import sys
import numpy as np
import cv2


# ─── 設定 ─────────────────────────────────────────────────────────────────────
WINDOW_TITLE = "ADB Stream (H264)"
SCALE_FACTOR = 0.45          # 顯示縮放比 (0.15 ~ 1.5)
BITRATE      = "8M"          # 串流位元率 (越高越清晰但越吃頻寬)
HUD_TOP      = 36            # 頂部 HUD 高度 px
HUD_BOTTOM   = 26            # 底部 HUD 高度 px
ADB_CMD      = "adb"         # 若 adb 不在 PATH，改為完整路徑
FFMPEG_CMD   = "ffmpeg"      # 若 ffmpeg 不在 PATH，改為完整路徑

# 顏色 (BGR)
C_GREEN  = (0, 255, 120)
C_RED    = (0, 60, 255)
C_YELLOW = (0, 220, 255)
C_DARK   = (15, 15, 25)
C_CURSOR = (0, 230, 180)
FONT     = cv2.FONT_HERSHEY_SIMPLEX
# ──────────────────────────────────────────────────────────────────────────────


class H264Streamer:
    def __init__(self):
        self.frame      = None
        self.lock       = threading.Lock()
        self.running    = False
        self.fps        = 0.0
        self.device_id  = None
        self.phone_w    = 0
        self.phone_h    = 0
        self.scaled_w   = 0
        self.scaled_h   = 0
        self.frame_size = 0
        self.adb_proc   = None
        self.ff_proc    = None
        self.error_msg  = None

        # 滑鼠狀態
        self.mouse_x    = 0
        self.mouse_y    = 0
        self.drag_start = None
        self.is_dragging = False

    # ── ADB 工具 ──────────────────────────────────────────────────────────────
    def _adb(self, args: list[str]) -> list[str]:
        return [ADB_CMD, "-s", self.device_id] + args

    def list_devices(self) -> list[str]:
        r = subprocess.run([ADB_CMD, "devices"], capture_output=True, text=True)
        return [
            line.split("\t")[0].strip()
            for line in r.stdout.splitlines()[1:]
            if "\tdevice" in line
        ]

    def get_phone_resolution(self) -> tuple[int, int]:
        r = subprocess.run(
            self._adb(["shell", "wm", "size"]),
            capture_output=True, text=True
        )
        for line in r.stdout.splitlines():
            if "size:" in line.lower():
                s = line.split(":")[-1].strip()
                w, h = map(int, s.split("x"))
                return w, h
        return 1080, 1920

    # ── 串流程序管理 ──────────────────────────────────────────────────────────
    def _kill_procs(self):
        for proc in (self.ff_proc, self.adb_proc):
            if proc:
                try:
                    proc.kill()
                except Exception:
                    pass
        self.ff_proc = self.adb_proc = None

    def start_stream(self) -> bool:
        """啟動 ADB screenrecord | FFmpeg 解碼管道"""
        self._kill_procs()

        # 計算縮放後尺寸（必須為偶數）
        sw = int(self.phone_w * SCALE_FACTOR)
        sh = int(self.phone_h * SCALE_FACTOR)
        self.scaled_w = sw - (sw % 2)
        self.scaled_h = sh - (sh % 2)
        self.frame_size = self.scaled_w * self.scaled_h * 3  # BGR = 3 bytes/px

        adb_cmd = self._adb([
            "exec-out", "screenrecord",
            "--output-format=h264",
            f"--bit-rate={BITRATE}",
            "-",
        ])

        ff_cmd = [
            FFMPEG_CMD, "-loglevel", "quiet",
            "-i", "pipe:0",
            "-vf", f"scale={self.scaled_w}:{self.scaled_h}",
            "-f", "rawvideo",
            "-pix_fmt", "bgr24",
            "pipe:1",
        ]

        try:
            self.adb_proc = subprocess.Popen(
                adb_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
            )
            self.ff_proc = subprocess.Popen(
                ff_cmd,
                stdin=self.adb_proc.stdout,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            return True
        except FileNotFoundError as e:
            print(f"❌ 找不到執行檔：{e}")
            return False

    # ── 背景擷取執行緒 ────────────────────────────────────────────────────────
    def capture_loop(self):
        fps_timer = time.time()
        count = 0

        while self.running:
            # 若子程序已死，自動重啟
            if self.ff_proc is None or self.ff_proc.poll() is not None:
                print("🔄 串流中斷，自動重啟...")
                self.error_msg = "🔄 重新連接中..."
                self.start_stream()
                time.sleep(1.0)
                continue

            try:
                raw = self.ff_proc.stdout.read(self.frame_size)
            except Exception:
                time.sleep(0.2)
                continue

            if len(raw) != self.frame_size:
                self.error_msg = "⚠ 串流中斷，重新連接..."
                self.start_stream()
                time.sleep(1.0)
                continue

            frame = np.frombuffer(raw, dtype=np.uint8).reshape(
                (self.scaled_h, self.scaled_w, 3)
            )
            with self.lock:
                self.frame = frame.copy()

            count += 1
            self.error_msg = None

            elapsed = time.time() - fps_timer
            if elapsed >= 1.0:
                self.fps  = count / elapsed
                count     = 0
                fps_timer = time.time()

    # ── 座標轉換 ──────────────────────────────────────────────────────────────
    def screen_to_phone(self, sx: int, sy: int) -> tuple[int, int]:
        """將顯示視窗座標轉換為手機實際座標"""
        adj_y = sy - HUD_TOP
        px = int(sx / SCALE_FACTOR)
        py = int(adj_y / SCALE_FACTOR)
        px = max(0, min(px, self.phone_w - 1))
        py = max(0, min(py, self.phone_h - 1))
        return px, py

    def in_phone_area(self, sy: int) -> bool:
        """判斷座標是否在手機畫面區域（非 HUD）"""
        canvas_h = self.scaled_h + HUD_TOP + HUD_BOTTOM
        return HUD_TOP <= sy <= (canvas_h - HUD_BOTTOM)

    # ── ADB 輸入指令 ──────────────────────────────────────────────────────────
    def adb_tap(self, px: int, py: int):
        subprocess.Popen(
            self._adb(["shell", "input", "tap", str(px), str(py)]),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    def adb_swipe(self, x1, y1, x2, y2, duration_ms: int = 250):
        subprocess.Popen(
            self._adb(["shell", "input", "swipe",
                       str(x1), str(y1), str(x2), str(y2), str(duration_ms)]),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    def adb_key(self, keycode: str):
        subprocess.Popen(
            self._adb(["shell", "input", "keyevent", keycode]),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    # ── 滑鼠回調 ──────────────────────────────────────────────────────────────
    def mouse_callback(self, event, x, y, flags, param):
        self.mouse_x, self.mouse_y = x, y

        if event == cv2.EVENT_LBUTTONDOWN:
            if self.in_phone_area(y):
                self.drag_start  = (x, y)
                self.is_dragging = True

        elif event == cv2.EVENT_LBUTTONUP:
            if not self.is_dragging or self.drag_start is None:
                return
            self.is_dragging = False
            dx = x - self.drag_start[0]
            dy = y - self.drag_start[1]
            dist = (dx**2 + dy**2) ** 0.5

            px1, py1 = self.screen_to_phone(*self.drag_start)
            px2, py2 = self.screen_to_phone(x, y)

            if dist < 12:                         # 點擊
                self.adb_tap(px1, py1)
                print(f"👆 Tap  ({px1:4d}, {py1:4d})")
            else:                                 # 滑動
                self.adb_swipe(px1, py1, px2, py2)
                print(f"👆 Swipe ({px1},{py1}) → ({px2},{py2})")
            self.drag_start = None

        elif event == cv2.EVENT_MOUSEWHEEL:
            if not self.in_phone_area(y):
                return
            px, py = self.screen_to_phone(x, y)
            if flags > 0:                         # 向上滾
                self.adb_swipe(px, py + 300, px, py - 300, 120)
            else:                                 # 向下滾
                self.adb_swipe(px, py - 300, px, py + 300, 120)

    # ── HUD 繪製 ──────────────────────────────────────────────────────────────
    def _draw_hud(self, canvas: np.ndarray) -> np.ndarray:
        h, w = canvas.shape[:2]

        # ── 頂部欄
        cv2.rectangle(canvas, (0, 0), (w, HUD_TOP), C_DARK, -1)

        fps_color = C_GREEN if self.fps >= 12 else C_YELLOW if self.fps >= 6 else C_RED
        cv2.putText(canvas, f"FPS: {self.fps:.1f}",
                    (10, 24), FONT, 0.65, fps_color, 2, cv2.LINE_AA)

        res_txt = f"{self.phone_w}×{self.phone_h}"
        cv2.putText(canvas, res_txt,
                    (w // 2 - 42, 24), FONT, 0.52, (180, 180, 200), 1, cv2.LINE_AA)

        dev_txt = self.device_id[:24] if self.device_id else ""
        cv2.putText(canvas, dev_txt,
                    (w - 180, 22), FONT, 0.4, (120, 120, 150), 1, cv2.LINE_AA)

        # ── 底部欄
        cv2.rectangle(canvas, (0, h - HUD_BOTTOM), (w, h), C_DARK, -1)
        help_txt = "[Click]點擊  [Drag]滑動  [Scroll]滾動  [BACK]B  [HOME]H  [S]截圖  [Q]退出"
        cv2.putText(canvas, help_txt,
                    (8, h - 7), FONT, 0.36, (130, 130, 155), 1, cv2.LINE_AA)

        # ── 錯誤訊息
        if self.error_msg:
            cv2.putText(canvas, self.error_msg,
                        (10, h // 2), FONT, 0.7, C_RED, 2, cv2.LINE_AA)

        # ── 滑鼠游標指示
        if self.in_phone_area(self.mouse_y):
            cv2.circle(canvas, (self.mouse_x, self.mouse_y), 9, C_CURSOR, 1, cv2.LINE_AA)
            cv2.circle(canvas, (self.mouse_x, self.mouse_y), 2, C_CURSOR, -1, cv2.LINE_AA)

            # 拖曳軌跡線
            if self.is_dragging and self.drag_start:
                cv2.line(canvas, self.drag_start,
                         (self.mouse_x, self.mouse_y), C_CURSOR, 1, cv2.LINE_AA)

        return canvas

    # ── 主迴圈 ───────────────────────────────────────────────────────────────
    def run(self):
        global SCALE_FACTOR

        # ── 選擇裝置
        devices = self.list_devices()
        if not devices:
            print("\n❌ 找不到 ADB 裝置！")
            print("  1. 手機以 USB 連接電腦")
            print("  2. 開啟 開發者模式 → USB 偵錯")
            print("  3. 執行 `adb devices` 確認看到裝置")
            sys.exit(1)

        if len(devices) == 1:
            self.device_id = devices[0]
        else:
            print("偵測到多個裝置：")
            for i, d in enumerate(devices):
                print(f"  [{i}] {d}")
            self.device_id = devices[int(input("選擇編號："))]

        print(f"✅ 使用裝置：{self.device_id}")

        # ── 取得解析度
        self.phone_w, self.phone_h = self.get_phone_resolution()
        print(f"📐 手機解析度：{self.phone_w} × {self.phone_h}")

        # ── 確認 FFmpeg 存在
        try:
            subprocess.run([FFMPEG_CMD, "-version"],
                           capture_output=True, check=True)
        except (FileNotFoundError, subprocess.CalledProcessError):
            print("\n❌ 找不到 FFmpeg！")
            print("  請安裝 FFmpeg 並確認已加入 PATH")
            print("  Windows 安裝：https://ffmpeg.org/download.html")
            print("  或使用 winget: winget install Gyan.FFmpeg")
            sys.exit(1)

        # ── 啟動串流
        print(f"🚀 啟動 H264 串流（縮放 {SCALE_FACTOR:.0%}）...")
        if not self.start_stream():
            sys.exit(1)

        self.running = True
        t = threading.Thread(target=self.capture_loop, daemon=True)
        t.start()

        # ── 等待第一幀
        print("⏳ 等待第一幀...")
        for _ in range(50):
            with self.lock:
                if self.frame is not None:
                    break
            time.sleep(0.3)
        else:
            print("❌ 超時：無法取得畫面")
            print("  請確認 FFmpeg 版本支援 H264，或嘗試降低 BITRATE 設定")
            self.running = False
            self._kill_procs()
            sys.exit(1)

        print("🎉 串流已啟動！")

        # ── 建立視窗
        cv2.namedWindow(WINDOW_TITLE)
        cv2.setMouseCallback(WINDOW_TITLE, self.mouse_callback)
        screenshot_n = 0

        while self.running:
            with self.lock:
                frame = self.frame.copy() if self.frame is not None else None

            if frame is None:
                time.sleep(0.02)
                continue

            # 建立帶 HUD 的畫布
            h, w = frame.shape[:2]
            canvas = np.zeros((h + HUD_TOP + HUD_BOTTOM, w, 3), dtype=np.uint8)
            canvas[HUD_TOP : HUD_TOP + h, :] = frame
            display = self._draw_hud(canvas)

            cv2.imshow(WINDOW_TITLE, display)
            key = cv2.waitKey(1) & 0xFF

            if key in (ord("q"), ord("Q"), 27):          # Q / ESC → 退出
                break
            elif key in (ord("s"), ord("S")):             # S → 截圖
                fname = f"screenshot_{screenshot_n:04d}.png"
                cv2.imwrite(fname, frame)
                print(f"📸 截圖已存：{fname}")
                screenshot_n += 1
            elif key in (ord("+"), ord("=")):             # + → 放大
                SCALE_FACTOR = min(SCALE_FACTOR + 0.05, 1.5)
                print(f"🔍 縮放：{SCALE_FACTOR:.0%}，重啟串流...")
                self.start_stream()
            elif key == ord("-"):                         # - → 縮小
                SCALE_FACTOR = max(SCALE_FACTOR - 0.05, 0.15)
                print(f"🔍 縮放：{SCALE_FACTOR:.0%}，重啟串流...")
                self.start_stream()
            elif key in (ord("b"), ord("B")):             # B → 返回鍵
                self.adb_key("KEYCODE_BACK")
                print("🔙 Back")
            elif key in (ord("h"), ord("H")):             # H → Home
                self.adb_key("KEYCODE_HOME")
                print("🏠 Home")
            elif key == ord("r") or key == ord("R"):      # R → 重啟串流
                print("🔄 手動重啟串流...")
                self.start_stream()

        # ── 清理
        self.running = False
        self._kill_procs()
        cv2.destroyAllWindows()
        print("👋 串流已結束")


# ─── 入口點 ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    H264Streamer().run()
