-- ============================================================================
-- PokéLab AI · schema.sql  （SQLite 3.4x）
-- 对应《宝可梦卡牌AI对战智能体_数据库设计_v1.0.md》（v1.1）
-- 设计决策：INTEGER 自增主键 / TEXT ISO8601 本地时间 / 外键强制 / 事件 JSONL 单列
-- 说明：updated_at 由应用层维护（可另加触发器）；预设卡组由应用首启导入（SQL 不内置）
-- v1.1 变更：①decks 增加归档语义（is_archived/archived_at，ADR-DB-001）②matches 增加 batch_task_id
-- （批量产出局可反查任务）③索引 idx_decks_list / idx_matches_batch
-- ============================================================================

PRAGMA foreign_keys = ON;

-- ----------------------------------------------------------------------------
-- T1 · users 用户表（用户域 / F13）
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE,
    password_hash TEXT    NOT NULL,
    nickname      TEXT,
    role          TEXT    NOT NULL DEFAULT 'user'   CHECK (role IN ('admin', 'user', 'guest')),
    status        TEXT    NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled')),
    daily_quota   INTEGER NOT NULL DEFAULT 10       CHECK (daily_quota >= 0),
    created_at    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- ----------------------------------------------------------------------------
-- T2 · cards 卡牌表（卡牌域 / F2 · M2）
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cards (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    series        TEXT NOT NULL,
    number        TEXT NOT NULL,
    supertype     TEXT,                                    -- Pokemon / Trainer / Energy
    types         TEXT,                                    -- JSON 数组文本，如 ["grass","fire"]
    hp            INTEGER,
    rules_text    TEXT,                                    -- 原始效果文本（合规引用源）
    attacks_json  TEXT,                                    -- 招式信息 JSON（文字卡渲染）
    effects_json  TEXT,                                    -- LLM 解析产物（引擎执行 / 双轨）
    status        TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('active', 'pending', 'unsupported')),
    source        TEXT,                                    -- 数据来源 / 许可标记
    created_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE (series, number)
);

-- ----------------------------------------------------------------------------
-- T3 · rule_chunks 规则知识块（卡牌域 / F8 · RAG 源；embedding 本体存向量文件）
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS rule_chunks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id       INTEGER REFERENCES cards(id) ON DELETE SET NULL,
    source_ref    TEXT NOT NULL,                           -- 规则书章节 / FAQ 出处
    chunk_text    TEXT NOT NULL,
    embedding_key TEXT,                                    -- 向量索引文件中的键
    status        TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'retired')),
    created_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- ----------------------------------------------------------------------------
-- T4 · decks 卡组表（构筑域 / F3 · M3；游客/预设卡组 owner_id 为空）
-- 删除策略（ADR-DB-001）：卡组被对局/任务/模型引用（RESTRICT），禁止硬删 →
-- UI「删除」= 归档（is_archived=1 + archived_at）；物理清理仅限无引用；用户注销前应用层先归档其卡组
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS decks (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id       INTEGER REFERENCES users(id) ON DELETE SET NULL,
    name           TEXT NOT NULL,
    format         TEXT NOT NULL DEFAULT 'standard',
    legal          INTEGER NOT NULL DEFAULT 1 CHECK (legal IN (0, 1)),
    is_preset      INTEGER NOT NULL DEFAULT 0 CHECK (is_preset IN (0, 1)),
    is_archived    INTEGER NOT NULL DEFAULT 0 CHECK (is_archived IN (0, 1)),
    archived_at    TEXT,
    meta_archetype TEXT,                                    -- 如 dragapult_dusknoir
    deck_text_raw  TEXT,                                    -- 导入原文（可回溯）
    created_at     TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at     TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- ----------------------------------------------------------------------------
-- T5 · deck_cards 卡组明细（构筑域 / F3；桥接 decks × cards）
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS deck_cards (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    deck_id  INTEGER NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
    card_id  INTEGER NOT NULL REFERENCES cards(id),
    quantity INTEGER NOT NULL DEFAULT 1 CHECK (quantity > 0),  -- 同名上限 4（基础能量除外）由引擎层校验
    UNIQUE (deck_id, card_id)
);

-- ----------------------------------------------------------------------------
-- T6 · matches 对局表（对战域 / F5 · M5）
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS matches (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id       INTEGER REFERENCES users(id) ON DELETE SET NULL,  -- 游客可空
    mode           TEXT NOT NULL CHECK (mode IN ('human_vs_ai', 'ai_vs_ai')),
    seed           TEXT NOT NULL,
    deck_a_id      INTEGER NOT NULL REFERENCES decks(id),
    deck_b_id      INTEGER NOT NULL REFERENCES decks(id),
    deck_a_agent   TEXT,            -- human / baseline_ai / dragapult_dusknoir_v1 ...
    deck_b_agent   TEXT,
    batch_task_id  INTEGER REFERENCES batch_tasks(id),   -- 批量产出局→可反查任务（人机局为 NULL）
    first_player   TEXT NOT NULL CHECK (first_player IN ('A', 'B')),
    winner         TEXT CHECK (winner IN ('A', 'B', 'DRAW')),  -- 进行中为 NULL（断线恢复）
    duration_s     REAL,
    events_log     TEXT,            -- JSONL 事件流（决策 3：不建行级事件表）
    engine_version TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at     TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- ----------------------------------------------------------------------------
-- T7 · batch_tasks 批量对战任务（批量域 / F6 · M6）
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS batch_tasks (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    task_type      TEXT NOT NULL CHECK (task_type IN ('a_vs_b', 'a_vs_pool')),
    deck_a_id      INTEGER NOT NULL REFERENCES decks(id),
    deck_b_id      INTEGER REFERENCES decks(id),           -- a_vs_pool 时为空，改用 pool_config
    pool_config    TEXT,                                   -- JSON：对手池配置
    total_games    INTEGER NOT NULL DEFAULT 100 CHECK (total_games > 0),
    finished_games INTEGER NOT NULL DEFAULT 0 CHECK (finished_games >= 0),
    status         TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'running', 'done', 'failed', 'canceled')),
    config         TEXT,                                   -- JSON：seed 策略/并行数等
    result_summary TEXT,                                   -- JSON：统计摘要
    cache_key      TEXT UNIQUE,                            -- 同卡组同参数结果缓存
    error          TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at     TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- ----------------------------------------------------------------------------
-- T8 · training_tasks 强化学习训练任务（训练域 / F4 · M4）
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS training_tasks (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    deck_id          INTEGER REFERENCES decks(id),         -- 训练用卡组（专精）
    algorithm        TEXT NOT NULL DEFAULT 'PPO' CHECK (algorithm IN ('BC', 'PPO', 'DQN')),
    base_checkpoint  TEXT,                                 -- 冷启动 checkpoint（BC）
    hyperparams      TEXT,                                 -- JSON：学习率/批大小等
    status           TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'running', 'done', 'failed', 'canceled')),
    progress         INTEGER NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
    current_episodes INTEGER NOT NULL DEFAULT 0,
    log_path         TEXT,
    error            TEXT,
    created_at       TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at       TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- ----------------------------------------------------------------------------
-- T9 · model_registry 模型登记表（训练域 / F4 · M4）
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS model_registry (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    deck_id         INTEGER REFERENCES decks(id) ON DELETE SET NULL,  -- 专精卡组（可空=通用）
    version         TEXT NOT NULL,
    artifact_path   TEXT NOT NULL,
    metric_winrate  REAL,                                   -- K1 发布门槛记录（≥70%）
    status          TEXT NOT NULL DEFAULT 'training' CHECK (status IN ('training', 'ready', 'deprecated', 'retired')),
    created_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- ----------------------------------------------------------------------------
-- T10 · report_exports 报告导出记录（报告域 / F7 · F16；P1 起用）
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS report_exports (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    source_task_id INTEGER REFERENCES batch_tasks(id) ON DELETE SET NULL,
    format         TEXT NOT NULL CHECK (format IN ('csv', 'md', 'json')),
    file_path      TEXT NOT NULL,
    created_at     TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- ============================================================================
-- 索引（按查询频次）
-- ============================================================================
CREATE INDEX IF NOT EXISTS idx_cards_name      ON cards (name);
CREATE INDEX IF NOT EXISTS idx_cards_sn        ON cards (series, number);
CREATE INDEX IF NOT EXISTS idx_cards_status    ON cards (status);
CREATE INDEX IF NOT EXISTS idx_rule_card       ON rule_chunks (card_id);
CREATE INDEX IF NOT EXISTS idx_decks_owner     ON decks (owner_id);
CREATE INDEX IF NOT EXISTS idx_decks_preset    ON decks (is_preset);
CREATE INDEX IF NOT EXISTS idx_decks_list      ON decks (owner_id, is_archived, updated_at);  -- 「我的卡组」列表：非归档 + 时间倒序
CREATE INDEX IF NOT EXISTS idx_deckcards_deck  ON deck_cards (deck_id);
CREATE INDEX IF NOT EXISTS idx_deckcards_card  ON deck_cards (card_id);
CREATE INDEX IF NOT EXISTS idx_matches_owner   ON matches (owner_id, created_at);
CREATE INDEX IF NOT EXISTS idx_matches_deckA   ON matches (deck_a_id);
CREATE INDEX IF NOT EXISTS idx_matches_deckB   ON matches (deck_b_id);
CREATE INDEX IF NOT EXISTS idx_matches_batch   ON matches (batch_task_id);  -- 任务→产出对局反查（F6 审计）
CREATE INDEX IF NOT EXISTS idx_matches_created ON matches (created_at);
CREATE INDEX IF NOT EXISTS idx_batch_owner     ON batch_tasks (owner_id, created_at);
CREATE INDEX IF NOT EXISTS idx_batch_status    ON batch_tasks (status);
CREATE INDEX IF NOT EXISTS idx_train_owner     ON training_tasks (owner_id);
CREATE INDEX IF NOT EXISTS idx_model_deck      ON model_registry (deck_id, status);
CREATE INDEX IF NOT EXISTS idx_report_owner    ON report_exports (owner_id, created_at);

-- ============================================================================
-- 种子数据：仅内置默认管理员占位；预设卡组（A1–A4）由应用首次启动导入
-- 说明：password_hash 需按 bcrypt/argon2 生成，占位符待应用初始化替换
-- ============================================================================
-- INSERT INTO users (username, password_hash, nickname, role)
-- VALUES ('admin', '<hash_placeholder>', 'Administrator', 'admin');
