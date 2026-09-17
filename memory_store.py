import json
import hashlib
import math
import os
import pickle
import sqlite3
from array import array
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from error_detection import ErrorEvent
from error_explanation import (ExplanationResult, LLMErrorEvidence,
                               StructuredExplanation)


DEFAULT_DB = Path(__file__).with_name("review_history.db")
MEMORY_DB = Path(os.getenv("GO_REVIEW_DB", str(DEFAULT_DB)))


def _connect():
    connection = sqlite3.connect(MEMORY_DB)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    _initialize(connection)
    return connection


def _initialize(connection):
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS games (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            board_size INTEGER NOT NULL,
            user_color TEXT NOT NULL,
            board_json TEXT NOT NULL,
            factors_json TEXT NOT NULL,
            tone_choice TEXT NOT NULL,
            low_commentary TEXT NOT NULL,
            high_commentary TEXT NOT NULL,
            position_summary TEXT NOT NULL,
            count_json TEXT NOT NULL,
            game_embedding_text TEXT NOT NULL,
            game_embedding BLOB NOT NULL,
            embedding_model TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS issues (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id INTEGER NOT NULL,
            issue_type TEXT NOT NULL,
            region TEXT NOT NULL,
            severity TEXT NOT NULL,
            evidence TEXT NOT NULL,
            improvement TEXT NOT NULL,
            embedding_text TEXT NOT NULL,
            embedding BLOB NOT NULL,
            FOREIGN KEY(game_id) REFERENCES games(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_games_board_size ON games(board_size);
        CREATE INDEX IF NOT EXISTS idx_issues_game_id ON issues(game_id);
        CREATE INDEX IF NOT EXISTS idx_issues_type ON issues(issue_type);

        CREATE TABLE IF NOT EXISTS memory_games (
            id TEXT PRIMARY KEY,
            source_fingerprint TEXT NOT NULL UNIQUE,
            source_type TEXT NOT NULL,
            board_size INTEGER NOT NULL,
            user_color TEXT NOT NULL,
            belongs_to_user INTEGER NOT NULL DEFAULT 1,
            source_content BLOB,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS memory_error_events (
            id TEXT PRIMARY KEY,
            game_id TEXT NOT NULL,
            player TEXT NOT NULL,
            event_start_move INTEGER NOT NULL,
            event_end_move INTEGER NOT NULL,
            candidate_moves_json TEXT NOT NULL,
            worst_move_number INTEGER NOT NULL,
            phase TEXT NOT NULL,
            severity_score REAL NOT NULL,
            sum_winrate_loss REAL NOT NULL,
            sum_score_loss REAL NOT NULL,
            net_winrate_loss REAL NOT NULL,
            net_score_loss REAL NOT NULL,
            peak_winrate_loss REAL NOT NULL,
            peak_score_loss REAL NOT NULL,
            actual_move TEXT NOT NULL,
            recommended_move TEXT NOT NULL,
            board_snapshot_json TEXT,
            is_important INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(game_id) REFERENCES memory_games(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS memory_error_evidence (
            event_id TEXT PRIMARY KEY,
            schema_version TEXT NOT NULL,
            evidence_fingerprint TEXT NOT NULL,
            rank INTEGER NOT NULL,
            deep_black_winrate_before REAL NOT NULL,
            deep_black_winrate_after REAL NOT NULL,
            deep_black_score_lead_before REAL NOT NULL,
            deep_black_score_lead_after REAL NOT NULL,
            player_winrate_loss REAL NOT NULL,
            player_score_loss REAL NOT NULL,
            visits INTEGER NOT NULL,
            warnings_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(event_id) REFERENCES memory_error_events(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS memory_error_explanations (
            event_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            error_code TEXT,
            title TEXT,
            summary TEXT,
            why_it_matters TEXT,
            better_plan TEXT,
            learning_point TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(event_id) REFERENCES memory_error_events(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS memory_error_embeddings (
            event_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            embedding_text TEXT,
            embedding BLOB,
            embedding_model TEXT,
            error_code TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(event_id) REFERENCES memory_error_events(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS memory_similarity_feedback (
            current_event_id TEXT NOT NULL,
            historical_event_id TEXT NOT NULL,
            feedback TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(current_event_id, historical_event_id),
            FOREIGN KEY(current_event_id) REFERENCES memory_error_events(id) ON DELETE CASCADE,
            FOREIGN KEY(historical_event_id) REFERENCES memory_error_events(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_memory_events_game
            ON memory_error_events(game_id);
        CREATE INDEX IF NOT EXISTS idx_memory_embeddings_status
            ON memory_error_embeddings(status);

        CREATE TABLE IF NOT EXISTS review_run_cache (
            id TEXT PRIMARY KEY,
            request_fingerprint TEXT NOT NULL UNIQUE,
            sgf_fingerprint TEXT NOT NULL,
            user_color TEXT NOT NULL,
            route TEXT NOT NULL,
            target_move INTEGER,
            goal_fingerprint TEXT NOT NULL,
            payload BLOB NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_review_run_cache_updated
            ON review_run_cache(updated_at);
        """
    )
    game_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(memory_games)")
    }
    if "source_content" not in game_columns:
        connection.execute("ALTER TABLE memory_games ADD COLUMN source_content BLOB")
    event_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(memory_error_events)")
    }
    if "board_snapshot_json" not in event_columns:
        connection.execute(
            "ALTER TABLE memory_error_events ADD COLUMN board_snapshot_json TEXT"
        )


def _vector_to_blob(vector):
    return array("f", vector).tobytes()


def _blob_to_vector(blob):
    values = array("f")
    values.frombytes(blob)
    return list(values)


def _cosine_similarity(left, right):
    if not left or not right or len(left) != len(right):
        return None
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0 or right_norm == 0:
        return None
    return dot / (left_norm * right_norm)


def load_history():
    """读取历史记录的轻量摘要，供界面统计。"""
    with _connect() as connection:
        rows = connection.execute(
            """SELECT id, created_at, board_size, user_color,
                      low_commentary, high_commentary, position_summary
               FROM games ORDER BY id DESC"""
        ).fetchall()
    return [dict(row) for row in rows]


def load_mistake_records():
    """读取错题本展示字段；不加载 embedding，也不改变存储 schema。"""
    with _connect() as connection:
        games = connection.execute(
            """SELECT id, created_at, board_size, user_color, board_json,
                      high_commentary, position_summary, count_json
               FROM games ORDER BY id DESC"""
        ).fetchall()
        result = []
        for game in games:
            issues = connection.execute(
                """SELECT issue_type, region, severity, evidence, improvement
                   FROM issues WHERE game_id = ? ORDER BY id""", (game["id"],)
            ).fetchall()
            item = dict(game)
            item["board_data"] = json.loads(item.pop("board_json"))
            item["count_estimate"] = json.loads(item.pop("count_json"))
            item["issues"] = [dict(issue) for issue in issues]
            result.append(item)
        event_rows = connection.execute(
            """SELECT e.id, e.game_id, e.created_at, g.board_size, g.user_color,
                      e.worst_move_number, e.phase, e.severity_score,
                      e.actual_move, e.recommended_move,
                      e.board_snapshot_json,
                      x.status, x.title, x.summary, x.why_it_matters,
                      x.better_plan, x.learning_point
               FROM memory_error_events e
               JOIN memory_games g ON g.id=e.game_id
               JOIN memory_error_explanations x ON x.event_id=e.id
               WHERE g.belongs_to_user=1
               ORDER BY e.created_at DESC"""
        ).fetchall()
        for row in event_rows:
            item = dict(row)
            title = item.get("title") or "解释待补充"
            summary = item.get("summary") or "KataGo 证据已保存，解释尚未完成。"
            item.update({
                "board_data": _board_data_from_snapshot(
                    json.loads(item["board_snapshot_json"])
                    if item.get("board_snapshot_json") else None
                ),
                "count_estimate": {},
                "position_summary": f"第 {item['worst_move_number']} 手 · {title}",
                "high_commentary": summary,
                "issues": [{
                    "issue_type": title,
                    "region": item["phase"],
                    "severity": f"{item['severity_score']:.2f}",
                    "evidence": (
                        f"实战 {item['actual_move']}；推荐 {item['recommended_move']}。"
                        f"{item.get('why_it_matters') or ''}"
                    ),
                    "improvement": item.get("better_plan") or "等待解释补充",
                }],
            })
            result.append(item)
    result.sort(key=lambda item: item["created_at"], reverse=True)
    return result


def _board_data_from_snapshot(snapshot):
    if snapshot is None:
        return None
    stones = [
        {"x": point[0], "y": point[1], "color": color}
        for color, key in (("black", "black"), ("white", "white"))
        for point in snapshot.get(key, [])
    ]
    return {"board_size": snapshot["board_size"], "stones": stones}


def load_mistake_game_context(event_id):
    """Load persisted SGF, primary move, snapshot and best available review cache."""
    with _connect() as connection:
        row = connection.execute(
            """SELECT e.id, e.game_id, e.worst_move_number,
                      e.board_snapshot_json, g.source_content,
                      g.source_fingerprint, g.user_color, g.board_size,
                      x.title, x.summary, x.why_it_matters, x.better_plan,
                      x.learning_point, v.player_winrate_loss,
                      v.player_score_loss, e.actual_move, e.recommended_move
               FROM memory_error_events e
               JOIN memory_games g ON g.id=e.game_id
               JOIN memory_error_explanations x ON x.event_id=e.id
               JOIN memory_error_evidence v ON v.event_id=e.id
               WHERE e.id=?""",
            (event_id,),
        ).fetchone()
        if row is None:
            return None
        cached = connection.execute(
            """SELECT payload FROM review_run_cache
               WHERE sgf_fingerprint=? AND user_color=?
               ORDER BY CASE route WHEN 'full_review' THEN 0 ELSE 1 END,
                        updated_at DESC LIMIT 1""",
            (row["source_fingerprint"], row["user_color"]),
        ).fetchone()
    result = dict(row)
    result["board_snapshot"] = (
        json.loads(result.pop("board_snapshot_json"))
        if result.get("board_snapshot_json") else None
    )
    result["review_run"] = pickle.loads(cached["payload"]) if cached else None
    return result


def load_dashboard_summary():
    """Aggregate games and saved ErrorEvents without treating headlines as types."""
    with _connect() as connection:
        cached_games = connection.execute(
            """SELECT sgf_fingerprint, MAX(updated_at) AS analyzed_at
               FROM review_run_cache GROUP BY sgf_fingerprint"""
        ).fetchall()
        memory_games = connection.execute(
            """SELECT source_fingerprint, MAX(updated_at) AS analyzed_at
               FROM memory_games WHERE belongs_to_user=1
               GROUP BY source_fingerprint"""
        ).fetchall()
        legacy_games = connection.execute(
            "SELECT id, created_at FROM games"
        ).fetchall()
        error_count = connection.execute(
            """SELECT COUNT(*) FROM memory_error_events e
               JOIN memory_games g ON g.id=e.game_id
               WHERE g.belongs_to_user=1"""
        ).fetchone()[0]
        phases = connection.execute(
            """SELECT e.phase, COUNT(*) AS count
               FROM memory_error_events e
               JOIN memory_games g ON g.id=e.game_id
               WHERE g.belongs_to_user=1 GROUP BY e.phase"""
        ).fetchall()
    games = {row["sgf_fingerprint"]: row["analyzed_at"] for row in cached_games}
    for row in memory_games:
        current = games.get(row["source_fingerprint"])
        if current is None or row["analyzed_at"] > current:
            games[row["source_fingerprint"]] = row["analyzed_at"]
    games.update({f"legacy:{row['id']}": row["created_at"] for row in legacy_games})
    by_day = {}
    for value in games.values():
        day = value[:10]
        by_day[day] = by_day.get(day, 0) + 1
    return {
        "total_reviews": len(games),
        "game_dates": list(games.values()),
        "games_by_date": dict(sorted(by_day.items())),
        "mistake_count": error_count,
        "phase_counts": {row["phase"]: row["count"] for row in phases},
    }


def _review_request_fingerprint(sgf_content, user_color, goal, target_move):
    identity = {
        "sgf": _sha256(sgf_content),
        "user_color": user_color,
        "goal": goal.strip(),
        "target_move": target_move,
    }
    return _sha256(_canonical_json(identity))


def save_review_run_cache(run, route):
    """Persist one completed internal review result in the existing SQLite DB."""
    if run.result.status != "completed":
        return None
    request = run.request
    if route not in {"full_review", "targeted_review", "memory_review"}:
        raise ValueError("Unsupported review route")
    request_fingerprint = _review_request_fingerprint(
        request.sgf_content, request.user_color, request.goal, request.target_move
    )
    identity = {
        "sgf": _sha256(request.sgf_content),
        "user_color": request.user_color,
        "route": route,
        "target_move": request.target_move,
        "goal": _sha256(request.goal.strip()),
    }
    cache_id = f"review_{_sha256(_canonical_json(identity))}"
    payload = pickle.dumps(run, protocol=pickle.HIGHEST_PROTOCOL)
    now = _utc_now()
    with _connect() as connection:
        connection.execute(
            """INSERT INTO review_run_cache (
                   id, request_fingerprint, sgf_fingerprint, user_color,
                   route, target_move, goal_fingerprint, payload,
                   created_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(request_fingerprint) DO UPDATE SET
                   id=excluded.id, route=excluded.route, payload=excluded.payload,
                   updated_at=excluded.updated_at""",
            (cache_id, request_fingerprint, identity["sgf"], request.user_color,
             route, request.target_move, identity["goal"], payload, now, now),
        )
    return cache_id


def load_review_run_cache(sgf_content, user_color, goal, target_move):
    fingerprint = _review_request_fingerprint(
        sgf_content, user_color, goal, target_move
    )
    with _connect() as connection:
        row = connection.execute(
            "SELECT payload FROM review_run_cache WHERE request_fingerprint=?",
            (fingerprint,),
        ).fetchone()
    return pickle.loads(row["payload"]) if row is not None else None


def load_latest_review_run_cache():
    with _connect() as connection:
        row = connection.execute(
            "SELECT payload FROM review_run_cache ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
    return pickle.loads(row["payload"]) if row is not None else None


def find_most_similar(game_vector, issues, board_size):
    """同棋盘规格内检索；综合分为35%局面相似度+65%最佳错误相似度。"""
    with _connect() as connection:
        game_rows = connection.execute(
            """SELECT id, created_at, high_commentary,
                      position_summary, game_embedding
               FROM games WHERE board_size = ?""",
            (board_size,),
        ).fetchall()
        best_match = None
        for game_row in game_rows:
            game_similarity = _cosine_similarity(
                game_vector, _blob_to_vector(game_row["game_embedding"])
            )
            if game_similarity is None:
                continue
            historical_issues = connection.execute(
                """SELECT issue_type, region, embedding_text, embedding
                   FROM issues WHERE game_id = ?""",
                (game_row["id"],),
            ).fetchall()
            best_issue_similarity = None
            best_issue_pair = None
            for current_issue in issues:
                for historical_issue in historical_issues:
                    similarity = _cosine_similarity(
                        current_issue["embedding"],
                        _blob_to_vector(historical_issue["embedding"]),
                    )
                    if similarity is not None and (best_issue_similarity is None or similarity > best_issue_similarity):
                        best_issue_similarity = similarity
                        best_issue_pair = {
                            "current_issue_type": current_issue["issue_type"],
                            "historical_issue_type": historical_issue["issue_type"],
                            "historical_region": historical_issue["region"],
                            "historical_embedding_text": historical_issue["embedding_text"],
                        }
            score = game_similarity if best_issue_similarity is None else 0.35 * game_similarity + 0.65 * best_issue_similarity
            candidate = {
                "record_id": game_row["id"],
                "created_at": game_row["created_at"],
                "score": score,
                "game_similarity": game_similarity,
                "issue_similarity": best_issue_similarity,
                "issue_pair": best_issue_pair,
                "position_summary": game_row["position_summary"],
                "commentary": game_row["high_commentary"],
            }
            if best_match is None or candidate["score"] > best_match["score"]:
                best_match = candidate
    return best_match


def save_record(board_data, user_color, factors, tone_choice, low_commentary,
                high_result, game_embedding_text, game_vector, issue_vectors,
                embedding_model):
    """在一个事务中保存棋局、两版点评、盘面估算和错误向量。"""
    if len(issue_vectors) != len(high_result["issues"]):
        raise ValueError("错误条目与错误向量数量不一致")
    with _connect() as connection:
        cursor = connection.execute(
            """INSERT INTO games (
                created_at, board_size, user_color, board_json, factors_json,
                tone_choice, low_commentary, high_commentary, position_summary,
                count_json, game_embedding_text, game_embedding, embedding_model
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                board_data["board_size"], user_color,
                json.dumps(board_data, ensure_ascii=False),
                json.dumps(factors, ensure_ascii=False), tone_choice,
                low_commentary, high_result["commentary"],
                high_result["position_summary"],
                json.dumps(high_result["count_estimate"], ensure_ascii=False),
                game_embedding_text, _vector_to_blob(game_vector), embedding_model,
            ),
        )
        game_id = cursor.lastrowid
        for issue, vector in zip(high_result["issues"], issue_vectors):
            connection.execute(
                """INSERT INTO issues (
                    game_id, issue_type, region, severity, evidence,
                    improvement, embedding_text, embedding
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (game_id, issue["issue_type"], issue["region"], issue["severity"],
                 issue["evidence"], issue["improvement"], issue["embedding_text"],
                 _vector_to_blob(vector)),
            )
    return game_id


def _utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _canonical_json(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256(value):
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def game_memory_id(game_content):
    """Return a deterministic game id from stable source content."""
    if not isinstance(game_content, (str, bytes)) or not game_content:
        raise ValueError("game_content must be non-empty text or bytes")
    return f"game_{_sha256(game_content)}"


def error_memory_id(game_id, event):
    """Return a deterministic logical-event id independent of LLM output."""
    identity = {
        "game_id": game_id,
        "player": event.player,
        "event_start_move": event.start_move,
        "event_end_move": event.end_move,
        "candidate_move_numbers": list(event.move_numbers),
        "worst_move_number": event.worst_move_number,
    }
    return f"error_{_sha256(_canonical_json(identity))}"


def _evidence_fingerprint(evidence):
    return _sha256(_canonical_json(evidence.to_dict()))


def _validate_memory_input(event, evidence, explanation_result):
    if explanation_result.evidence != evidence:
        raise ValueError("ExplanationResult evidence does not match evidence")
    if event.player != evidence.player:
        raise ValueError("ErrorEvent player does not match evidence")
    if (event.start_move, event.end_move) != (
        evidence.event_start_move,
        evidence.event_end_move,
    ):
        raise ValueError("ErrorEvent range does not match evidence")
    if event.worst_move_number != evidence.worst_move_number:
        raise ValueError("ErrorEvent worst move does not match evidence")
    if event.worst_move_number not in event.move_numbers:
        raise ValueError("Worst move must be one of the event candidate moves")
    if explanation_result.status == "succeeded":
        if explanation_result.explanation is None or explanation_result.error_code is not None:
            raise ValueError("Succeeded explanation result is inconsistent")
    elif explanation_result.status == "pending":
        if explanation_result.explanation is not None:
            raise ValueError("Pending explanation result cannot contain an explanation")
    else:
        raise ValueError("Unsupported explanation status")


def build_semantic_mistake_text(evidence, explanation):
    """Build neutral deterministic embedding text without numeric telemetry."""
    if not isinstance(explanation, StructuredExplanation):
        raise ValueError("A validated StructuredExplanation is required")
    fields = asdict(explanation)
    if any(not isinstance(value, str) or not value.strip() for value in fields.values()):
        raise ValueError("Explanation fields must be non-empty strings")
    player = "黑棋" if evidence.player == "B" else "白棋"
    return (
        f"围棋错误模式；阶段：{evidence.phase}；行棋方：{player}；"
        f"实战着：{evidence.actual_move}；推荐着：{evidence.deep_best_move_before}；"
        f"错误：{explanation.title.strip()}；"
        f"情况：{explanation.summary.strip()}；"
        f"影响：{explanation.why_it_matters.strip()}；"
        f"改进：{explanation.better_plan.strip()}；"
        f"学习点：{explanation.learning_point.strip()}"
    )


def _upsert_explanation(connection, event_id, result, now):
    existing = connection.execute(
        "SELECT * FROM memory_error_explanations WHERE event_id = ?",
        (event_id,),
    ).fetchone()
    if (
        existing is not None
        and existing["status"] == "succeeded"
        and result.status == "pending"
    ):
        return StructuredExplanation(
            title=existing["title"],
            summary=existing["summary"],
            why_it_matters=existing["why_it_matters"],
            better_plan=existing["better_plan"],
            learning_point=existing["learning_point"],
        )

    explanation = result.explanation
    values = (
        result.status,
        result.error_code,
        explanation.title if explanation else None,
        explanation.summary if explanation else None,
        explanation.why_it_matters if explanation else None,
        explanation.better_plan if explanation else None,
        explanation.learning_point if explanation else None,
    )
    connection.execute(
        """INSERT INTO memory_error_explanations (
               event_id, status, error_code, title, summary, why_it_matters,
               better_plan, learning_point, created_at, updated_at
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(event_id) DO UPDATE SET
               status=excluded.status,
               error_code=excluded.error_code,
               title=excluded.title,
               summary=excluded.summary,
               why_it_matters=excluded.why_it_matters,
               better_plan=excluded.better_plan,
               learning_point=excluded.learning_point,
               updated_at=excluded.updated_at""",
        (event_id, *values, now, now),
    )
    return explanation


def _upsert_embedding_state(connection, event_id, embedding_text, now):
    existing = connection.execute(
        "SELECT embedding_text FROM memory_error_embeddings WHERE event_id = ?",
        (event_id,),
    ).fetchone()
    if existing is None:
        connection.execute(
            """INSERT INTO memory_error_embeddings (
                   event_id, status, embedding_text, embedding, embedding_model,
                   error_code, created_at, updated_at
               ) VALUES (?, 'pending', ?, NULL, NULL, NULL, ?, ?)""",
            (event_id, embedding_text, now, now),
        )
    elif existing["embedding_text"] != embedding_text:
        connection.execute(
            """UPDATE memory_error_embeddings
               SET status='pending', embedding_text=?, embedding=NULL,
                   embedding_model=NULL, error_code=NULL, updated_at=?
               WHERE event_id=?""",
            (embedding_text, now, event_id),
        )


def save_error_memory(
    game_content,
    board_size,
    user_color,
    event,
    evidence,
    explanation_result,
    *,
    source_type="sgf",
    belongs_to_user=True,
    embedding_fn=None,
    embedding_model=None,
):
    """Transactionally persist an event, evidence, and explanation state."""
    if type(board_size) is not int or board_size <= 0:
        raise ValueError("board_size must be a positive integer")
    if user_color not in {"B", "W"}:
        raise ValueError("user_color must be 'B' or 'W'")
    if type(belongs_to_user) is not bool:
        raise ValueError("belongs_to_user must be boolean")
    _validate_memory_input(event, evidence, explanation_result)

    game_id = game_memory_id(game_content)
    event_id = error_memory_id(game_id, event)
    source_fingerprint = _sha256(game_content)
    evidence_fingerprint = _evidence_fingerprint(evidence)
    now = _utc_now()
    source_content = (
        game_content.encode("utf-8")
        if isinstance(game_content, str) else game_content
    )
    board_snapshot = None
    if source_type == "sgf":
        from sgf_ingestion import parse_sgf
        try:
            position = parse_sgf(source_content).position(event.worst_move_number)
        except ValueError:
            position = None
        if position is not None:
            board_snapshot = {
                "board_size": position.board_data["board_size"],
                "black": [[stone["x"], stone["y"]] for stone in position.board_data["stones"]
                          if stone["color"] == "black"],
                "white": [[stone["x"], stone["y"]] for stone in position.board_data["stones"]
                          if stone["color"] == "white"],
                "move_number": event.worst_move_number,
            }

    with _connect() as connection:
        connection.execute(
            """INSERT INTO memory_games (
                   id, source_fingerprint, source_type, board_size, user_color,
                   belongs_to_user, source_content, created_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   source_type=excluded.source_type,
                   board_size=excluded.board_size,
                   user_color=excluded.user_color,
                   belongs_to_user=excluded.belongs_to_user,
                   source_content=COALESCE(memory_games.source_content, excluded.source_content),
                   updated_at=excluded.updated_at""",
            (
                game_id, source_fingerprint, source_type, board_size, user_color,
                int(belongs_to_user), source_content, now, now,
            ),
        )
        connection.execute(
            """INSERT INTO memory_error_events (
                   id, game_id, player, event_start_move, event_end_move,
                   candidate_moves_json, worst_move_number, phase,
                   severity_score, sum_winrate_loss, sum_score_loss,
                   net_winrate_loss, net_score_loss, peak_winrate_loss,
                   peak_score_loss, actual_move, recommended_move,
                   board_snapshot_json, created_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   phase=excluded.phase,
                   severity_score=excluded.severity_score,
                   sum_winrate_loss=excluded.sum_winrate_loss,
                   sum_score_loss=excluded.sum_score_loss,
                   net_winrate_loss=excluded.net_winrate_loss,
                   net_score_loss=excluded.net_score_loss,
                   peak_winrate_loss=excluded.peak_winrate_loss,
                   peak_score_loss=excluded.peak_score_loss,
                   actual_move=excluded.actual_move,
                   recommended_move=excluded.recommended_move,
                   board_snapshot_json=excluded.board_snapshot_json,
                   updated_at=excluded.updated_at""",
            (
                event_id, game_id, event.player, event.start_move, event.end_move,
                _canonical_json(list(event.move_numbers)), event.worst_move_number,
                evidence.phase, event.severity_score, event.sum_winrate_loss,
                event.sum_score_loss, event.net_winrate_loss,
                event.net_score_loss, event.peak_winrate_loss,
                event.peak_score_loss, evidence.actual_move,
                evidence.deep_best_move_before,
                _canonical_json(board_snapshot) if board_snapshot else None,
                now, now,
            ),
        )
        connection.execute(
            """INSERT INTO memory_error_evidence (
                   event_id, schema_version, evidence_fingerprint, rank,
                   deep_black_winrate_before, deep_black_winrate_after,
                   deep_black_score_lead_before, deep_black_score_lead_after,
                   player_winrate_loss, player_score_loss, visits, warnings_json,
                   created_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(event_id) DO UPDATE SET
                   schema_version=excluded.schema_version,
                   evidence_fingerprint=excluded.evidence_fingerprint,
                   rank=excluded.rank,
                   deep_black_winrate_before=excluded.deep_black_winrate_before,
                   deep_black_winrate_after=excluded.deep_black_winrate_after,
                   deep_black_score_lead_before=excluded.deep_black_score_lead_before,
                   deep_black_score_lead_after=excluded.deep_black_score_lead_after,
                   player_winrate_loss=excluded.player_winrate_loss,
                   player_score_loss=excluded.player_score_loss,
                   visits=excluded.visits,
                   warnings_json=excluded.warnings_json,
                   updated_at=excluded.updated_at""",
            (
                event_id, evidence.schema_version, evidence_fingerprint,
                evidence.rank, evidence.deep_black_winrate_before,
                evidence.deep_black_winrate_after,
                evidence.deep_black_score_lead_before,
                evidence.deep_black_score_lead_after,
                evidence.player_winrate_loss, evidence.player_score_loss,
                evidence.visits, _canonical_json(evidence.warnings), now, now,
            ),
        )
        explanation = _upsert_explanation(
            connection, event_id, explanation_result, now
        )
        embedding_text = (
            build_semantic_mistake_text(evidence, explanation)
            if explanation is not None
            else None
        )
        _upsert_embedding_state(connection, event_id, embedding_text, now)

    if embedding_fn is not None and embedding_text is not None:
        update_error_embedding(
            event_id,
            embedding_fn,
            embedding_model=embedding_model,
        )
    return load_error_memory(event_id)


def load_error_memory(event_id):
    with _connect() as connection:
        row = connection.execute(
            """SELECT e.*, g.source_fingerprint, g.source_type, g.board_size,
                      g.user_color, g.belongs_to_user,
                      v.schema_version, v.rank,
                      v.deep_black_winrate_before, v.deep_black_winrate_after,
                      v.deep_black_score_lead_before,
                      v.deep_black_score_lead_after,
                      v.player_winrate_loss, v.player_score_loss, v.visits,
                      v.warnings_json, x.status AS explanation_status,
                      x.error_code AS explanation_error_code, x.title, x.summary,
                      x.why_it_matters, x.better_plan, x.learning_point,
                      m.status AS embedding_status,
                      m.embedding_text, m.embedding_model,
                      m.error_code AS embedding_error_code
               FROM memory_error_events e
               JOIN memory_games g ON g.id=e.game_id
               JOIN memory_error_evidence v ON v.event_id=e.id
               JOIN memory_error_explanations x ON x.event_id=e.id
               JOIN memory_error_embeddings m ON m.event_id=e.id
               WHERE e.id=?""",
            (event_id,),
        ).fetchone()
    if row is None:
        return None
    result = dict(row)
    result["candidate_move_numbers"] = json.loads(
        result.pop("candidate_moves_json")
    )
    result["warnings"] = json.loads(result.pop("warnings_json"))
    result["belongs_to_user"] = bool(result["belongs_to_user"])
    result["is_important"] = bool(result["is_important"])
    return result


def update_error_explanation(
    event_id,
    explanation_result,
    *,
    embedding_fn=None,
    embedding_model=None,
):
    """Fill or refresh explanation state without creating another event."""
    if explanation_result.status != "succeeded":
        raise ValueError("A succeeded explanation is required")
    evidence = explanation_result.evidence
    now = _utc_now()
    with _connect() as connection:
        stored = connection.execute(
            """SELECT evidence_fingerprint
               FROM memory_error_evidence WHERE event_id=?""",
            (event_id,),
        ).fetchone()
        if stored is None:
            raise ValueError("Unknown error event")
        if stored["evidence_fingerprint"] != _evidence_fingerprint(evidence):
            raise ValueError("Explanation evidence does not match stored evidence")
        explanation = _upsert_explanation(
            connection, event_id, explanation_result, now
        )
        embedding_text = build_semantic_mistake_text(evidence, explanation)
        _upsert_embedding_state(connection, event_id, embedding_text, now)

    if embedding_fn is not None:
        update_error_embedding(
            event_id,
            embedding_fn,
            embedding_model=embedding_model,
        )
    return load_error_memory(event_id)


def update_error_embedding(event_id, embedding_fn, *, embedding_model=None):
    """Generate an embedding after durable evidence has already committed."""
    with _connect() as connection:
        row = connection.execute(
            """SELECT embedding_text FROM memory_error_embeddings
               WHERE event_id=?""",
            (event_id,),
        ).fetchone()
    if row is None or not row["embedding_text"]:
        raise ValueError("Event has no embedding-ready explanation")

    try:
        vectors = embedding_fn([row["embedding_text"]])
        if len(vectors) != 1 or not vectors[0]:
            raise ValueError("Embedding provider returned no vector")
        blob = _vector_to_blob(vectors[0])
    except Exception as error:
        with _connect() as connection:
            connection.execute(
                """UPDATE memory_error_embeddings
                   SET status='failed', embedding=NULL, embedding_model=?,
                       error_code=?, updated_at=? WHERE event_id=?""",
                (
                    embedding_model,
                    type(error).__name__,
                    _utc_now(),
                    event_id,
                ),
            )
        return load_error_memory(event_id)

    with _connect() as connection:
        connection.execute(
            """UPDATE memory_error_embeddings
               SET status='succeeded', embedding=?, embedding_model=?,
                   error_code=NULL, updated_at=? WHERE event_id=?""",
            (blob, embedding_model, _utc_now(), event_id),
        )
    return load_error_memory(event_id)


def search_similar_errors(
    query_vector=None,
    *,
    current_event_id=None,
    exclude_game_id=None,
    top_k=5,
    personal_only=True,
):
    """Rank historical event memories by cosine similarity."""
    if type(top_k) is not int or top_k <= 0:
        raise ValueError("top_k must be a positive integer")
    if query_vector is None:
        if current_event_id is None:
            raise ValueError("query_vector or current_event_id is required")
        with _connect() as connection:
            query_row = connection.execute(
                """SELECT embedding FROM memory_error_embeddings
                   WHERE event_id=? AND status='succeeded'""",
                (current_event_id,),
            ).fetchone()
        if query_row is None:
            return []
        query_vector = _blob_to_vector(query_row["embedding"])

    suppressed = set()
    if current_event_id is not None:
        with _connect() as connection:
            suppressed = set()
            for row in connection.execute(
                """SELECT current_event_id, historical_event_id
                   FROM memory_similarity_feedback
                   WHERE feedback='not_same_error'
                     AND (current_event_id=? OR historical_event_id=?)""",
                (current_event_id, current_event_id),
            ).fetchall():
                other = (
                    row["historical_event_id"]
                    if row["current_event_id"] == current_event_id
                    else row["current_event_id"]
                )
                suppressed.add(other)

    with _connect() as connection:
        rows = connection.execute(
            """SELECT e.id AS event_id, e.game_id, e.player, e.phase,
                      e.event_start_move, e.event_end_move,
                      e.worst_move_number, e.actual_move, e.recommended_move,
                      e.is_important, g.belongs_to_user,
                      x.title, x.summary, x.learning_point,
                      m.embedding_text, m.embedding
               FROM memory_error_events e
               JOIN memory_games g ON g.id=e.game_id
               JOIN memory_error_explanations x ON x.event_id=e.id
               JOIN memory_error_embeddings m ON m.event_id=e.id
               WHERE m.status='succeeded'"""
        ).fetchall()

    matches = []
    for row in rows:
        if personal_only and not row["belongs_to_user"]:
            continue
        if row["event_id"] == current_event_id:
            continue
        if exclude_game_id is not None and row["game_id"] == exclude_game_id:
            continue
        if row["event_id"] in suppressed:
            continue
        similarity = _cosine_similarity(
            query_vector,
            _blob_to_vector(row["embedding"]),
        )
        if similarity is None:
            continue
        match = dict(row)
        match.pop("embedding")
        match["belongs_to_user"] = bool(match["belongs_to_user"])
        match["is_important"] = bool(match["is_important"])
        match["similarity_score"] = similarity
        matches.append(match)
    matches.sort(key=lambda item: (-item["similarity_score"], item["event_id"]))
    return matches[:top_k]


def list_personal_embedded_events(limit=20):
    """Return recent personal event ids that already have usable embeddings."""
    if type(limit) is not int or limit <= 0:
        raise ValueError("limit must be a positive integer")
    with _connect() as connection:
        rows = connection.execute(
            """SELECT e.id AS event_id, e.game_id, e.player, e.phase,
                      e.worst_move_number, x.title, x.summary, m.updated_at
               FROM memory_error_events e
               JOIN memory_games g ON g.id=e.game_id
               JOIN memory_error_explanations x ON x.event_id=e.id
               JOIN memory_error_embeddings m ON m.event_id=e.id
               WHERE g.belongs_to_user=1 AND m.status='succeeded'
               ORDER BY m.updated_at DESC, e.id
               LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def set_error_importance(event_id, is_important):
    if type(is_important) is not bool:
        raise ValueError("is_important must be boolean")
    with _connect() as connection:
        cursor = connection.execute(
            """UPDATE memory_error_events
               SET is_important=?, updated_at=? WHERE id=?""",
            (int(is_important), _utc_now(), event_id),
        )
        if cursor.rowcount != 1:
            raise ValueError("Unknown error event")


def set_similarity_feedback(current_event_id, historical_event_id, feedback):
    """Store pair-specific retrieval feedback or clear it with None."""
    if feedback not in {"same_error", "not_same_error", None}:
        raise ValueError("Unsupported similarity feedback")
    with _connect() as connection:
        if feedback is None:
            connection.execute(
                """DELETE FROM memory_similarity_feedback
                   WHERE current_event_id=? AND historical_event_id=?""",
                (current_event_id, historical_event_id),
            )
            return
        connection.execute(
            """INSERT INTO memory_similarity_feedback (
                   current_event_id, historical_event_id, feedback, updated_at
               ) VALUES (?, ?, ?, ?)
               ON CONFLICT(current_event_id, historical_event_id) DO UPDATE SET
                   feedback=excluded.feedback, updated_at=excluded.updated_at""",
            (
                current_event_id,
                historical_event_id,
                feedback,
                _utc_now(),
            ),
        )
