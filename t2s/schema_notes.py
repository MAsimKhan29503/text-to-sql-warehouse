TABLE_NOTES = {
    "dim_repo": "One row per GitHub repository (dimension).",
    "dim_actor": "One row per GitHub user or bot account (dimension).",
    "fct_events": (
        "One row per GitHub event (fact table). VERY LARGE: full scans are slow. "
        "Use it only when event-level detail is needed; for counts, trends and "
        "rankings use fct_daily_repo_activity instead."
    ),
    "fct_daily_repo_activity": (
        "One row per repository per day, pre-aggregated from fct_events and about 3x "
        "smaller, so queries on it are noticeably faster. The default choice for "
        "questions about activity, popularity and trends."
    ),
}

COLUMN_NOTES = {
    ("fct_events", "event_id"): "unique id of the event",
    ("fct_events", "event_type"): (
        "one of: PushEvent, PullRequestEvent, WatchEvent (a repo star), IssuesEvent, ForkEvent"
    ),
    ("fct_events", "created_at"): "when the event happened (UTC)",
    ("fct_events", "event_date"): "calendar date of created_at; use this for date filters",
    ("fct_events", "action"): "action within the event (for example opened or closed); may be NULL",
    ("fct_events", "loaded_at"): "when the row was loaded into the warehouse (pipeline metadata)",
    ("dim_repo", "repo_name"): "repository name in owner/name format",
    ("dim_repo", "last_seen_at"): "most recent time the repository appeared in the data",
    ("dim_actor", "actor_login"): "GitHub username",
    ("dim_actor", "is_bot"): "true for bot accounts; filter is_bot = false for human activity",
    ("dim_actor", "last_seen_at"): "most recent time the account appeared in the data",
    ("fct_daily_repo_activity", "stars"): "number of WatchEvents (stars) that day",
    ("fct_daily_repo_activity", "forks"): "number of ForkEvents that day",
    ("fct_daily_repo_activity", "unique_actors"): "distinct accounts active that day, bots included",
    ("fct_daily_repo_activity", "unique_human_actors"): "distinct non-bot accounts active that day",
}

JOIN_HINTS = [
    "analytics_marts.fct_events.repo_id = analytics_marts.dim_repo.repo_id",
    "analytics_marts.fct_events.actor_id = analytics_marts.dim_actor.actor_id",
    "analytics_marts.fct_daily_repo_activity.repo_id = analytics_marts.dim_repo.repo_id",
]