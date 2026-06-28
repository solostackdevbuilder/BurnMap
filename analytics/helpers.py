"""Shared primitives for analytics: cache, filter builder, cost math, SQL helpers."""
import threading
from datetime import date, datetime, timedelta

from pricing import calc_cost


def _coalesce_sql(*expressions):
    non_empty = [f"NULLIF({expr}, '')" for expr in expressions if expr]
    if not non_empty:
        return "''"
    return f"COALESCE({', '.join(non_empty)}, '')"

# ThreadingHTTPServer dispatches concurrent requests, so the cache needs a lock.
# Rescans call clear() while other threads may be reading; CPython's GIL keeps
# the dict itself consistent but can't stop a reader from returning pre-clear
# data a few instructions after the clear happened. The lock closes that gap.
_DASHBOARD_CACHE = {}
_DASHBOARD_CACHE_TTL_SECONDS = 2.0
_DASHBOARD_CACHE_LOCK = threading.Lock()


def _cache_key(db_path, range_name=None, models=None, providers=None, from_date=None, to_date=None):
    return (
        str(db_path),
        range_name or "all",
        tuple(models or []),
        tuple(providers or []),
        from_date or "",
        to_date or "",
    )


def clear_dashboard_cache():
    with _DASHBOARD_CACHE_LOCK:
        _DASHBOARD_CACHE.clear()


def cache_get(key):
    with _DASHBOARD_CACHE_LOCK:
        return _DASHBOARD_CACHE.get(key)


def cache_set(key, value):
    with _DASHBOARD_CACHE_LOCK:
        _DASHBOARD_CACHE[key] = value


def get_range_cutoff(range_name):
    if range_name == "today":
        return date.today().isoformat()
    if range_name == "yesterday":
        return (date.today() - timedelta(days=1)).isoformat()
    if range_name == "7d":
        return (date.today() - timedelta(days=7)).isoformat()
    if range_name == "30d":
        return (date.today() - timedelta(days=30)).isoformat()
    if range_name == "90d":
        return (date.today() - timedelta(days=90)).isoformat()
    return None


def source_label(source):
    normalized = (source or "").strip().lower()
    if normalized == "claude":
        return "Claude Code"
    if normalized in ("codex", "openai-codex"):
        return "Codex"
    if normalized == "pi":
        return "Pi"
    if normalized == "opencode":
        return "OpenCode"
    if not normalized:
        return "Unknown"
    return source


def backend_provider_label(provider):
    normalized = (provider or "").strip().lower()
    if normalized == "anthropic":
        return "Anthropic"
    if normalized in ("openai", "openai-codex"):
        return "OpenAI"
    if normalized == "openrouter":
        return "OpenRouter"
    if not normalized:
        return "Unknown"
    return provider


def get_source_sql(turn_alias="t", session_alias="s"):
    """Return a SQL expression that normalizes a turn to its source app.

    BurnMap compares usage by the app/agent the user chose (Claude Code,
    Pi, Codex), not by the upstream model vendor embedded in some logs.
    The expression prefers durable session-level signals so older Pi rows
    already stored as provider='anthropic' still normalize back to 'pi'.
    """
    turn_session_id = f"COALESCE({turn_alias}.session_id, '')"
    turn_provider = f"COALESCE({turn_alias}.provider, '')"
    if session_alias:
        session_provider = f"COALESCE({session_alias}.provider, '')"
        session_client = f"COALESCE({session_alias}.client, '')"
    else:
        session_provider = "''"
        session_client = "''"
    return f"""
        CASE
            WHEN {turn_session_id} LIKE 'pi:%' OR {session_provider} = 'pi' OR {session_client} = 'pi-agent' THEN 'pi'
            WHEN {turn_session_id} LIKE 'codex:%' OR {session_provider} = 'codex' OR {session_client} = 'codex' OR {turn_provider} = 'codex' THEN 'codex'
            WHEN {turn_session_id} LIKE 'opencode:%' OR {session_provider} = 'opencode' OR {session_client} = 'opencode' OR {turn_provider} = 'opencode' THEN 'opencode'
            WHEN {turn_session_id} LIKE 'claude:%' OR {session_provider} = 'claude' OR {session_client} = 'claude-code' OR {turn_provider} = 'claude' THEN 'claude'
            ELSE COALESCE(NULLIF({session_provider}, ''), NULLIF({turn_provider}, ''), 'unknown')
        END
    """.strip()


def get_backend_provider_sql(turn_alias="t", session_alias="s", source_sql=None):
    turn_backend = f"COALESCE({turn_alias}.backend_provider, '')"
    session_backend = f"COALESCE({session_alias}.backend_provider, '')" if session_alias else "''"
    turn_provider = f"COALESCE({turn_alias}.provider, '')"
    session_provider = f"COALESCE({session_alias}.provider, '')" if session_alias else "''"
    source_sql = source_sql or get_source_sql(turn_alias=turn_alias, session_alias=session_alias)
    return f"""
        CASE
            WHEN {turn_backend} != '' THEN {turn_backend}
            WHEN {session_backend} != '' THEN {session_backend}
            WHEN {turn_provider} NOT IN ('', 'claude', 'pi', 'codex', 'opencode') THEN {turn_provider}
            WHEN {session_provider} NOT IN ('', 'claude', 'pi', 'codex', 'opencode') THEN {session_provider}
            WHEN ({source_sql}) = 'claude' THEN 'anthropic'
            WHEN ({source_sql}) = 'codex' THEN 'openai'
            ELSE ''
        END
    """.strip()


def normalize_date_range(from_date=None, to_date=None):
    def normalize(value):
        if not value:
            return None
        try:
            return date.fromisoformat(str(value)).isoformat()
        except Exception:
            return None

    normalized_from = normalize(from_date)
    normalized_to = normalize(to_date)
    if normalized_from and normalized_to and normalized_from > normalized_to:
        normalized_from, normalized_to = normalized_to, normalized_from
    return normalized_from, normalized_to



def build_turn_filters(models=None, cutoff=None, providers=None, provider_sql=None, from_date=None, to_date=None):
    clauses = []
    params = []
    provider_sql = provider_sql or "COALESCE(t.provider, 'claude')"
    from_date, to_date = normalize_date_range(from_date, to_date)
    if models is not None:
        if not models:
            clauses.append("1 = 0")
        else:
            placeholders = ",".join("?" for _ in models)
            clauses.append(f"COALESCE(t.model, 'unknown') IN ({placeholders})")
            params.extend(models)
    if providers is not None:
        if not providers:
            clauses.append("1 = 0")
        else:
            placeholders = ",".join("?" for _ in providers)
            clauses.append(f"{provider_sql} IN ({placeholders})")
            params.extend(providers)
    if from_date or to_date:
        if from_date:
            clauses.append("substr(t.timestamp, 1, 10) >= ?")
            params.append(from_date)
        if to_date:
            clauses.append("substr(t.timestamp, 1, 10) <= ?")
            params.append(to_date)
    elif cutoff:
        clauses.append("substr(t.timestamp, 1, 10) >= ?")
        params.append(cutoff)
    where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return where_sql, params


def _duration_minutes(first_timestamp, last_timestamp):
    try:
        t1 = datetime.fromisoformat((first_timestamp or "").replace("Z", "+00:00"))
        t2 = datetime.fromisoformat((last_timestamp or "").replace("Z", "+00:00"))
        return round((t2 - t1).total_seconds() / 60, 1)
    except Exception:
        return 0


def cost_parts_for_turn(model, input_tokens, output_tokens, cache_read, cache_creation, native_cost=0.0):
    """Return native/estimated/total cost for a single turn."""
    native = native_cost or 0.0
    estimated = 0.0 if native > 0 else calc_cost(model, input_tokens or 0, output_tokens or 0, cache_read or 0, cache_creation or 0)
    return {
        "native_cost": native,
        "estimated_cost": estimated,
        "cost": native + estimated,
    }


def cost_for_turn(model, input_tokens, output_tokens, cache_read, cache_creation, native_cost=0.0):
    """Cost of a single turn.

    If the source recorded a native cost, trust it. Otherwise estimate
    via pricing.calc_cost.
    """
    return cost_parts_for_turn(model, input_tokens, output_tokens, cache_read, cache_creation, native_cost)["cost"]


def cost_parts_for_aggregate(model, native_cost_sum, est_input, est_output, est_cache_read, est_cache_creation):
    """Return native/estimated/total cost for an aggregate row."""
    native = native_cost_sum or 0.0
    estimated = calc_cost(model, est_input or 0, est_output or 0, est_cache_read or 0, est_cache_creation or 0)
    return {
        "native_cost": native,
        "estimated_cost": estimated,
        "cost": native + estimated,
    }


def cost_for_aggregate(model, native_cost_sum, est_input, est_output, est_cache_read, est_cache_creation):
    """Cost of an aggregate row (multiple turns summed in one group).

    `native_cost_sum` is the summed native_cost across the group's turns.
    `est_*` are the token sums from turns in the group with native_cost == 0
    (so we estimate only the non-native portion, then add the native portion).
    """
    return cost_parts_for_aggregate(model, native_cost_sum, est_input, est_output, est_cache_read, est_cache_creation)["cost"]


# Backwards-compat shim (eng review 2D). Remove once no external callers remain.
def _cost_for_row(model, input_tokens, output_tokens, cache_read, cache_creation, native_cost=0.0,
                  est_input=None, est_output=None, est_cache_read=None, est_cache_creation=None):
    if est_input is None:
        return cost_for_turn(model, input_tokens, output_tokens, cache_read, cache_creation, native_cost)
    return cost_for_aggregate(model, native_cost, est_input, est_output, est_cache_read, est_cache_creation)


_EST_COLUMNS = ", ".join([
    "SUM(CASE WHEN native_cost > 0 THEN 0 ELSE input_tokens          END) as est_input",
    "SUM(CASE WHEN native_cost > 0 THEN 0 ELSE output_tokens         END) as est_output",
    "SUM(CASE WHEN native_cost > 0 THEN 0 ELSE cache_read_tokens     END) as est_cache_read",
    "SUM(CASE WHEN native_cost > 0 THEN 0 ELSE cache_creation_tokens END) as est_cache_creation",
])

_EST_COLUMNS_T = ", ".join([
    "SUM(CASE WHEN t.native_cost > 0 THEN 0 ELSE t.input_tokens          END) as est_input",
    "SUM(CASE WHEN t.native_cost > 0 THEN 0 ELSE t.output_tokens         END) as est_output",
    "SUM(CASE WHEN t.native_cost > 0 THEN 0 ELSE t.cache_read_tokens     END) as est_cache_read",
    "SUM(CASE WHEN t.native_cost > 0 THEN 0 ELSE t.cache_creation_tokens END) as est_cache_creation",
])


def _count_tool_names(tool_names):
    counts = {}
    for chunk in (tool_names or "").split(","):
        name = chunk.strip()
        if not name:
            continue
        counts[name] = counts.get(name, 0) + 1
    return counts
