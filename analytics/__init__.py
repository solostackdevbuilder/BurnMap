"""Analytics package: cache + filter helpers, rollup builders, SQL-backed endpoint fetchers.

Public surface preserved for backward compatibility: `from analytics import X`
still works for every function the monolithic analytics.py used to expose.
"""
from .helpers import (  # noqa: F401
    _DASHBOARD_CACHE,
    _DASHBOARD_CACHE_TTL_SECONDS,
    _DASHBOARD_CACHE_LOCK,
    _EST_COLUMNS,
    _EST_COLUMNS_T,
    _cache_key,
    _cost_for_row,  # deprecated shim (2D) — remove when external callers go
    _count_tool_names,
    _duration_minutes,
    backend_provider_label,
    build_turn_filters,
    cache_get,
    cache_set,
    clear_dashboard_cache,
    cost_for_aggregate,
    cost_for_turn,
    cost_parts_for_aggregate,
    cost_parts_for_turn,
    get_backend_provider_sql,
    get_range_cutoff,
    get_source_sql,
    normalize_date_range,
    source_label,
)
from .rollups import (  # noqa: F401
    _build_branch_explorer,
    _build_conversation_tree,
    build_overview_summary,
    build_pi_summary,
    build_project_details,
    build_project_rollups,
    build_session_details,
    build_session_rollups,
)
from .queries import (  # noqa: F401
    _base_api_payload,
    get_all_time_stats_data,
    get_dashboard_data,
    get_light_dashboard_data,
    get_models_data,
    get_overview_data,
    get_pi_data,
    get_project_detail_data,
    get_projects_data,
    get_session_detail_data,
    get_sessions_data,
    get_today_usage_data,
)
