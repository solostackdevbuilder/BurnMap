"""SQL-backed data fetchers and public per-endpoint shape projections."""
import sqlite3
import time
from datetime import date, datetime

from paths import resolve_db_path
from pricing import PRICING
from schema import init_db

from .helpers import (
    _DASHBOARD_CACHE_TTL_SECONDS,
    _EST_COLUMNS,
    _EST_COLUMNS_T,
    _cache_key,
    _duration_minutes,
    backend_provider_label,
    build_turn_filters,
    cache_get,
    cache_set,
    cost_for_aggregate,
    cost_parts_for_aggregate,
    cost_parts_for_turn,
    get_backend_provider_sql,
    get_range_cutoff,
    get_source_sql,
    normalize_date_range,
    source_label,
)
from .rollups import (
    build_overview_summary,
    build_pi_summary,
    build_project_details,
    build_project_rollups,
    build_session_details,
    build_session_rollups,
)


def get_today_usage_data(db_path=None, day=None):
    db_path = db_path or resolve_db_path()
    if not db_path.exists():
        return {"error": "Database not found. Run: python main.py scan"}

    day = day or date.today().isoformat()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    init_db(conn)

    source_sql = get_source_sql("t", "s")
    rows = conn.execute(
        f"""
        SELECT
            {source_sql}                         as provider,
            COALESCE(t.model, 'unknown')         as model,
            SUM(t.input_tokens)                  as input,
            SUM(t.output_tokens)                 as output,
            SUM(t.cache_read_tokens)             as cache_read,
            SUM(t.cache_creation_tokens)         as cache_creation,
            SUM(t.native_cost)                   as native_cost,
            {_EST_COLUMNS_T},
            COUNT(*)                             as turns
        FROM turns t
        LEFT JOIN sessions s ON t.session_id = s.session_id
        WHERE substr(t.timestamp, 1, 10) = ?
        GROUP BY {source_sql}, COALESCE(t.model, 'unknown')
        ORDER BY input + output DESC
        """,
        (day,),
    ).fetchall()

    sessions = conn.execute(
        """
        SELECT COUNT(DISTINCT session_id) as count
        FROM turns
        WHERE substr(timestamp, 1, 10) = ?
        """,
        (day,),
    ).fetchone()
    conn.close()

    items = []
    totals = {
        "input": 0,
        "output": 0,
        "cache_read": 0,
        "cache_creation": 0,
        "turns": 0,
        "cost": 0.0,
        "sessions": sessions["count"] if sessions else 0,
    }
    for row in rows:
        parts = cost_parts_for_aggregate(
            row["model"], row["native_cost"],
            row["est_input"], row["est_output"],
            row["est_cache_read"], row["est_cache_creation"],
        )
        item = {
            "provider": row["provider"],
            "provider_label": source_label(row["provider"]),
            "model": row["model"],
            "input": row["input"] or 0,
            "output": row["output"] or 0,
            "cache_read": row["cache_read"] or 0,
            "cache_creation": row["cache_creation"] or 0,
            "native_cost": parts["native_cost"],
            "estimated_cost": parts["estimated_cost"],
            "turns": row["turns"] or 0,
            "cost": parts["cost"],
        }
        items.append(item)
        totals["input"] += item["input"]
        totals["output"] += item["output"]
        totals["cache_read"] += item["cache_read"]
        totals["cache_creation"] += item["cache_creation"]
        totals["turns"] += item["turns"]
        totals["cost"] += item["cost"]
        totals["native_cost"] = totals.get("native_cost", 0.0) + item["native_cost"]
        totals["estimated_cost"] = totals.get("estimated_cost", 0.0) + item["estimated_cost"]

    totals.setdefault("native_cost", 0.0)
    totals.setdefault("estimated_cost", 0.0)
    return {"day": day, "rows": items, "totals": totals}


def get_all_time_stats_data(db_path=None):
    db_path = db_path or resolve_db_path()
    if not db_path.exists():
        return {"error": "Database not found. Run: python main.py scan"}

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    init_db(conn)

    session_info = conn.execute(
        """
        SELECT
            COUNT(*)             as sessions,
            MIN(first_timestamp) as first,
            MAX(last_timestamp)  as last
        FROM sessions
        """
    ).fetchone()

    totals = conn.execute(
        """
        SELECT
            SUM(input_tokens)          as input,
            SUM(output_tokens)         as output,
            SUM(cache_read_tokens)     as cache_read,
            SUM(cache_creation_tokens) as cache_creation,
            COUNT(*)                   as turns
        FROM turns
        """
    ).fetchone()

    source_sql = get_source_sql("t", "s")
    backend_provider_sql = get_backend_provider_sql("t", "s", source_sql=source_sql)
    by_model_rows = conn.execute(
        f"""
        SELECT
            {source_sql}                  as provider,
            COALESCE(t.model, 'unknown')  as model,
            SUM(t.input_tokens)           as input,
            SUM(t.output_tokens)          as output,
            SUM(t.cache_read_tokens)      as cache_read,
            SUM(t.cache_creation_tokens)  as cache_creation,
            SUM(t.native_cost)            as native_cost,
            {_EST_COLUMNS_T},
            COUNT(*)                      as turns,
            COUNT(DISTINCT t.session_id)  as sessions
        FROM turns t
        LEFT JOIN sessions s ON t.session_id = s.session_id
        GROUP BY {source_sql}, COALESCE(t.model, 'unknown')
        ORDER BY input + output DESC
        """
    ).fetchall()
    conn.close()

    by_model = []
    total_cost = 0.0
    for row in by_model_rows:
        parts = cost_parts_for_aggregate(
            row["model"], row["native_cost"],
            row["est_input"], row["est_output"],
            row["est_cache_read"], row["est_cache_creation"],
        )
        total_cost += parts["cost"]
        by_model.append({
            "provider": row["provider"],
            "provider_label": source_label(row["provider"]),
            "model": row["model"],
            "input": row["input"] or 0,
            "output": row["output"] or 0,
            "cache_read": row["cache_read"] or 0,
            "cache_creation": row["cache_creation"] or 0,
            "native_cost": parts["native_cost"],
            "estimated_cost": parts["estimated_cost"],
            "turns": row["turns"] or 0,
            "sessions": row["sessions"] or 0,
            "cost": parts["cost"],
        })

    return {
        "period": {
            "first": (session_info["first"] or "")[:10],
            "last": (session_info["last"] or "")[:10],
        },
        "totals": {
            "sessions": session_info["sessions"] or 0,
            "turns": totals["turns"] or 0,
            "input": totals["input"] or 0,
            "output": totals["output"] or 0,
            "cache_read": totals["cache_read"] or 0,
            "cache_creation": totals["cache_creation"] or 0,
            "cost": total_cost,
            "native_cost": sum(item["native_cost"] for item in by_model),
            "estimated_cost": sum(item["estimated_cost"] for item in by_model),
        },
        "by_model": by_model,
    }


def get_dashboard_data(
    db_path=None,
    range_name=None,
    models=None,
    providers=None,
    from_date=None,
    to_date=None,
    include_session_details=True,
    include_project_details=True,
    include_turn_events=True,
    include_pi_message_nodes=True,
):
    db_path = db_path or resolve_db_path()
    if not db_path.exists():
        return {"error": "Database not found. Run: python main.py scan"}

    cutoff = get_range_cutoff(range_name)
    from_date, to_date = normalize_date_range(from_date, to_date)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    source_sql = get_source_sql("t", "s")
    backend_provider_sql = get_backend_provider_sql("t", "s", source_sql=source_sql)

    model_rows = conn.execute(
        """
        SELECT COALESCE(model, 'unknown') as model
        FROM turns
        GROUP BY model
        ORDER BY SUM(input_tokens + output_tokens) DESC
        """
    ).fetchall()
    all_models = [row["model"] for row in model_rows]

    provider_rows = conn.execute(
        f"""
        SELECT {source_sql} as provider
        FROM turns t
        LEFT JOIN sessions s ON t.session_id = s.session_id
        GROUP BY {source_sql}
        ORDER BY COUNT(*) DESC
        """
    ).fetchall()
    all_providers = [row["provider"] for row in provider_rows]

    backend_provider_rows = conn.execute(
        f"""
        SELECT {backend_provider_sql} as backend_provider
        FROM turns t
        LEFT JOIN sessions s ON t.session_id = s.session_id
        WHERE {backend_provider_sql} != ''
        GROUP BY {backend_provider_sql}
        ORDER BY COUNT(*) DESC
        """
    ).fetchall()
    all_backend_providers = [row["backend_provider"] for row in backend_provider_rows]

    where_sql, params = build_turn_filters(
        models=models,
        cutoff=cutoff,
        providers=providers,
        provider_sql=source_sql,
        from_date=from_date,
        to_date=to_date,
    )

    daily_rows = conn.execute(
        f"""
        SELECT
            substr(t.timestamp, 1, 10)   as day,
            COALESCE(t.model, 'unknown') as model,
            SUM(t.input_tokens)          as input,
            SUM(t.output_tokens)         as output,
            SUM(t.cache_read_tokens)     as cache_read,
            SUM(t.cache_creation_tokens) as cache_creation,
            SUM(t.native_cost)           as native_cost,
            {_EST_COLUMNS_T},
            SUM(t.tool_call_count)       as tool_calls,
            COUNT(*)                     as turns
        FROM turns t
        LEFT JOIN sessions s ON t.session_id = s.session_id
        {where_sql}
        GROUP BY substr(t.timestamp, 1, 10), COALESCE(t.model, 'unknown')
        ORDER BY substr(t.timestamp, 1, 10), COALESCE(t.model, 'unknown')
        """,
        params,
    ).fetchall()
    daily_by_model = [
        {
            "day": row["day"],
            "model": row["model"],
            "input": row["input"] or 0,
            "output": row["output"] or 0,
            "cache_read": row["cache_read"] or 0,
            "cache_creation": row["cache_creation"] or 0,
            "native_cost": row["native_cost"] or 0.0,
            "est_input": row["est_input"] or 0,
            "est_output": row["est_output"] or 0,
            "est_cache_read": row["est_cache_read"] or 0,
            "est_cache_creation": row["est_cache_creation"] or 0,
            "tool_calls": row["tool_calls"] or 0,
            "turns": row["turns"] or 0,
        }
        for row in daily_rows
    ]

    provider_breakdown_rows = conn.execute(
        f"""
        SELECT
            {source_sql}                           as provider,
            SUM(t.input_tokens)                    as input,
            SUM(t.output_tokens)                   as output,
            SUM(t.cache_read_tokens)               as cache_read,
            SUM(t.cache_creation_tokens)           as cache_creation,
            SUM(t.native_cost)                     as native_cost,
            SUM(t.tool_call_count)                 as tool_calls,
            COUNT(*)                               as turns,
            COUNT(DISTINCT t.session_id)           as sessions
        FROM turns t
        LEFT JOIN sessions s ON t.session_id = s.session_id
        {where_sql}
        GROUP BY {source_sql}
        ORDER BY SUM(t.input_tokens + t.output_tokens) DESC
        """,
        params,
    ).fetchall()
    provider_breakdown = [
        {
            "provider": row["provider"],
            "provider_label": source_label(row["provider"]),
            "input": row["input"] or 0,
            "output": row["output"] or 0,
            "cache_read": row["cache_read"] or 0,
            "cache_creation": row["cache_creation"] or 0,
            "native_cost": row["native_cost"] or 0.0,
            "estimated_cost": 0.0,
            "turns": row["turns"] or 0,
            "sessions": row["sessions"] or 0,
            "tool_calls": row["tool_calls"] or 0,
            "cost": row["native_cost"] or 0.0,
        }
        for row in provider_breakdown_rows
    ]

    session_rows = conn.execute(
        f"""
        SELECT
            t.session_id                             as session_id,
            {source_sql}                             as provider,
            COALESCE(s.project_name, 'unknown')      as project_name,
            MIN(s.first_timestamp)                   as first_timestamp,
            MAX(s.last_timestamp)                    as last_timestamp,
            substr(t.timestamp, 1, 10)               as day,
            COALESCE(t.model, 'unknown')             as model,
            SUM(t.input_tokens)                      as input,
            SUM(t.output_tokens)                     as output,
            SUM(t.cache_read_tokens)                 as cache_read,
            SUM(t.cache_creation_tokens)             as cache_creation,
            SUM(t.native_cost)                       as native_cost,
            {_EST_COLUMNS_T},
            SUM(t.tool_call_count)                   as tool_calls,
            GROUP_CONCAT(COALESCE(t.tool_names, '')) as tool_names,
            COUNT(*)                                 as turns
        FROM turns t
        LEFT JOIN sessions s ON t.session_id = s.session_id
        {where_sql}
        GROUP BY t.session_id, {source_sql}, substr(t.timestamp, 1, 10), COALESCE(t.model, 'unknown')
        ORDER BY MAX(s.last_timestamp) DESC, t.session_id, day
        """,
        params,
    ).fetchall()

    turn_rows = conn.execute(
        f"""
        SELECT
            t.session_id                            as session_id,
            COALESCE(s.project_name, 'unknown')     as project_name,
            {source_sql}                            as session_provider,
            {backend_provider_sql}                  as backend_provider,
            COALESCE(s.client, t.client, 'cli')     as client,
            {source_sql}                            as provider,
            COALESCE(t.model, 'unknown')            as model,
            t.timestamp                             as timestamp,
            t.input_tokens                          as input,
            t.output_tokens                         as output,
            t.cache_read_tokens                     as cache_read,
            t.cache_creation_tokens                 as cache_creation,
            t.native_cost                           as native_cost,
            t.tool_call_count                       as tool_calls,
            COALESCE(t.tool_names, '')              as tool_names,
            COALESCE(t.cwd, '')                     as cwd,
            COALESCE(t.message_id, '')              as message_id,
            COALESCE(t.parent_message_id, '')       as parent_message_id,
            COALESCE(s.first_timestamp, t.timestamp) as first_timestamp,
            COALESCE(s.last_timestamp, t.timestamp)  as last_timestamp,
            COALESCE(s.tree_nodes, 0)               as tree_nodes,
            COALESCE(s.tree_edges, 0)               as tree_edges,
            COALESCE(s.tree_max_depth, 0)           as tree_max_depth,
            COALESCE(s.tree_branch_points, 0)       as tree_branch_points,
            COALESCE(s.tree_leaf_count, 0)          as tree_leaf_count,
            COALESCE(s.tree_root_count, 0)          as tree_root_count
        FROM turns t
        LEFT JOIN sessions s ON t.session_id = s.session_id
        {where_sql}
        ORDER BY t.session_id, t.timestamp
        """,
        params,
    ).fetchall()

    session_models_daily = []
    turn_events = []
    pi_message_nodes = []
    tool_usage = {}
    session_event_map = {}

    for row in session_rows:
        raw_session_id = row["session_id"] or ""
        session_models_daily.append(
            {
                "session_id": raw_session_id.split(":", 1)[-1][:8],
                "full_session_id": raw_session_id,
                "provider": row["provider"] or "claude",
                "project": row["project_name"] or "unknown",
                "provider_label": source_label(row["provider"] or "claude"),
                "last": (row["last_timestamp"] or "")[:16].replace("T", " "),
                "last_date": (row["last_timestamp"] or "")[:10],
                "duration_min": _duration_minutes(row["first_timestamp"], row["last_timestamp"]),
                "day": row["day"] or "",
                "model": row["model"] or "unknown",
                "turns": row["turns"] or 0,
                "input": row["input"] or 0,
                "output": row["output"] or 0,
                "cache_read": row["cache_read"] or 0,
                "cache_creation": row["cache_creation"] or 0,
                "native_cost": row["native_cost"] or 0.0,
                "est_input": row["est_input"] or 0,
                "est_output": row["est_output"] or 0,
                "est_cache_read": row["est_cache_read"] or 0,
                "est_cache_creation": row["est_cache_creation"] or 0,
                "tool_calls": row["tool_calls"] or 0,
                "tool_names": row["tool_names"] or "",
            }
        )

    for row in turn_rows:
        raw_session_id = row["session_id"] or ""
        parts = cost_parts_for_turn(
            row["model"], row["input"], row["output"], row["cache_read"], row["cache_creation"], row["native_cost"]
        )
        event = {
            "session_id": raw_session_id.split(":", 1)[-1][:8],
            "full_session_id": raw_session_id,
            "project": row["project_name"] or "unknown",
            "session_provider": row["session_provider"] or "claude",
            "backend_provider": row["backend_provider"] or "",
            "backend_provider_label": backend_provider_label(row["backend_provider"] or ""),
            "client": row["client"] or "cli",
            "provider": row["provider"] or "claude",
            "provider_label": source_label(row["provider"] or "claude"),
            "model": row["model"] or "unknown",
            "timestamp": row["timestamp"] or "",
            "input": row["input"] or 0,
            "output": row["output"] or 0,
            "cache_read": row["cache_read"] or 0,
            "cache_creation": row["cache_creation"] or 0,
            "native_cost": parts["native_cost"],
            "estimated_cost": parts["estimated_cost"],
            "cost": parts["cost"],
            "tool_calls": row["tool_calls"] or 0,
            "tool_names": row["tool_names"] or "",
            "cwd": row["cwd"] or "",
            "message_id": row["message_id"] or "",
            "parent_message_id": row["parent_message_id"] or "",
            "first_timestamp": row["first_timestamp"] or "",
            "last_timestamp": row["last_timestamp"] or "",
            "tree_nodes": row["tree_nodes"] or 0,
            "tree_edges": row["tree_edges"] or 0,
            "tree_max_depth": row["tree_max_depth"] or 0,
            "tree_branch_points": row["tree_branch_points"] or 0,
            "tree_leaf_count": row["tree_leaf_count"] or 0,
            "tree_root_count": row["tree_root_count"] or 0,
        }
        if include_turn_events or include_session_details:
            turn_events.append(event)
        session_event_map.setdefault(raw_session_id, []).append(event)
        for name in [name.strip() for name in (row["tool_names"] or "").split(",") if name.strip()]:
            tool_usage[name] = tool_usage.get(name, 0) + 1

    session_analytics = []
    for raw_session_id, events in session_event_map.items():
        models_seen = []
        providers_seen = []
        backend_providers_seen = []
        model_switches = 0
        provider_switches = 0
        backend_switches = 0
        last_model = None
        last_provider = None
        last_backend_provider = None
        tool_calls = 0
        for event in events:
            model = event["model"] or "unknown"
            provider = event["provider"] or "claude"
            backend_provider = event.get("backend_provider") or ""
            if model not in models_seen:
                models_seen.append(model)
            if provider not in providers_seen:
                providers_seen.append(provider)
            if backend_provider and backend_provider not in backend_providers_seen:
                backend_providers_seen.append(backend_provider)
            if last_model is not None and model != last_model:
                model_switches += 1
            if last_provider is not None and provider != last_provider:
                provider_switches += 1
            if last_backend_provider is not None and backend_provider != last_backend_provider:
                backend_switches += 1
            last_model = model
            last_provider = provider
            last_backend_provider = backend_provider
            tool_calls += event["tool_calls"] or 0
        first_timestamp = events[0].get("first_timestamp") or events[0].get("timestamp") or ""
        last_timestamp = events[-1].get("last_timestamp") or events[-1].get("timestamp") or ""
        session_analytics.append(
            {
                "session_id": raw_session_id.split(":", 1)[-1][:8],
                "full_session_id": raw_session_id,
                "project": events[0].get("project") or "unknown",
                "session_provider": events[0].get("session_provider") or "claude",
                "client": events[0].get("client") or "cli",
                "models": models_seen,
                "providers": providers_seen,
                "backend_providers": backend_providers_seen,
                "model_switches": model_switches,
                "provider_switches": provider_switches,
                "backend_switches": backend_switches,
                "total_switches": model_switches + provider_switches,
                "turns": len(events),
                "tool_calls": tool_calls,
                "duration_min": _duration_minutes(first_timestamp, last_timestamp),
                "first_timestamp": first_timestamp,
                "last_timestamp": last_timestamp,
                "tree_nodes": events[0].get("tree_nodes", 0) or 0,
                "tree_edges": events[0].get("tree_edges", 0) or 0,
                "tree_max_depth": events[0].get("tree_max_depth", 0) or 0,
                "tree_branch_points": events[0].get("tree_branch_points", 0) or 0,
                "tree_leaf_count": events[0].get("tree_leaf_count", 0) or 0,
                "tree_root_count": events[0].get("tree_root_count", 0) or 0,
            }
        )
    session_analytics.sort(key=lambda row: (row["last_timestamp"], row["full_session_id"]), reverse=True)

    filtered_session_ids = [row["full_session_id"] for row in session_analytics]
    if (include_pi_message_nodes or include_session_details) and filtered_session_ids:
        placeholders = ",".join("?" for _ in filtered_session_ids)
        pi_rows = conn.execute(
            f"""
            SELECT
                session_id,
                message_id,
                COALESCE(parent_message_id, '') as parent_message_id,
                COALESCE(role, '') as role,
                COALESCE(timestamp, '') as timestamp,
                COALESCE(provider, '') as provider,
                COALESCE(model, '') as model,
                COALESCE(tool_names, '') as tool_names,
                COALESCE(text_preview, '') as text_preview,
                COALESCE(depth, 0) as depth,
                COALESCE(child_count, 0) as child_count
            FROM pi_messages
            WHERE session_id IN ({placeholders})
            ORDER BY session_id, depth, timestamp, message_id
            """,
            filtered_session_ids,
        ).fetchall()
        pi_message_nodes = [dict(row) for row in pi_rows]

    session_rollups = build_session_rollups(session_models_daily)

    provider_cost_map = {}
    for session in session_rollups:
        provider = session.get("provider") or "unknown"
        bucket = provider_cost_map.setdefault(provider, {"native_cost": 0.0, "estimated_cost": 0.0, "cost": 0.0})
        bucket["native_cost"] += session.get("native_cost", 0.0) or 0.0
        bucket["estimated_cost"] += session.get("estimated_cost", 0.0) or 0.0
        bucket["cost"] += session.get("cost", 0.0) or 0.0
    for item in provider_breakdown:
        cost_parts = provider_cost_map.get(item.get("provider") or "unknown", {})
        item["native_cost"] = cost_parts.get("native_cost", item.get("native_cost", 0.0) or 0.0)
        item["estimated_cost"] = cost_parts.get("estimated_cost", 0.0)
        item["cost"] = cost_parts.get("cost", item.get("cost", 0.0) or 0.0)

    session_details = build_session_details(session_rollups, turn_events, session_analytics, pi_message_nodes) if include_session_details else {}
    project_rollups = build_project_rollups(session_rollups)
    project_details = build_project_details(session_rollups, session_models_daily, session_analytics) if include_project_details else {}
    overview_summary = build_overview_summary(daily_by_model, provider_breakdown, session_rollups)
    pi_summary = build_pi_summary(session_analytics)

    conn.close()
    return {
        "all_models": all_models,
        "all_providers": all_providers,
        "all_backend_providers": all_backend_providers,
        "daily_by_model": daily_by_model,
        "provider_breakdown": provider_breakdown,
        "session_models_daily": session_models_daily,
        "session_rollups": session_rollups,
        "session_details": session_details,
        "project_rollups": project_rollups,
        "project_details": project_details,
        "overview_summary": overview_summary,
        "pi_summary": pi_summary,
        "turn_events": turn_events if include_turn_events else [],
        "pi_message_nodes": pi_message_nodes if include_pi_message_nodes else [],
        "tool_usage": [{"tool": key, "count": value} for key, value in sorted(tool_usage.items(), key=lambda item: (-item[1], item[0]))],
        "session_analytics": session_analytics,
        "pricing": PRICING,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "selected_range": range_name or "all",
        "selected_models": models or [],
        "selected_providers": providers or [],
        "selected_from": from_date,
        "selected_to": to_date,
    }


def _base_api_payload(db_path=None, range_name=None, models=None, providers=None, from_date=None, to_date=None):
    db_path = db_path or resolve_db_path()
    from_date, to_date = normalize_date_range(from_date, to_date)
    key = _cache_key(db_path, range_name=range_name, models=models, providers=providers, from_date=from_date, to_date=to_date)
    db_mtime = db_path.stat().st_mtime if db_path.exists() else None
    now = time.time()

    cached = cache_get(key)
    if cached:
        if cached["db_mtime"] == db_mtime and (now - cached["created_at"]) <= _DASHBOARD_CACHE_TTL_SECONDS:
            return cached["data"]

    data = get_dashboard_data(db_path=db_path, range_name=range_name, models=models, providers=providers, from_date=from_date, to_date=to_date)
    cache_set(key, {
        "created_at": now,
        "db_mtime": db_mtime,
        "data": data,
    })
    return data


def get_overview_data(db_path=None, range_name=None, models=None, providers=None, from_date=None, to_date=None):
    data = _base_api_payload(db_path=db_path, range_name=range_name, models=models, providers=providers, from_date=from_date, to_date=to_date)
    if data.get("error"):
        return data
    return {
        "generated_at": data["generated_at"],
        "selected_range": data["selected_range"],
        "selected_models": data["selected_models"],
        "selected_providers": data["selected_providers"],
        "selected_from": data["selected_from"],
        "selected_to": data["selected_to"],
        "all_models": data["all_models"],
        "all_providers": data["all_providers"],
        "all_backend_providers": data["all_backend_providers"],
        "daily_by_model": data["daily_by_model"],
        "provider_breakdown": data["provider_breakdown"],
        "overview_summary": data["overview_summary"],
        "pricing": data["pricing"],
    }


def get_sessions_data(db_path=None, range_name=None, models=None, providers=None, from_date=None, to_date=None):
    data = _base_api_payload(db_path=db_path, range_name=range_name, models=models, providers=providers, from_date=from_date, to_date=to_date)
    if data.get("error"):
        return data
    return {
        "generated_at": data["generated_at"],
        "selected_range": data["selected_range"],
        "selected_models": data["selected_models"],
        "selected_providers": data["selected_providers"],
        "selected_from": data["selected_from"],
        "selected_to": data["selected_to"],
        "all_backend_providers": data["all_backend_providers"],
        "session_models_daily": data["session_models_daily"],
        "session_rollups": data["session_rollups"],
        "session_details": data["session_details"],
        "turn_events": data["turn_events"],
        "session_analytics": data["session_analytics"],
        "tool_usage": data["tool_usage"],
        "pricing": data["pricing"],
    }


def get_projects_data(db_path=None, range_name=None, models=None, providers=None, from_date=None, to_date=None):
    data = _base_api_payload(db_path=db_path, range_name=range_name, models=models, providers=providers, from_date=from_date, to_date=to_date)
    if data.get("error"):
        return data
    return {
        "generated_at": data["generated_at"],
        "selected_range": data["selected_range"],
        "selected_models": data["selected_models"],
        "selected_providers": data["selected_providers"],
        "selected_from": data["selected_from"],
        "selected_to": data["selected_to"],
        "all_backend_providers": data["all_backend_providers"],
        "session_models_daily": data["session_models_daily"],
        "session_rollups": data["session_rollups"],
        "project_rollups": data["project_rollups"],
        "project_details": data["project_details"],
        "session_analytics": data["session_analytics"],
        "pricing": data["pricing"],
    }


def get_models_data(db_path=None, range_name=None, models=None, providers=None, from_date=None, to_date=None):
    data = _base_api_payload(db_path=db_path, range_name=range_name, models=models, providers=providers, from_date=from_date, to_date=to_date)
    if data.get("error"):
        return data
    return {
        "generated_at": data["generated_at"],
        "selected_range": data["selected_range"],
        "selected_models": data["selected_models"],
        "selected_providers": data["selected_providers"],
        "selected_from": data["selected_from"],
        "selected_to": data["selected_to"],
        "all_backend_providers": data["all_backend_providers"],
        "daily_by_model": data["daily_by_model"],
        "overview_summary": data["overview_summary"],
        "pricing": data["pricing"],
    }


def get_pi_data(db_path=None, range_name=None, models=None, providers=None, from_date=None, to_date=None):
    data = _base_api_payload(db_path=db_path, range_name=range_name, models=models, providers=providers, from_date=from_date, to_date=to_date)
    if data.get("error"):
        return data
    return {
        "generated_at": data["generated_at"],
        "selected_range": data["selected_range"],
        "selected_models": data["selected_models"],
        "selected_providers": data["selected_providers"],
        "selected_from": data["selected_from"],
        "selected_to": data["selected_to"],
        "all_backend_providers": data["all_backend_providers"],
        "session_analytics": data["pi_summary"]["sessions"],
        "session_details": {
            key: value for key, value in data["session_details"].items()
            if key.startswith("pi:") or (value.get("analytics") or {}).get("tree_nodes", 0) > 0 or ((value.get("analytics") or {}).get("session_provider") == "pi")
        },
        "pi_summary": data["pi_summary"],
        "turn_events": [
            row for row in data["turn_events"]
            if row.get("client") == "pi-agent" or row.get("session_provider") == "pi" or row.get("tree_nodes", 0) > 0 or (row.get("full_session_id", "").startswith("pi:"))
        ],
        "pi_message_nodes": data["pi_message_nodes"],
        "tool_usage": data["tool_usage"],
        "pricing": data["pricing"],
    }


def get_light_dashboard_data(db_path=None, range_name=None, models=None, providers=None, from_date=None, to_date=None):
    return get_dashboard_data(
        db_path=db_path,
        range_name=range_name,
        models=models,
        providers=providers,
        from_date=from_date,
        to_date=to_date,
        include_session_details=False,
        include_project_details=False,
        include_turn_events=False,
        include_pi_message_nodes=False,
    )


def get_session_detail_data(db_path=None, range_name=None, models=None, providers=None, from_date=None, to_date=None, session_id=None):
    data = get_dashboard_data(db_path=db_path, range_name=range_name, models=models, providers=providers, from_date=from_date, to_date=to_date, include_project_details=False)
    if data.get("error"):
        return data
    return {
        "generated_at": data["generated_at"],
        "session_detail": data["session_details"].get(session_id),
    }


def get_project_detail_data(db_path=None, range_name=None, models=None, providers=None, from_date=None, to_date=None, project_name=None):
    data = get_dashboard_data(db_path=db_path, range_name=range_name, models=models, providers=providers, from_date=from_date, to_date=to_date, include_session_details=False, include_turn_events=False, include_pi_message_nodes=False)
    if data.get("error"):
        return data
    return {
        "generated_at": data["generated_at"],
        "project_detail": data["project_details"].get(project_name),
    }
