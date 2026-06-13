 bạn """
Traffic logging module — ghi lại tất cả HTTP request để theo dõi traffic và IP đáng ngờ.
Dữ liệu lưu vào SQLite table `traffic_logs`.
"""
import sqlite3
import time
from datetime import datetime, timedelta
from typing import Optional
from app.database.connection import DATABASE_PATH, get_db_connection


def init_traffic_table():
    """Tạo bảng traffic_logs nếu chưa tồn tại."""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS traffic_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ip TEXT NOT NULL,
            method TEXT NOT NULL,
            path TEXT NOT NULL,
            status_code INTEGER DEFAULT 0,
            user_agent TEXT,
            referer TEXT,
            country TEXT,
            response_time_ms REAL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_traffic_ip ON traffic_logs(ip)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_traffic_path ON traffic_logs(path)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_traffic_created ON traffic_logs(created_at DESC)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_traffic_status ON traffic_logs(status_code)")
    conn.commit()
    conn.close()


def log_traffic(
    ip: str,
    method: str,
    path: str,
    status_code: int = 0,
    user_agent: str = "",
    referer: str = "",
    response_time_ms: float = 0,
):
    """Ghi một record traffic vào DB."""
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO traffic_logs (ip, method, path, status_code, user_agent, referer, response_time_ms)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (ip, method, path, status_code, user_agent or "", referer or "", response_time_ms),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass  # Không để lỗi log ảnh hưởng request


def get_traffic_summary(hours: int = 24) -> dict:
    """Trả về tổng quan traffic trong khoảng N giờ gần nhất."""
    since = (datetime.utcnow() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
    with get_db_connection() as conn:
        cur = conn.cursor()

        # Tổng request
        cur.execute("SELECT COUNT(*) FROM traffic_logs WHERE created_at >= ?", (since,))
        total = cur.fetchone()[0]

        # Unique IPs
        cur.execute("SELECT COUNT(DISTINCT ip) FROM traffic_logs WHERE created_at >= ?", (since,))
        unique_ips = cur.fetchone()[0]

        # Suspicious: truy cập .git, .env, wp-admin, ...
        cur.execute(
            """SELECT COUNT(*) FROM traffic_logs
               WHERE created_at >= ? AND (
                   path LIKE '%.git%' OR path LIKE '%.env%' OR path LIKE '%wp-%'
                   OR path LIKE '%admin%php%' OR path LIKE '%/config%'
                   OR path LIKE '%.sql%' OR path LIKE '%/shell%'
                   OR path LIKE '%/passwd%' OR path LIKE '%/etc/%'
                   OR path LIKE '%phpmyadmin%' OR path LIKE '%/cgi-bin%'
               )""",
            (since,),
        )
        suspicious = cur.fetchone()[0]

        # Error requests (4xx, 5xx)
        cur.execute(
            "SELECT COUNT(*) FROM traffic_logs WHERE created_at >= ? AND status_code >= 400",
            (since,),
        )
        errors = cur.fetchone()[0]

        return {
            "total_requests": total,
            "unique_ips": unique_ips,
            "suspicious_requests": suspicious,
            "error_requests": errors,
            "period_hours": hours,
        }


def get_traffic_by_ip(hours: int = 24, limit: int = 50) -> list:
    """Top IP theo số lượng request."""
    since = (datetime.utcnow() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT ip, COUNT(*) as cnt,
                      MIN(created_at) as first_seen,
                      MAX(created_at) as last_seen,
                      GROUP_CONCAT(DISTINCT method) as methods
               FROM traffic_logs
               WHERE created_at >= ?
               GROUP BY ip
               ORDER BY cnt DESC
               LIMIT ?""",
            (since, limit),
        )
        rows = cur.fetchall()
        return [
            {
                "ip": r[0],
                "count": r[1],
                "first_seen": r[2],
                "last_seen": r[3],
                "methods": r[4],
            }
            for r in rows
        ]


def get_traffic_by_path(hours: int = 24, limit: int = 50) -> list:
    """Top path được truy cập nhiều nhất."""
    since = (datetime.utcnow() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT path, method, COUNT(*) as cnt,
                      COUNT(DISTINCT ip) as unique_ips,
                      GROUP_CONCAT(DISTINCT status_code) as statuses
               FROM traffic_logs
               WHERE created_at >= ?
               GROUP BY path, method
               ORDER BY cnt DESC
               LIMIT ?""",
            (since, limit),
        )
        rows = cur.fetchall()
        return [
            {
                "path": r[0],
                "method": r[1],
                "count": r[2],
                "unique_ips": r[3],
                "statuses": r[4],
            }
            for r in rows
        ]


def get_suspicious_requests(hours: int = 24, limit: int = 100) -> list:
    """Lấy danh sách request đáng ngờ: quét .git, .env, wp-admin, SQL injection, ..."""
    since = (datetime.utcnow() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT ip, method, path, status_code, user_agent, created_at
               FROM traffic_logs
               WHERE created_at >= ? AND (
                   path LIKE '%.git%' OR path LIKE '%.env%' OR path LIKE '%wp-%'
                   OR path LIKE '%admin%php%' OR path LIKE '%/config%'
                   OR path LIKE '%.sql%' OR path LIKE '%/shell%'
                   OR path LIKE '%/passwd%' OR path LIKE '%/etc/%'
                   OR path LIKE '%phpmyadmin%' OR path LIKE '%/cgi-bin%'
                   OR status_code = 404
               )
               ORDER BY created_at DESC
               LIMIT ?""",
            (since, limit),
        )
        rows = cur.fetchall()
        return [
            {
                "ip": r[0],
                "method": r[1],
                "path": r[2],
                "status_code": r[3],
                "user_agent": r[4],
                "created_at": r[5],
            }
            for r in rows
        ]


def get_recent_logs(limit: int = 100) -> list:
    """Lấy N log gần nhất."""
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT ip, method, path, status_code, user_agent, referer, response_time_ms, created_at
               FROM traffic_logs
               ORDER BY created_at DESC
               LIMIT ?""",
            (limit,),
        )
        rows = cur.fetchall()
        return [
            {
                "ip": r[0],
                "method": r[1],
                "path": r[2],
                "status_code": r[3],
                "user_agent": r[4],
                "referer": r[5],
                "response_time_ms": r[6],
                "created_at": r[7],
            }
            for r in rows
        ]


def get_hourly_chart(hours: int = 24) -> list:
    """Lấy thống kê traffic theo từng giờ cho biểu đồ."""
    since = (datetime.utcnow() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT strftime('%Y-%m-%d %H:00', created_at) as hour,
                      COUNT(*) as cnt,
                      COUNT(DISTINCT ip) as unique_ips
               FROM traffic_logs
               WHERE created_at >= ?
               GROUP BY hour
               ORDER BY hour ASC""",
            (since,),
        )
        rows = cur.fetchall()
        return [{"hour": r[0], "requests": r[1], "unique_ips": r[2]} for r in rows]
