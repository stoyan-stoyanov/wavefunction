"""Read-only model statistics from recorded coding usage, without loading transcripts."""

import sqlite3
from datetime import datetime, timedelta


def _number(document: str, path: str) -> str:
    value = f"json_extract({document}, '{path}')"
    return (
        f"CASE WHEN json_type({document}, '{path}') IN ('integer', 'real') "
        f"AND {value} >= 0 AND {value} < 1e308 THEN {value} END"
    )


METRICS = (
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cached_input_tokens",
    "uncached_input_tokens",
    "cost_usd",
    "duration_ms",
    "average_duration_ms",
)
COUNTS = ("runs", "completed_runs", "failed_runs", "cancelled_runs", "active_runs", "user_requests")


def run_analytics(connection: sqlite3.Connection, agent_id: str, now: str) -> dict:
    """Include helpers, not copied fork history. Daily buckets use UTC run-start dates.

    Run totals are reported subtotals: providers may omit individual requests or fields.
    Exact uncached input needs complete per-call cache coverage, as in the run usage popover.
    Only the two indexed model-event types are inspected, and only their usage is extracted.
    """
    today = datetime.fromisoformat(now).date()
    dates = [(today - timedelta(days=offset)).isoformat() for offset in range(13, -1, -1)]
    usage_columns = {
        "input_tokens": "$.input_tokens",
        "output_tokens": "$.output_tokens",
        "recorded_total": "$.total_tokens",
        "recorded_cache": "$.input_token_details.cache_read",
        "cost_usd": "$.cost",
    }
    columns = ", ".join(f"{_number('usage', path)} AS {name}" for name, path in usage_columns.items())
    aggregates = """
        COUNT(*) AS runs,
        COUNT(CASE WHEN status = 'completed' THEN 1 END) AS completed_runs,
        COUNT(CASE WHEN status = 'failed' THEN 1 END) AS failed_runs,
        COUNT(CASE WHEN status = 'cancelled' THEN 1 END) AS cancelled_runs,
        COUNT(CASE WHEN status IN ('queued','running','waiting_approval','waiting_connector','waiting_computer') THEN 1 END) AS active_runs,
        COUNT(CASE WHEN kind = 'user' THEN 1 END) AS user_requests,
        strftime('%Y-%m-%dT%H:%M:%fZ', MAX(active_at)) AS last_active_at
    """
    for metric in METRICS:
        column = "duration_ms" if metric == "average_duration_ms" else metric
        operation = "AVG" if metric == "average_duration_ms" else "SUM"
        aggregates += (
            f", CASE WHEN COUNT(*) = 0 THEN 0 ELSE {operation}({column}) END AS {metric}"
            f", COUNT({column}) AS {metric}_known_runs"
        )

    rows = connection.execute(
        f"""
        WITH source AS (
            SELECT id, status, kind, started_at, completed_at, duration_ms,
                CASE WHEN json_valid(spec_json) THEN spec_json ELSE '{{}}' END AS spec,
                CASE WHEN json_valid(usage_json) THEN usage_json ELSE '{{}}' END AS usage
            FROM runs WHERE project_id = ? AND kind != 'fork'
        ), recorded AS (
            SELECT id, status, kind, date(started_at) AS day,
                COALESCE(julianday(completed_at), julianday(started_at)) AS active_at,
                CASE WHEN typeof(duration_ms) IN ('integer','real') AND duration_ms >= 0
                    THEN duration_ms END AS duration_ms,
                CASE WHEN json_type(spec, '$.model.provider') = 'text'
                    THEN NULLIF(trim(json_extract(spec, '$.model.provider')), '') END AS provider,
                CASE WHEN json_type(spec, '$.model.model') = 'text'
                    THEN NULLIF(trim(json_extract(spec, '$.model.model')), '') END AS model,
                {columns}
            FROM source
        ), model_events AS (
            SELECT e.run_id, e.event_type,
                CASE WHEN json_valid(e.payload_json) THEN e.payload_json ELSE '{{}}' END AS payload
            FROM run_events AS e JOIN recorded AS r ON r.id = e.run_id
            WHERE e.event_type IN ('model_call_finished', 'summarization_finished', 'model_call_failed')
                AND (e.event_type != 'model_call_failed' OR json_type(e.payload_json, '$.usage') = 'array')
        ), call_entries AS (
            SELECT e.run_id, CASE WHEN entry.type = 'object' THEN entry.value ELSE '{{}}' END AS usage
            FROM model_events AS e LEFT JOIN json_each(CASE
                WHEN e.event_type IN ('model_call_finished', 'model_call_failed') AND json_type(e.payload, '$.usage') = 'array'
                    THEN json_extract(e.payload, '$.usage')
                WHEN e.event_type = 'summarization_finished' AND json_type(e.payload, '$.usage') = 'object'
                    THEN json_array(json_extract(e.payload, '$.usage'))
                ELSE '[null]' END) AS entry
        ), calls AS (
            SELECT run_id, {_number("usage", "$.input_tokens")} AS input,
                {_number("usage", "$.input_token_details.cache_read")} AS cached
            FROM call_entries
        ), coverage AS (
            SELECT run_id, SUM(input) AS input, SUM(cached) AS cached,
                COUNT(*) = COUNT(input) AND COUNT(*) = COUNT(cached)
                    AND MIN(cached <= input) AS complete
            FROM calls GROUP BY run_id
        ), checked AS (
            SELECT r.*, c.cached AS call_cache,
                c.complete AND c.input = r.input_tokens
                    AND (r.recorded_cache IS NULL OR r.recorded_cache = c.cached) AS covered
            FROM recorded AS r LEFT JOIN coverage AS c ON c.run_id = r.id
        ), metrics AS (
            SELECT *, COALESCE(recorded_total, input_tokens + output_tokens) AS total_tokens,
                COALESCE(CASE WHEN recorded_cache <= input_tokens OR input_tokens IS NULL
                    THEN recorded_cache END, CASE WHEN covered THEN call_cache END) AS cached_input_tokens,
                CASE WHEN covered THEN input_tokens - call_cache END AS uncached_input_tokens
            FROM checked
        )
        SELECT 'totals' AS bucket, NULL AS provider, NULL AS model, NULL AS date, {aggregates}
            FROM metrics
        UNION ALL
        SELECT 'model', provider, model, NULL, {aggregates}
            FROM metrics GROUP BY provider, model
        UNION ALL
        SELECT 'daily', NULL, NULL, day, {aggregates}
            FROM metrics WHERE day >= ? AND day <= ? GROUP BY day
    """,
        (agent_id, dates[0], dates[-1]),
    ).fetchall()

    def stats(row):
        return {
            **{name: row[name] for name in COUNTS},
            "last_active_at": row["last_active_at"],
            **{name: {"value": row[name], "known_runs": row[f"{name}_known_runs"]} for name in METRICS},
        }

    empty = {
        **dict.fromkeys(COUNTS, 0),
        "last_active_at": None,
        **{name: {"value": 0, "known_runs": 0} for name in METRICS},
    }
    totals = next(stats(row) for row in rows if row["bucket"] == "totals")
    models = [
        {"provider": row["provider"], "model": row["model"], **stats(row)}
        for row in rows
        if row["bucket"] == "model"
    ]
    daily = {row["date"]: stats(row) for row in rows if row["bucket"] == "daily"}
    return {
        "generated_at": now,
        "timezone": "UTC",
        "totals": totals,
        "models": sorted(models, key=lambda row: (-row["runs"], row["provider"] or "", row["model"] or "")),
        "daily": [{"date": date, **daily.get(date, empty)} for date in dates],
    }
