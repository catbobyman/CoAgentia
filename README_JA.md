<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="site/assets/wordmark-dark.svg">
  <img src="site/assets/wordmark-light.svg" alt="CoAgentia" width="460">
</picture>

<h3>AI エージェントを同僚として扱う、ローカルファーストのマルチエージェント協働プラットフォーム</h3>

<p>ひと言のリクエストから公開 URL まで、すべてが同じ IM の中で完結します。</p>

<p>
  <img alt="pytest" src="https://img.shields.io/badge/pytest-1122_passed-46D98A?style=flat-square&labelColor=1A1D18">
  <img alt="vitest" src="https://img.shields.io/badge/vitest-512_passed-46D98A?style=flat-square&labelColor=1A1D18">
  <img alt="pyright" src="https://img.shields.io/badge/pyright-0_errors-46D98A?style=flat-square&labelColor=1A1D18">
  <img alt="milestones" src="https://img.shields.io/badge/MVP-M1–M8_shipped-E8763A?style=flat-square&labelColor=1A1D18">
</p>
<p>
  <img alt="python" src="https://img.shields.io/badge/Python-3.12+-E8763A?style=flat-square&labelColor=1A1D18&logo=python&logoColor=E9E7DC">
  <img alt="fastapi" src="https://img.shields.io/badge/FastAPI-server-E8763A?style=flat-square&labelColor=1A1D18&logo=fastapi&logoColor=E9E7DC">
  <img alt="react" src="https://img.shields.io/badge/React-web-E8763A?style=flat-square&labelColor=1A1D18&logo=react&logoColor=E9E7DC">
  <img alt="sqlite" src="https://img.shields.io/badge/SQLite-≥3.35-E8763A?style=flat-square&labelColor=1A1D18&logo=sqlite&logoColor=E9E7DC">
  <img alt="platform" src="https://img.shields.io/badge/Windows-単機MVP-A5A69A?style=flat-square&labelColor=1A1D18">
</p>

<p>
  <a href="#コア機能"><b>コア機能</b></a> · <a href="#アーキテクチャ"><b>アーキテクチャ</b></a> · <a href="#クイックスタート"><b>クイックスタート</b></a> · <a href="site/index.html"><b>紹介ページ</b></a>
</p>

<p>
  <a href="README_EN.md">English</a> | <a href="README.md">中文</a> | <b>日本語</b>
</p>

<code>リクエスト → 分解 → 確認 → 並列デリバリー → Diff/プレビュー検収 → マージ → ワンクリックデプロイ → コスト精算</code>

</div>

---

**CoAgentia** は、**コントラクト駆動・オーケストレーション可能・ガードレール可視**なマルチエージェント協働ワークベンチです。人間と AI エージェントがチャンネルの中で同僚のように会話し、Orchestrator がリクエストをタスクグラフに分解、複数のエージェントが並列でコードを納品します。Diff とライブプレビューで検収し、ワンクリックでマージ、もうワンクリックでデプロイ——最後に URL とこの仕事の token 請求書が届きます。

## なぜ IM なのか

エージェントオーケストレーションツールの多くは「ワークフローキャンバス＋ログコンソール」の姿をしています。CoAgentia の賭けはこうです：**協働の自然なかたちは会話である**。チャンネル、スレッド、@メンション、タスクボード——人間のチームが協働するやり方そのままに、人間とエージェントの混成チームも協働するべきです。キャンバスもガードレールも台帳も会話のそばに置き、会話をコンソールに押し込めることはしません。

## コア機能

| ドメイン | できること |
| --- | --- |
| **IM 基盤** | チャンネル / DM / スレッド / @メンション / ファイル / 既読。WS イベント駆動でリロード不要 |
| **タスク** | メッセージのワンクリックタスク化、クレーム / アサイン / 状態機械、ボード、全文検索、Activity ストリーム |
| **オーケストレーションキャンバス** | React Flow 依存グラフ。循環防止書き込みトランザクション、`blocked` リアルタイム導出＋配送層 gating、force-start 介入 |
| **Orchestrator 分解チェーン** | @Orchestrator で分解：提案 → 構造検証（14 ルール）→ ドラフト層での確認（項目ごとに調整可）→ アトミックな着地。実行中でも delta 増分修正が可能 |
| **デリバリーチェーン** | `writes_code` タスクは git worktree を自動派生。Diff ビューア、**常駐 dev server によるライブプレビュー**（iframe 並置検収）、DAG 順 `merge --no-ff`、コンフリクトは自動でタスク化して差し戻し |
| **ワンクリックデプロイ** | 人間のクリックとエージェントの `trigger_deploy` の二経路。デプロイログのリアルタイムストリーム、409 による直列化保護、URL 付き結果カード |
| **ガードレール** | 沈黙リマインダーのエスカレーション、freshness ゲート＋保留ドラフト三択、サマリーラウンド護欄（空転防止）、品質シグナルのフィードバックループ |
| **コスト精算** | `GET /usage` でワークスペース / エージェント / タスクの三層帰属。デプロイごとの token 小計。カバレッジは正直に表示し、通貨換算を偽装しない |
| **デュアル runtime** | Claude Code と Codex CLI のアダプター。チャンネル別通知ポリシー、cron、スキル許可リスト、ロールテンプレートと三ステップのチーム作成ウィザード |

## アーキテクチャ

```
┌──────────────┐    REST + WebSocket    ┌───────────────────┐
│   apps/web   │ <====================> │    apps/server    │
│ React + Vite │                        │ FastAPI + SQLite  │
└──────────────┘                        └─────────┬─────────┘
                                                  │  WS frames (Contract D)
                                        ┌─────────┴─────────┐
                                        │    apps/daemon    │
                                        │   executor only   │
                                        └─────────┬─────────┘
                                                  │  stdio / JSON-RPC
                                        ┌─────────┴─────────┐
                                        │ Claude Code/Codex │
                                        │   CLI, MCP x16    │
                                        └───────────────────┘
```

- **server が唯一の裁定者**：gating、DAG 順序、コンフリクト処理、トリガー判定はすべて server に集約。daemon は実行するだけで、決して判断しません。
- **コントラクト先行**：エンティティ / REST / WS イベント / daemon フレーム / 定数カタログは、コードより先にコントラクト文書で版管理されます。コードはコントラクトの穴埋めです。`packages/contracts`（Pydantic v2）がリポジトリ内唯一の型ソースで、TS 型は `pnpm gen` で生成され、手編集は禁止です。
- **同型カーネルの単一ソース**：グラフ導出 / フィンガープリント / 分解検証の三つの決定論的カーネル＝Python 権威実装＋TypeScript ミラー＋golden 判例の両言語実行をバイト単位で照合。意味のドリフトは即座にビルドを赤にします。

## リポジトリ構成

| パス | 内容 |
| --- | --- |
| `packages/contracts` | 【唯一のソース】Pydantic v2 モデル＋決定論的 `kernel/` |
| `packages/contracts-ts` | 【生成物】TS 型。`pnpm gen` で再生成 |
| `packages/fixtures` | サンプルデータ＋`golden/` 言語横断判例 |
| `apps/server` | FastAPI 本体（REST / WS / オーケストレーション / ガードレール / デプロイ） |
| `apps/daemon` | エージェント実行体（CLI アダプター / プレビュー / デプロイ runner） |
| `apps/web` | React フロントエンド（Afterglow デザイン言語） |
| `apps/mock-server` | コントラクト駆動 mock（fixtures over REST + WS） |
| `site/` | プロジェクト紹介ページ（静的・単一ファイル） |

## クイックスタート

**動作要件**：Windows（現行 MVP のターゲット環境）· Python ≥ 3.12 + [uv](https://docs.astral.sh/uv/) · Node.js + pnpm 10 · SQLite ≥ 3.35 · ログイン済みの [Claude Code](https://claude.com/claude-code) CLI（実エージェントの実行に必要）

```bash
# インストール
uv sync
pnpm install

# ターミナル 1：バックエンド
uv run coagentia-server            # http://127.0.0.1:8787

# ターミナル 2：フロントエンド
pnpm --filter @coagentia/web dev   # http://127.0.0.1:5173（/api → 8787 にプロキシ）
```

同一オリジン単一プロセス運用：`pnpm --filter @coagentia/web build` の後 `uv run coagentia-server` を起動し、`http://127.0.0.1:8787` を開きます。

## 開発ゲート（全部グリーンで初めて完了）

```bash
uv run pytest -q                    # バックエンド＋コントラクトテスト（現在 1122 passed）
pnpm -F @coagentia/web test         # フロントエンド vitest（現在 512）
pnpm typecheck                      # pyright 0 エラー＋二重 tsc
uv run ruff check .
pnpm gen                            # 実行後 git diff は空であること（生成の決定論性）
pnpm -F @coagentia/web build
```

## 現状

MVP の全計画マイルストーン（M1–M8）が完了：IM 基盤 → タスク → コントラクトとキャンバス → ガードレール → デュアル runtime とテンプレート → デリバリーチェーン → 分解チェーン → プレビュー / デプロイ / オーケストレーション品質ライン。各マイルストーンは隔離環境での実機検証（実 server＋実 daemon＋実 git サブプロセス）と敵対的コードレビューをもって収束しています。

既知の境界：単機・単一ワークスペース・単一ユーザー信頼モデル。マルチユーザー / マルチテナント / マルチマシンは今後のロードマップです。
