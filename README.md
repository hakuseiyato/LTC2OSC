# LTC2OSC

LTC（Linear Time Code）を OSC に変換し、TouchDesigner / Unity / 演出機器へ送出する Python ツール。

> **出自**: 2605_ISJ_ZeppHaneda 案件（`prj_ISJ_2605/Assets/sandbox/ltc2osc/`）から 2026-05-08 に独立 git 化。
> Phase 2.5 以降のアップデート方針はまだ未確定。先にリポジトリとしてバージョン管理を開始する。

## 構成

| ファイル | 役割 |
|---|---|
| `main.py` | エントリポイント（GUI 起動） |
| `gui/` | GUI 一式（PyQt 系） |
| `ltc_encoder.py` | LTC バイナリ生成 |
| `tc_generator.py` | タイムコード生成（fps 計算など） |
| `audio_output.py` | LTC を音声として送出 |
| `osc_sender.py` / `osc_receiver.py` | OSC 送受信 |
| `sync_clock.py` | クロック同期 |
| `config.py` | 設定 |
| `requirements.txt` | 依存パッケージ |
| `LTC2OSC.spec` | PyInstaller 用 spec |

## 関連

- 案件側 LTC 統合ドキュメント: `01_Projects/Composition/2605_ISJ_ZeppHaneda/TD_System/10_LTC_Integration.md`（Obsidian）
- 集約 INDEX: `C:\Work\Yato\Claude\yato-atlas\prj_ISJ_2605\ltc_integration.md`
- LTC 送出側アプリ: [LTC Station](../yato-atlas/LTCStation/)（別案件・WAN 越し送出）

## ステータス

- v0.1.0（2026-05-08 git 初期化）: 案件運用版そのまま。Phase 0/2 完了済み、Phase 2.5 以降未着手。
