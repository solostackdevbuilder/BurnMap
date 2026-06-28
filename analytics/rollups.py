"""Rollup + summary builders: transform raw SQL rows into session / project / overview / Pi payloads."""
from .helpers import _count_tool_names, cost_for_aggregate, cost_for_turn, cost_parts_for_aggregate


def build_session_rollups(session_models_daily):
    session_map = {}
    for row in session_models_daily:
        session_key = row.get("full_session_id") or row.get("session_id")
        if session_key not in session_map:
            session_map[session_key] = {
                "session_id": row.get("session_id") or session_key,
                "full_session_id": session_key,
                "provider": row.get("provider") or "unknown",
                "project": row.get("project") or "unknown",
                "last": row.get("last") or "",
                "last_date": row.get("last_date") or "",
                "duration_min": row.get("duration_min") or 0,
                "turns": 0,
                "input": 0,
                "output": 0,
                "cache_read": 0,
                "cache_creation": 0,
                "native_cost": 0.0,
                "estimated_cost": 0.0,
                "cost": 0.0,
                "models": set(),
            }
        session = session_map[session_key]
        session["turns"] += row.get("turns", 0) or 0
        session["input"] += row.get("input", 0) or 0
        session["output"] += row.get("output", 0) or 0
        session["cache_read"] += row.get("cache_read", 0) or 0
        session["cache_creation"] += row.get("cache_creation", 0) or 0
        parts = cost_parts_for_aggregate(
            row.get("model"),
            row.get("native_cost", 0.0),
            row.get("est_input"), row.get("est_output"),
            row.get("est_cache_read"), row.get("est_cache_creation"),
        )
        session["native_cost"] += parts["native_cost"]
        session["estimated_cost"] += parts["estimated_cost"]
        session["cost"] += parts["cost"]
        if row.get("model"):
            session["models"].add(row.get("model"))

    sessions = []
    for session in session_map.values():
        model_list = sorted(session.pop("models"))
        session["model_list"] = model_list
        session["model"] = model_list[0] if len(model_list) == 1 else (f"mixed ({len(model_list)})" if model_list else "unknown")
        sessions.append(session)
    sessions.sort(key=lambda item: (item.get("last", ""), item.get("full_session_id", "")), reverse=True)
    return sessions


def build_project_rollups(session_rollups):
    project_map = {}
    for session in session_rollups:
        project = session.get("project") or "unknown"
        if project not in project_map:
            project_map[project] = {
                "project": project,
                "sessions": 0,
                "turns": 0,
                "input": 0,
                "output": 0,
                "cache_read": 0,
                "cache_creation": 0,
                "native_cost": 0.0,
                "estimated_cost": 0.0,
                "cost": 0.0,
            }
        item = project_map[project]
        item["sessions"] += 1
        item["turns"] += session.get("turns", 0) or 0
        item["input"] += session.get("input", 0) or 0
        item["output"] += session.get("output", 0) or 0
        item["cache_read"] += session.get("cache_read", 0) or 0
        item["cache_creation"] += session.get("cache_creation", 0) or 0
        item["native_cost"] += session.get("native_cost", 0.0) or 0.0
        item["estimated_cost"] += session.get("estimated_cost", 0.0) or 0.0
        item["cost"] += session.get("cost", 0.0) or 0.0
    projects = sorted(project_map.values(), key=lambda item: ((item["input"] + item["output"]), item["cost"]), reverse=True)
    return projects


def build_project_details(session_rollups, session_models_daily, session_analytics):
    details = {}
    for project in {row.get("project") or "unknown" for row in session_rollups}:
        project_sessions = [row for row in session_rollups if (row.get("project") or "unknown") == project]
        project_rows = [row for row in session_models_daily if (row.get("project") or "unknown") == project]
        project_session_analytics = [row for row in session_analytics if (row.get("project") or "unknown") == project]
        if not project_sessions and not project_rows and not project_session_analytics:
            continue

        totals = {
            "sessions": len(project_sessions),
            "turns": sum(row.get("turns", 0) or 0 for row in project_sessions),
            "input": sum(row.get("input", 0) or 0 for row in project_sessions),
            "output": sum(row.get("output", 0) or 0 for row in project_sessions),
            "cache_read": sum(row.get("cache_read", 0) or 0 for row in project_sessions),
            "cache_creation": sum(row.get("cache_creation", 0) or 0 for row in project_sessions),
            "native_cost": sum(row.get("native_cost", 0.0) or 0.0 for row in project_sessions),
            "estimated_cost": sum(row.get("estimated_cost", 0.0) or 0.0 for row in project_sessions),
            "cost": sum(row.get("cost", 0.0) or 0.0 for row in project_sessions),
        }

        daily_map = {}
        model_map = {}
        provider_turns = {}
        tool_map = {}
        for row in project_rows:
            day = row.get("day") or ""
            if day not in daily_map:
                daily_map[day] = {"day": day, "input": 0, "output": 0}
            daily_map[day]["input"] += row.get("input", 0) or 0
            daily_map[day]["output"] += row.get("output", 0) or 0

            model = row.get("model") or "unknown"
            if model not in model_map:
                model_map[model] = {"model": model, "input": 0, "output": 0, "turns": 0, "native_cost": 0.0, "estimated_cost": 0.0, "cost": 0.0}
            model_map[model]["input"] += row.get("input", 0) or 0
            model_map[model]["output"] += row.get("output", 0) or 0
            model_map[model]["turns"] += row.get("turns", 0) or 0
            parts = cost_parts_for_aggregate(
                model,
                row.get("native_cost", 0.0),
                row.get("est_input"), row.get("est_output"),
                row.get("est_cache_read"), row.get("est_cache_creation"),
            )
            model_map[model]["native_cost"] += parts["native_cost"]
            model_map[model]["estimated_cost"] += parts["estimated_cost"]
            model_map[model]["cost"] += parts["cost"]

            provider = row.get("provider") or "unknown"
            provider_turns[provider] = provider_turns.get(provider, 0) + (row.get("turns", 0) or 0)
            for tool, count in _count_tool_names(row.get("tool_names", "")).items():
                tool_map[tool] = tool_map.get(tool, 0) + count

        models = sorted(model_map.values(), key=lambda item: ((item["input"] + item["output"]), item["cost"]), reverse=True)
        tools = [{"name": name, "count": count} for name, count in sorted(tool_map.items(), key=lambda item: (-item[1], item[0]))]
        tree_totals = {
            "sessions": sum(1 for row in project_session_analytics if (row.get("tree_nodes", 0) or 0) > 0),
            "nodes": sum(row.get("tree_nodes", 0) or 0 for row in project_session_analytics),
            "edges": sum(row.get("tree_edges", 0) or 0 for row in project_session_analytics),
            "branch_points": sum(row.get("tree_branch_points", 0) or 0 for row in project_session_analytics),
            "leaves": sum(row.get("tree_leaf_count", 0) or 0 for row in project_session_analytics),
            "max_depth": max([row.get("tree_max_depth", 0) or 0 for row in project_session_analytics] or [0]),
        }
        tree_sessions = sorted(
            [row for row in project_session_analytics if (row.get("tree_nodes", 0) or 0) > 0],
            key=lambda item: ((item.get("tree_max_depth", 0) or 0), (item.get("tree_nodes", 0) or 0)),
            reverse=True,
        )[:8]
        top_session = max(project_sessions, key=lambda item: item.get("cost", 0.0), default=None)
        top_model = models[0] if models else None
        top_provider = sorted(provider_turns.items(), key=lambda item: item[1], reverse=True)[0] if provider_turns else None
        provider_count = len({row.get("provider") or "unknown" for row in project_sessions})
        model_count = len({model for row in project_sessions for model in (row.get("model_list") or [row.get("model")]) if model})

        details[project] = {
            "project": project,
            "totals": totals,
            "daily": sorted(daily_map.values(), key=lambda item: item["day"]),
            "models": models,
            "tools": tools,
            "treeTotals": tree_totals,
            "treeSessions": tree_sessions,
            "sessions": project_sessions[:5],
            "insights": [
                {"title": "Primary model", "text": f"{top_model['model']} leads this project with {top_model['input'] + top_model['output']} tokens."} if top_model else {"title": "Primary model", "text": "No model data."},
                {"title": "Most expensive session", "text": f"{top_session['session_id']} cost ${top_session['cost']:.4f} and produced {top_session['output']} output tokens."} if top_session else {"title": "Most expensive session", "text": "No sessions available."},
                {"title": "Source mix", "text": f"{top_provider[0]} contributes the most turns in this project."} if top_provider else {"title": "Source mix", "text": "No source data."},
                {"title": "Model switching", "text": f"{model_count} distinct models were used in this project." if model_count > 1 else "This project stayed on a single model."},
                {"title": "Source switching", "text": f"{provider_count} sources appeared in this project." if provider_count > 1 else "This project stayed on a single source."},
                {"title": "Cache behavior", "text": f"{round((totals['cache_read'] / totals['input']) * 100)}% cache-hit ratio against prompt volume." if totals['input'] else "No cache hits recorded."},
                {"title": "Pi tree complexity", "text": f"{tree_totals['sessions']} Pi sessions in this project reached max depth {tree_totals['max_depth']} with {tree_totals['branch_points']} total fork points." if tree_totals['sessions'] else "No Pi tree analytics recorded for this project."},
            ],
        }
    return details


def build_overview_summary(daily_by_model, provider_breakdown, session_rollups):
    totals = {
        "sessions": len(session_rollups),
        "turns": sum(item.get("turns", 0) or 0 for item in daily_by_model),
        "input": sum(item.get("input", 0) or 0 for item in daily_by_model),
        "output": sum(item.get("output", 0) or 0 for item in daily_by_model),
        "cache_read": sum(item.get("cache_read", 0) or 0 for item in daily_by_model),
        "cache_creation": sum(item.get("cache_creation", 0) or 0 for item in daily_by_model),
        "native_cost": sum(item.get("native_cost", 0.0) or 0.0 for item in daily_by_model),
        "estimated_cost": sum(
            cost_parts_for_aggregate(
                item.get("model"),
                item.get("native_cost", 0.0),
                item.get("est_input"), item.get("est_output"),
                item.get("est_cache_read"), item.get("est_cache_creation"),
            )["estimated_cost"]
            for item in daily_by_model
        ),
        "cost": sum(
            cost_parts_for_aggregate(
                item.get("model"),
                item.get("native_cost", 0.0),
                item.get("est_input"), item.get("est_output"),
                item.get("est_cache_read"), item.get("est_cache_creation"),
            )["cost"]
            for item in daily_by_model
        ),
    }
    source_distribution = [
        {
            "provider": item.get("provider") or "unknown",
            "sessions": item.get("sessions", 0) or 0,
            "turns": item.get("turns", 0) or 0,
            "tokens": (item.get("input", 0) or 0) + (item.get("output", 0) or 0),
            "native_cost": item.get("native_cost", 0.0) or 0.0,
            "estimated_cost": item.get("estimated_cost", 0.0) or 0.0,
            "cost": item.get("cost", 0.0) or 0.0,
        }
        for item in provider_breakdown
    ]
    return {"totals": totals, "source_distribution": source_distribution}


def build_pi_summary(session_analytics):
    pi_sessions = [
        row for row in session_analytics
        if row.get("client") == "pi-agent" or row.get("session_provider") == "pi" or row.get("tree_nodes", 0) > 0 or (row.get("full_session_id", "").startswith("pi:"))
    ]
    pi_sessions.sort(key=lambda item: ((item.get("tree_nodes", 0) or 0), (item.get("last_timestamp", "") or "")), reverse=True)
    totals = {
        "sessions": len(pi_sessions),
        "turns": sum(item.get("turns", 0) or 0 for item in pi_sessions),
        "switches": sum(item.get("total_switches", 0) or 0 for item in pi_sessions),
        "nodes": sum(item.get("tree_nodes", 0) or 0 for item in pi_sessions),
        "forks": sum(item.get("tree_branch_points", 0) or 0 for item in pi_sessions),
        "max_depth": max([item.get("tree_max_depth", 0) or 0 for item in pi_sessions] or [0]),
    }
    top_sessions = [
        {
            "session_id": row.get("session_id"),
            "full_session_id": row.get("full_session_id"),
            "project": row.get("project") or "unknown",
            "tree_nodes": row.get("tree_nodes", 0) or 0,
            "tree_max_depth": row.get("tree_max_depth", 0) or 0,
            "total_switches": row.get("total_switches", 0) or 0,
        }
        for row in pi_sessions[:5]
    ]
    return {"sessions": pi_sessions, "totals": totals, "top_sessions": top_sessions}


def _build_branch_explorer(turn_events):
    by_message_id = {}
    children = {}
    by_parent_id = {}

    for event in turn_events:
        if event.get("message_id"):
            by_message_id[event["message_id"]] = event
        parent_id = event.get("parent_message_id") or ""
        if parent_id:
            children.setdefault(parent_id, []).append(event)
            by_parent_id.setdefault(parent_id, []).append(event)

    roots = [event for event in turn_events if not event.get("parent_message_id") or event.get("parent_message_id") not in by_message_id]
    visited = set()
    branches = []

    def seen_key(event):
        return event.get("message_id") or event.get("timestamp") or id(event)

    def walk_branch(start_event):
        path = []
        current = start_event
        while current and seen_key(current) not in visited:
            visited.add(seen_key(current))
            path.append(current)
            current_children = children.get(current.get("message_id") or "", [])
            if len(current_children) != 1:
                break
            current = current_children[0]
        return path

    for root in roots:
        path = walk_branch(root)
        if path:
            branches.append(path)
        for node in path:
            node_children = children.get(node.get("message_id") or "", [])
            if len(node_children) > 1:
                for child in node_children:
                    sub_path = walk_branch(child)
                    if sub_path:
                        branches.append(sub_path)

    for event in turn_events:
        if seen_key(event) not in visited:
            path = walk_branch(event)
            if path:
                branches.append(path)

    forks = []
    for parent_id, child_events in by_parent_id.items():
        if len(child_events) <= 1:
            continue
        parent = by_message_id.get(parent_id)
        forks.append({
            "parent_id": parent_id,
            "parent": parent,
            "children": sorted(child_events, key=lambda item: item.get("timestamp") or ""),
        })
    forks.sort(key=lambda item: (-(len(item["children"])), ((item.get("parent") or {}).get("timestamp") or "")))

    branch_rows = []
    for idx, path in enumerate(branches, 1):
        branch_rows.append({
            "id": idx,
            "path": path,
            "start": path[0],
            "end": path[-1],
            "depth": len(path),
        })
    branch_rows.sort(key=lambda item: item["depth"], reverse=True)
    return {"forks": forks, "branches": branch_rows}


def _build_conversation_tree(message_nodes):
    nodes = [{**node, "children": []} for node in (message_nodes or [])]
    by_id = {node.get("message_id"): node for node in nodes if node.get("message_id")}
    roots = []
    for node in nodes:
        parent_id = node.get("parent_message_id")
        if parent_id and parent_id in by_id:
            by_id[parent_id]["children"].append(node)
        else:
            roots.append(node)
    for node in nodes:
        node["children"].sort(key=lambda item: ((item.get("timestamp") or ""), (item.get("message_id") or "")))
    roots.sort(key=lambda item: ((item.get("timestamp") or ""), (item.get("message_id") or "")))
    return {"roots": roots, "count": len(nodes)}


def build_session_details(session_rollups, turn_events, session_analytics, pi_message_nodes):
    details = {}
    for session in session_rollups:
        session_id = session.get("full_session_id") or session.get("session_id")
        events = sorted([row for row in turn_events if (row.get("full_session_id") or row.get("session_id")) == session_id], key=lambda item: item.get("timestamp") or "")
        analytics = next((row for row in session_analytics if (row.get("full_session_id") or row.get("session_id")) == session_id), None)
        message_nodes = sorted([row for row in pi_message_nodes if row.get("session_id") == session_id], key=lambda item: ((item.get("timestamp") or ""), (item.get("message_id") or "")))

        tool_map = {}
        for event in events:
            for tool, count in _count_tool_names(event.get("tool_names", "")).items():
                tool_map[tool] = tool_map.get(tool, 0) + count

        timeline = []
        last_model = None
        last_provider = None
        for idx, event in enumerate(events):
            model_changed = idx > 0 and event.get("model") != last_model
            provider_changed = idx > 0 and event.get("provider") != last_provider
            last_model = event.get("model")
            last_provider = event.get("provider")
            timeline.append({
                **event,
                "modelChanged": model_changed,
                "providerChanged": provider_changed,
                "changed": model_changed or provider_changed,
                "cost": event.get("cost", cost_for_turn(event.get("model"), event.get("input", 0), event.get("output", 0), event.get("cache_read", 0), event.get("cache_creation", 0), event.get("native_cost", 0.0))),
                "estimated_cost": event.get("estimated_cost", 0.0),
            })

        details[session_id] = {
            **session,
            "analytics": analytics,
            "events": timeline,
            "tools": [{"name": name, "count": count} for name, count in sorted(tool_map.items(), key=lambda item: (-item[1], item[0]))],
            "branchExplorer": _build_branch_explorer(timeline),
            "conversationTree": _build_conversation_tree(message_nodes),
            "messageNodes": message_nodes,
        }
    return details
