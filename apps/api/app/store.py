"""PostgreSQL store: client companies, their API keys and per-call usage.

Only metadata is kept here (never document content or extracted values). API keys are stored as
SHA-256 hashes: they are long random tokens, so a slow password hash would add latency for nothing.
Psycopg runs in sync mode because its async mode rejects the default event loop on Windows; callers
use FastAPI's threadpool or asyncio.to_thread.
"""
import hashlib
import logging
import secrets
from dataclasses import dataclass

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

log = logging.getLogger("kyc.store")

KEY_PREFIX = "kyc_live_"
SHOWN_PREFIX_LEN = len(KEY_PREFIX) + 6

SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name text NOT NULL,
    contact_email text,
    active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS api_keys (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    client_id bigint NOT NULL REFERENCES clients(id),
    name text NOT NULL,
    prefix text NOT NULL,
    key_hash text NOT NULL UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now(),
    last_used_at timestamptz,
    revoked_at timestamptz
);
CREATE INDEX IF NOT EXISTS api_keys_client_idx ON api_keys (client_id);
CREATE TABLE IF NOT EXISTS usage_events (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    client_id bigint NOT NULL REFERENCES clients(id),
    key_id bigint NOT NULL REFERENCES api_keys(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    http_status integer NOT NULL,
    document_type text,
    llm_used boolean NOT NULL DEFAULT false,
    llm_cost_usd numeric(12, 6),
    duration_ms integer NOT NULL
);
CREATE INDEX IF NOT EXISTS usage_client_date_idx ON usage_events (client_id, created_at);
"""

CLIENT_COLUMNS = "id, name, contact_email, active, created_at"
KEY_COLUMNS = "id, client_id, name, prefix, created_at, last_used_at, revoked_at"


@dataclass(frozen=True)
class Caller:
    client_id: int
    key_id: int


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


class Store:
    def __init__(self, url: str):
        self.pool = ConnectionPool(url, min_size=1, max_size=5, timeout=5, open=False,
                                   kwargs={"autocommit": True, "row_factory": dict_row})
        self._migrated = False

    def open(self) -> None:
        """Never raises: the API must still start (and serve API_KEYS callers) while the database is down."""
        self.pool.open()
        try:
            self._migrate()
        except Exception as e:
            log.error("Database not ready at startup: %s", e)

    def close(self) -> None:
        self.pool.close()

    def _migrate(self) -> None:
        with self.pool.connection() as conn, conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(7240115)")  # several workers may start together
            conn.execute(SCHEMA)
        self._migrated = True

    def _all(self, sql: str, params: tuple = ()) -> list[dict]:
        if not self._migrated:
            self._migrate()
        with self.pool.connection() as conn:
            return conn.execute(sql, params).fetchall()

    def _one(self, sql: str, params: tuple = ()) -> dict | None:
        rows = self._all(sql, params)
        return rows[0] if rows else None

    # Authentication
    def find_key(self, key: str) -> Caller | None:
        row = self._one(
            """SELECT k.id, k.client_id FROM api_keys k JOIN clients c ON c.id = k.client_id
               WHERE k.key_hash = %s AND k.revoked_at IS NULL AND c.active""",
            (hash_key(key),),
        )
        return Caller(row["client_id"], row["id"]) if row else None

    def record_usage(self, caller: Caller, http_status: int, duration_ms: int, document_type: str | None,
                     llm_used: bool, llm_cost_usd: float | None) -> None:
        """Best effort: a metering failure must not fail a call that already succeeded."""
        try:
            self._all(
                """WITH touched AS (UPDATE api_keys SET last_used_at = now() WHERE id = %s)
                   INSERT INTO usage_events (client_id, key_id, http_status, duration_ms, document_type, llm_used, llm_cost_usd)
                   VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (caller.key_id, caller.client_id, caller.key_id, http_status, duration_ms, document_type, llm_used, llm_cost_usd),
            )
        except Exception as e:
            log.error("Usage not recorded for client %s: %s", caller.client_id, e)

    # Administration
    def list_clients(self) -> list[dict]:
        return self._all(
            """SELECT c.id, c.name, c.contact_email, c.active, c.created_at,
                      (SELECT count(*) FROM api_keys k WHERE k.client_id = c.id AND k.revoked_at IS NULL) AS active_keys,
                      u.calls, u.successes, u.llm_cost_usd
               FROM clients c
               LEFT JOIN LATERAL (
                   SELECT count(*) AS calls, count(*) FILTER (WHERE e.http_status = 200) AS successes,
                          coalesce(sum(e.llm_cost_usd), 0)::float8 AS llm_cost_usd
                   FROM usage_events e
                   WHERE e.client_id = c.id AND e.created_at >= date_trunc('month', now())
               ) u ON true
               ORDER BY c.name"""
        )

    def create_client(self, name: str, contact_email: str | None) -> dict:
        return self._one(f"INSERT INTO clients (name, contact_email) VALUES (%s, %s) RETURNING {CLIENT_COLUMNS}",
                         (name, contact_email))

    def update_client(self, client_id: int, changes: dict) -> dict | None:
        allowed = [c for c in ("name", "contact_email", "active") if c in changes]
        if not allowed:
            return self._one(f"SELECT {CLIENT_COLUMNS} FROM clients WHERE id = %s", (client_id,))
        assignments = ", ".join(f"{c} = %s" for c in allowed)  # column names come from the fixed list above
        return self._one(f"UPDATE clients SET {assignments} WHERE id = %s RETURNING {CLIENT_COLUMNS}",
                         (*[changes[c] for c in allowed], client_id))

    def list_keys(self, client_id: int) -> list[dict]:
        return self._all(f"SELECT {KEY_COLUMNS} FROM api_keys WHERE client_id = %s ORDER BY created_at DESC", (client_id,))

    def create_key(self, client_id: int, name: str) -> dict | None:
        """Returns the key row plus `key`, the only time the plain value exists."""
        if not self._one("SELECT id FROM clients WHERE id = %s", (client_id,)):
            return None
        key = KEY_PREFIX + secrets.token_urlsafe(32)
        row = self._one(
            f"INSERT INTO api_keys (client_id, name, prefix, key_hash) VALUES (%s, %s, %s, %s) RETURNING {KEY_COLUMNS}",
            (client_id, name, key[:SHOWN_PREFIX_LEN], hash_key(key)),
        )
        return {**row, "key": key}

    def revoke_key(self, key_id: int) -> dict | None:
        return self._one(
            f"UPDATE api_keys SET revoked_at = coalesce(revoked_at, now()) WHERE id = %s RETURNING {KEY_COLUMNS}", (key_id,)
        )

    def monthly_usage(self, client_id: int) -> list[dict]:
        return self._all(
            """SELECT to_char(date_trunc('month', created_at), 'YYYY-MM') AS month,
                      count(*) AS calls, count(*) FILTER (WHERE http_status = 200) AS successes,
                      count(*) FILTER (WHERE llm_used) AS llm_calls,
                      coalesce(sum(llm_cost_usd), 0)::float8 AS llm_cost_usd
               FROM usage_events
               WHERE client_id = %s AND created_at >= date_trunc('month', now()) - interval '11 months'
               GROUP BY 1 ORDER BY 1 DESC""",
            (client_id,),
        )
