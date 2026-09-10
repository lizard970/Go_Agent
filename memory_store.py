import json
import math
import os
import sqlite3
from array import array
from datetime import datetime
from pathlib import Path


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
        """
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
