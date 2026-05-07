import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QPushButton, QLineEdit,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QGroupBox, QMessageBox, QCheckBox, QDoubleSpinBox,
    QScrollArea, QRadioButton, QButtonGroup, QSpinBox,
    QStackedWidget,
)
from PyQt6.QtCore import Qt, pyqtSignal, QObject, QTimer
from PyQt6.QtGui import QFont

from tc_generator import TcGenerator, Timecode, FPS_VALUES
from osc_sender import OscSender
from osc_receiver import OscReceiver, is_port_available
from sync_clock import RelayClock
from ltc_encoder import encode_frame
from audio_output import LtcAudioOutput
import config


FRAME_RATE_LABELS = list(FPS_VALUES.keys())
_CH_LABELS = ["左 (L)", "右 (R)", "両方 (L+R)"]

MODE_SENDER = "sender"
MODE_RELAY = "relay"


class _Bridge(QObject):
    """スレッドをまたいで TC を GUI スレッドへ渡す。"""
    tick = pyqtSignal(str)

    def emit_tick(self, tc: Timecode) -> None:
        self.tick.emit(str(tc))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self._cfg = config.load()
        self._bridge = _Bridge()

        # モード別コンポーネント
        self._sender_osc = OscSender()
        self._receiver_osc = OscReceiver(on_tc=self._on_relay_tc)
        self._tc_gen = TcGenerator(on_tick=self._on_tick_sender)
        self._relay_clock = RelayClock(on_tick=self._on_tick_relay)
        self._audio = LtcAudioOutput()

        self._running: bool = False

        self._bridge.tick.connect(self._update_tc)

        self._build_ui()
        self._load_config()
        self._refresh_audio_devices()
        self._apply_mode(self._cfg.get("mode", MODE_SENDER), force=True)

        # 中継モード用ステータス更新タイマ
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(500)
        self._status_timer.timeout.connect(self._update_relay_status)
        self._status_timer.start()

    # ── UI 構築 ──────────────────────────────────────────────

    def _build_ui(self) -> None:
        self.setWindowTitle("LTC Station")
        self.setMinimumWidth(540)
        self.setMinimumHeight(600)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.setCentralWidget(scroll)

        root = QWidget()
        scroll.setWidget(root)
        layout = QVBoxLayout(root)
        layout.setSpacing(8)

        layout.addWidget(self._make_mode_group())
        layout.addWidget(self._make_tc_group())
        layout.addWidget(self._make_common_settings_group())

        self._stack = QStackedWidget()
        self._stack.addWidget(self._make_sender_page())   # index 0
        self._stack.addWidget(self._make_relay_page())    # index 1
        layout.addWidget(self._stack)

        layout.addWidget(self._make_audio_group())
        layout.addWidget(self._make_control_group())
        layout.addStretch()

    def _make_mode_group(self) -> QGroupBox:
        box = QGroupBox("モード")
        h = QHBoxLayout(box)

        self._mode_sender_rb = QRadioButton("送出モード  (TC生成 → OSC/LTC音声)")
        self._mode_relay_rb = QRadioButton("中継モード  (OSC受信 → LTC音声)")
        grp = QButtonGroup(self)
        grp.addButton(self._mode_sender_rb)
        grp.addButton(self._mode_relay_rb)
        self._mode_sender_rb.setChecked(True)

        self._mode_sender_rb.toggled.connect(
            lambda checked: checked and self._apply_mode(MODE_SENDER)
        )
        self._mode_relay_rb.toggled.connect(
            lambda checked: checked and self._apply_mode(MODE_RELAY)
        )

        h.addWidget(self._mode_sender_rb)
        h.addWidget(self._mode_relay_rb)
        h.addStretch()
        return box

    def _make_tc_group(self) -> QGroupBox:
        box = QGroupBox("タイムコード")
        v = QVBoxLayout(box)

        self._tc_label = QLabel("00:00:00:00")
        font = QFont("Courier New", 32, QFont.Weight.Bold)
        self._tc_label.setFont(font)
        self._tc_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self._tc_label)

        self._status_label = QLabel("停止")
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self._status_label)
        return box

    def _make_common_settings_group(self) -> QGroupBox:
        box = QGroupBox("設定 (共通)")
        v = QVBoxLayout(box)

        row0 = QHBoxLayout()
        row0.addWidget(QLabel("デバイス名:"))
        self._device_name_edit = QLineEdit()
        self._device_name_edit.setPlaceholderText("例: Stage_A / Zepp_Relay")
        self._device_name_edit.textChanged.connect(self._update_title)
        row0.addWidget(self._device_name_edit)
        v.addLayout(row0)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("フレームレート:"))
        self._fps_combo = QComboBox()
        self._fps_combo.addItems(FRAME_RATE_LABELS)
        self._fps_combo.setCurrentText("30NDF")
        row1.addWidget(self._fps_combo)
        row1.addStretch()
        v.addLayout(row1)
        return box

    # ── 送出モードページ ──────────────────────────────────────

    def _make_sender_page(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)

        # スタート TC
        tc_box = QGroupBox("送出設定")
        tv = QVBoxLayout(tc_box)
        row = QHBoxLayout()
        row.addWidget(QLabel("スタート TC:"))
        self._start_tc_edit = QLineEdit("00:00:00:00")
        self._start_tc_edit.setMaximumWidth(120)
        self._start_tc_edit.setPlaceholderText("HH:MM:SS:FF")
        row.addWidget(self._start_tc_edit)
        row.addStretch()
        tv.addLayout(row)
        v.addWidget(tc_box)

        # OSC 送信
        osc_box = QGroupBox("OSC 送信")
        ov = QVBoxLayout(osc_box)

        addr_row = QHBoxLayout()
        addr_row.addWidget(QLabel("OSC アドレス:"))
        self._osc_addr = QLineEdit("/Sync")
        addr_row.addWidget(self._osc_addr)
        ov.addLayout(addr_row)

        red_row = QHBoxLayout()
        red_row.addWidget(QLabel("冗長送信回数:"))
        self._redundancy_spin = QSpinBox()
        self._redundancy_spin.setRange(1, 5)
        self._redundancy_spin.setValue(2)
        self._redundancy_spin.setMaximumWidth(60)
        red_row.addWidget(self._redundancy_spin)
        red_row.addWidget(QLabel("(WAN パケロス対策)"))
        red_row.addStretch()
        ov.addLayout(red_row)

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["ホスト / IP アドレス", "ポート", ""])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(1, 80)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(2, 50)
        self._table.verticalHeader().setVisible(False)
        self._table.setMaximumHeight(160)
        ov.addWidget(self._table)

        add_btn = QPushButton("+ 追加")
        add_btn.clicked.connect(self._add_target_row)
        ov.addWidget(add_btn)

        v.addWidget(osc_box)
        v.addStretch()
        return page

    # ── 中継モードページ ──────────────────────────────────────

    def _make_relay_page(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)

        box = QGroupBox("OSC 受信")
        gv = QVBoxLayout(box)

        row = QHBoxLayout()
        row.addWidget(QLabel("受信ポート:"))
        self._listen_port_spin = QSpinBox()
        self._listen_port_spin.setRange(1, 65535)
        self._listen_port_spin.setValue(7000)
        self._listen_port_spin.setMaximumWidth(90)
        row.addWidget(self._listen_port_spin)
        row.addSpacing(16)
        row.addWidget(QLabel("OSC アドレス:"))
        self._relay_addr = QLineEdit("/Sync")
        row.addWidget(self._relay_addr)
        gv.addLayout(row)

        self._relay_status_label = QLabel("停止中")
        f = QFont()
        f.setPointSize(10)
        self._relay_status_label.setFont(f)
        gv.addWidget(self._relay_status_label)

        v.addWidget(box)

        info = QLabel(
            "※ 受信した OSC/TC をもとに内部クロックを同期し、\n"
            "　 このPCの音声出力デバイスから LTC 音声を再生します。"
        )
        info.setStyleSheet("color: #888;")
        v.addWidget(info)
        v.addStretch()
        return page

    # ── Audio 出力（両モード共通） ──────────────────────────────

    def _make_audio_group(self) -> QGroupBox:
        box = QGroupBox("LTC Audio 出力")
        v = QVBoxLayout(box)

        self._audio_enable_cb = QCheckBox("LTC 音声出力を有効にする")
        self._audio_enable_cb.toggled.connect(self._on_audio_enable_toggled)
        v.addWidget(self._audio_enable_cb)

        dev_row = QHBoxLayout()
        dev_row.addWidget(QLabel("出力デバイス:"))
        self._audio_device_combo = QComboBox()
        self._audio_device_combo.setMinimumWidth(200)
        dev_row.addWidget(self._audio_device_combo, 1)
        refresh_btn = QPushButton("更新")
        refresh_btn.setMaximumWidth(60)
        refresh_btn.clicked.connect(self._refresh_audio_devices)
        dev_row.addWidget(refresh_btn)
        v.addLayout(dev_row)

        level_row = QHBoxLayout()
        level_row.addWidget(QLabel("出力レベル:"))
        self._audio_level_spin = QDoubleSpinBox()
        self._audio_level_spin.setRange(0.0, 1.0)
        self._audio_level_spin.setSingleStep(0.05)
        self._audio_level_spin.setValue(0.9)
        self._audio_level_spin.setDecimals(2)
        self._audio_level_spin.setMaximumWidth(80)
        level_row.addWidget(self._audio_level_spin)
        level_row.addWidget(QLabel("(0.0 〜 1.0)"))
        level_row.addStretch()
        v.addLayout(level_row)

        ch_row = QHBoxLayout()
        ch_row.addWidget(QLabel("出力チャンネル:"))
        self._audio_ch_combo = QComboBox()
        self._audio_ch_combo.addItems(_CH_LABELS)
        ch_row.addWidget(self._audio_ch_combo)
        ch_row.addStretch()
        v.addLayout(ch_row)

        self._on_audio_enable_toggled(False)
        return box

    def _make_control_group(self) -> QGroupBox:
        box = QGroupBox()
        h = QHBoxLayout(box)

        self._play_btn = QPushButton("▶  開始")
        self._pause_btn = QPushButton("⏸  一時停止")
        self._stop_btn = QPushButton("■  停止")

        self._pause_btn.setEnabled(False)
        self._stop_btn.setEnabled(False)

        self._play_btn.clicked.connect(self._start)
        self._pause_btn.clicked.connect(self._pause)
        self._stop_btn.clicked.connect(self._stop)

        h.addWidget(self._play_btn)
        h.addWidget(self._pause_btn)
        h.addWidget(self._stop_btn)
        return box

    # ── モード切替 ────────────────────────────────────────────

    def _apply_mode(self, mode: str, force: bool = False) -> None:
        if self._running and not force:
            QMessageBox.warning(self, "モード切替", "停止してからモードを切替えてください。")
            # UI ラジオボタンを元に戻す
            current = self._current_mode()
            blocked = self._mode_sender_rb.blockSignals(True)
            self._mode_sender_rb.setChecked(current == MODE_SENDER)
            self._mode_sender_rb.blockSignals(blocked)
            blocked = self._mode_relay_rb.blockSignals(True)
            self._mode_relay_rb.setChecked(current == MODE_RELAY)
            self._mode_relay_rb.blockSignals(blocked)
            return

        if mode == MODE_SENDER:
            self._mode_sender_rb.setChecked(True)
            self._stack.setCurrentIndex(0)
            # 送出モードでは Audio 出力は任意
            self._audio_enable_cb.setEnabled(True)
        else:
            self._mode_relay_rb.setChecked(True)
            self._stack.setCurrentIndex(1)
            # 中継モードは音声出力がゴールなので強制ON
            self._audio_enable_cb.setChecked(True)
            self._audio_enable_cb.setEnabled(False)

        self._update_title()

    def _current_mode(self) -> str:
        return MODE_RELAY if self._mode_relay_rb.isChecked() else MODE_SENDER

    # ── Audio デバイス ────────────────────────────────────────

    def _refresh_audio_devices(self) -> None:
        self._audio_devices = LtcAudioOutput.list_output_devices()
        self._audio_device_combo.clear()
        self._audio_device_combo.addItem("システム既定", None)
        for dev in self._audio_devices:
            self._audio_device_combo.addItem(dev["name"], dev["index"])

    def _on_audio_enable_toggled(self, enabled: bool) -> None:
        for w in (
            self._audio_device_combo,
            self._audio_level_spin,
            self._audio_ch_combo,
        ):
            w.setEnabled(enabled)

    def _selected_audio_device_index(self):
        return self._audio_device_combo.currentData()

    # ── ターゲット行 ──────────────────────────────────────────

    def _add_target_row(self, ip: str = "", port: int = 7000) -> None:
        row = self._table.rowCount()
        self._table.insertRow(row)
        host_item = QTableWidgetItem(ip)
        host_item.setToolTip("例: 192.168.1.10  /  moov.synology.me")
        self._table.setItem(row, 0, host_item)
        self._table.setItem(row, 1, QTableWidgetItem(str(port)))
        del_btn = QPushButton("×")
        del_btn.clicked.connect(lambda: self._remove_row(del_btn))
        self._table.setCellWidget(row, 2, del_btn)

    def _remove_row(self, btn: QPushButton) -> None:
        for r in range(self._table.rowCount()):
            if self._table.cellWidget(r, 2) == btn:
                self._table.removeRow(r)
                break

    def _collect_targets(self) -> list[dict]:
        targets = []
        for r in range(self._table.rowCount()):
            ip_item = self._table.item(r, 0)
            port_item = self._table.item(r, 1)
            if ip_item and port_item:
                ip = ip_item.text().strip()
                try:
                    port = int(port_item.text().strip())
                except ValueError:
                    continue
                if ip:
                    targets.append({"ip": ip, "port": port})
        return targets

    # ── 再生制御 ─────────────────────────────────────────────

    def _start(self) -> None:
        mode = self._current_mode()
        if mode == MODE_SENDER:
            self._start_sender()
        else:
            self._start_relay()

    def _start_sender(self) -> None:
        audio_enabled = self._audio_enable_cb.isChecked()
        targets = self._collect_targets()

        if not targets and not audio_enabled:
            QMessageBox.warning(
                self,
                "エラー",
                "OSC 送信先を追加するか、LTC Audio 出力を有効にしてください。",
            )
            return

        fps_label = self._fps_combo.currentText()
        start_tc = Timecode.parse(self._start_tc_edit.text())
        if start_tc is None:
            QMessageBox.warning(self, "エラー", "スタート TC の形式が正しくありません。\n例: 01:00:00:00")
            return

        self._tc_gen.set_fps(fps_label)
        self._tc_gen.set_start(start_tc)

        if targets:
            self._sender_osc.set_address(self._osc_addr.text().strip() or "/Sync")
            self._sender_osc.set_targets(targets)
            self._sender_osc.set_redundancy(self._redundancy_spin.value())
        else:
            self._sender_osc.set_targets([])

        if audio_enabled:
            if not self._try_start_audio():
                return

        self._tc_gen.play()
        self._running = True
        self._play_btn.setEnabled(False)
        self._pause_btn.setEnabled(True)
        self._stop_btn.setEnabled(True)
        self._status_label.setText("送出中")

    def _start_relay(self) -> None:
        fps_label = self._fps_combo.currentText()
        port = self._listen_port_spin.value()

        # 1) 事前ポート可否確認（分かりやすいメッセージを出す）
        if not is_port_available(port):
            QMessageBox.critical(
                self,
                "ポート使用中",
                f"UDP ポート {port} は他のアプリに使われています。\n\n"
                f"対策:\n"
                f"・受信ポート番号を変更してください（例: 57000, 57001 など未使用ポート）\n"
                f"・同時に送出側アプリの送信ポートも合わせて変更してください\n"
                f"・別の LTC Station が同じポートで受信中の場合は停止してください",
            )
            return

        # 2) OSC 受信を先に起動（これが失敗したら音声を開かない）
        self._relay_clock.set_fps(fps_label)
        self._receiver_osc.configure(
            address=self._relay_addr.text().strip() or "/Sync",
            port=port,
        )
        try:
            self._receiver_osc.start()
        except Exception as e:
            QMessageBox.critical(
                self,
                "受信エラー",
                f"OSC 受信を開始できませんでした:\n{e}\n\n"
                f"ポート番号を変えて再試行してください。",
            )
            return

        # 3) 音声出力を起動
        if not self._try_start_audio():
            self._receiver_osc.stop()
            return

        # 4) 内部クロック起動
        self._relay_clock.start()
        self._running = True
        self._play_btn.setEnabled(False)
        self._pause_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._status_label.setText(f"待機中 (port {port} / 未同期)")

    def _try_start_audio(self) -> bool:
        self._audio.configure(
            device_index=self._selected_audio_device_index(),
            amplitude=self._audio_level_spin.value(),
            channel_mode=self._audio_ch_combo.currentIndex(),
        )
        try:
            self._audio.start()
            return True
        except Exception as e:
            QMessageBox.critical(self, "Audio エラー", f"音声デバイスを開けませんでした:\n{e}")
            return False

    def _pause(self) -> None:
        # 中継モードでは一時停止は無意味なので送出モードのみ
        if self._current_mode() == MODE_SENDER:
            self._tc_gen.pause()
            self._play_btn.setEnabled(True)
            self._pause_btn.setEnabled(False)
            self._status_label.setText("一時停止")

    def _stop(self) -> None:
        self._tc_gen.stop()
        self._relay_clock.stop()
        self._receiver_osc.stop()
        self._audio.stop()
        self._running = False
        self._play_btn.setEnabled(True)
        self._pause_btn.setEnabled(False)
        self._stop_btn.setEnabled(False)
        self._status_label.setText("停止")
        if self._current_mode() == MODE_SENDER:
            self._tc_label.setText(self._start_tc_edit.text() or "00:00:00:00")
        else:
            self._tc_label.setText("00:00:00:00")

    # ── Tick コールバック ─────────────────────────────────────

    def _on_tick_sender(self, tc: Timecode) -> None:
        if self._sender_osc.has_targets():
            self._sender_osc.send(tc)
        if self._audio.is_enabled:
            mono = encode_frame(tc, self._fps_combo.currentText())
            self._audio.push_frame(mono)
        self._bridge.emit_tick(tc)

    def _on_tick_relay(self, tc: Timecode) -> None:
        if self._audio.is_enabled:
            mono = encode_frame(tc, self._fps_combo.currentText())
            self._audio.push_frame(mono)
        self._bridge.emit_tick(tc)

    def _on_relay_tc(self, tc: Timecode) -> None:
        """OSC 受信スレッドから呼ばれる。"""
        self._relay_clock.on_remote_tc(tc)

    def _update_tc(self, tc_str: str) -> None:
        self._tc_label.setText(tc_str)

    def _update_title(self) -> None:
        name = self._device_name_edit.text().strip()
        mode_label = "送出" if self._current_mode() == MODE_SENDER else "中継"
        base = f"LTC Station [{mode_label}]"
        self.setWindowTitle(f"{base}  [{name}]" if name else base)

    # ── 中継ステータス更新 ───────────────────────────────────

    def _update_relay_status(self) -> None:
        if self._current_mode() != MODE_RELAY or not self._running:
            return
        clock_st = self._relay_clock.get_status()
        osc_st = self._receiver_osc.get_stats()

        audio_ok = "♪音声出力中" if self._audio.is_enabled else "♪音声停止"
        if clock_st["synced"]:
            self._status_label.setText(
                f"同期中 ({clock_st['sync_events']} events)  {audio_ok}"
            )
            status_text = (
                f"[同期] ドリフト: {clock_st['drift_frames']:+.2f} frame  /  "
                f"受信: {osc_st['packets']} (重複 {osc_st['dedup']})  /  "
                f"最終同期: {clock_st['seconds_since_last_sync']:.1f}s 前"
            )
        else:
            status_text = (
                f"[未同期] 受信パケット待機中...  受信: {osc_st['packets']}  /  {audio_ok}"
            )
        self._relay_status_label.setText(status_text)

    # ── 設定の読み書き ────────────────────────────────────────

    def _load_config(self) -> None:
        cfg = self._cfg
        self._device_name_edit.setText(cfg.get("device_name", ""))

        fps = cfg.get("frame_rate", "30NDF")
        idx = self._fps_combo.findText(fps)
        if idx >= 0:
            self._fps_combo.setCurrentIndex(idx)

        # 送出モード設定
        self._osc_addr.setText(cfg.get("osc_address", "/Sync"))
        self._start_tc_edit.setText(cfg.get("start_tc", "00:00:00:00"))
        self._redundancy_spin.setValue(cfg.get("osc_redundancy", 2))
        for t in cfg.get("targets", []):
            self._add_target_row(t.get("ip", ""), t.get("port", 7000))

        # 中継モード設定
        self._listen_port_spin.setValue(cfg.get("relay_listen_port", 7000))
        self._relay_addr.setText(cfg.get("osc_address", "/Sync"))

        # Audio
        self._audio_enable_cb.setChecked(cfg.get("audio_enabled", False))
        self._audio_level_spin.setValue(cfg.get("audio_amplitude", 0.9))
        self._audio_ch_combo.setCurrentIndex(cfg.get("audio_channel_mode", 0))

        saved_dev = cfg.get("audio_device_index")
        if saved_dev is not None:
            for i in range(self._audio_device_combo.count()):
                if self._audio_device_combo.itemData(i) == saved_dev:
                    self._audio_device_combo.setCurrentIndex(i)
                    break

        self._update_title()

    def _save_config(self) -> None:
        config.save({
            "mode": self._current_mode(),
            "device_name": self._device_name_edit.text().strip(),
            "frame_rate": self._fps_combo.currentText(),
            "start_tc": self._start_tc_edit.text(),
            "osc_address": self._osc_addr.text().strip(),
            "targets": self._collect_targets(),
            "osc_redundancy": self._redundancy_spin.value(),
            "relay_listen_port": self._listen_port_spin.value(),
            "audio_enabled": self._audio_enable_cb.isChecked(),
            "audio_device_index": self._selected_audio_device_index(),
            "audio_amplitude": self._audio_level_spin.value(),
            "audio_channel_mode": self._audio_ch_combo.currentIndex(),
        })

    def closeEvent(self, event) -> None:
        self._tc_gen.stop()
        self._relay_clock.stop()
        self._receiver_osc.stop()
        self._audio.stop()
        self._save_config()
        event.accept()
