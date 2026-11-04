"""Token usage of helper runs, as the installed CLIs report it.

Claude: the result envelope's `usage` is that call, while `modelUsage` is the session so far. Codex: the app-server's
`thread/tokenUsage/updated` carries the thread's running `total`, so one run is the difference between its last total
and the total the session had before the turn. Whatever was not received is None, never zero. A run whose turn did not
complete has only a lower bound, marked complete: False.
"""
import json

CLAUDE_FIELDS = ('input_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens', 'output_tokens')
CODEX_FIELDS = ('totalTokens', 'inputTokens', 'cachedInputTokens', 'outputTokens', 'reasoningOutputTokens')


def _counts(values, fields):
    return isinstance(values, dict) and all(type(values.get(f)) is int and values[f] >= 0 for f in fields)


def claude_run(stdout):
    try:
        envelope = json.loads(stdout.decode('utf-8'))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(envelope, dict) or envelope.get('type') != 'result':
        return None
    reported, session = envelope.get('usage'), envelope.get('modelUsage')
    if not isinstance(reported, dict) and not isinstance(session, dict):
        return None
    # What was reported is kept as it is; only four complete counters of this call can be summed.
    tokens = {f: reported[f] for f in CLAUDE_FIELDS} if _counts(reported, CLAUDE_FIELDS) else None
    return {'tokens': tokens, 'reported': reported if isinstance(reported, dict) else None, 'session': session,
            'complete': True}


def codex_run(record, earlier=()):
    """record: the adapter's usage file, {thread_id, start, end, completed, went_back}; start is the total before the
    turn. earlier: the last totals the earlier runs of the same session saw, oldest first (see last_total)."""
    if not isinstance(record, dict) or record.get('went_back'):
        return None  # The counter went back inside the turn, so no difference of it belongs to this run.
    start, end = record.get('start'), record.get('end')
    if not _counts(start, CODEX_FIELDS) or not _counts(end, CODEX_FIELDS):
        return None
    if any(end[f] < start[f] for f in CODEX_FIELDS):
        return None  # The counter went back, so the start no longer belongs to this total.
    before = earlier[-1] if earlier else None
    if _counts(before, CODEX_FIELDS) and any(start[f] < before[f] for f in CODEX_FIELDS):
        return None  # The session's counter is below what it already reported: it was reset.
    return {'tokens': {f: end[f] - start[f] for f in CODEX_FIELDS}, 'session': {f: end[f] for f in CODEX_FIELDS},
            'complete': record.get('completed') is True}


def last_total(record):
    """The session's last running total a Codex run saw, even when its own usage is unknown."""
    if not isinstance(record, dict):
        return None
    for total in (record.get('end'), record.get('start')):
        if _counts(total, CODEX_FIELDS):
            return {f: total[f] for f in CODEX_FIELDS}
    return None


def summarize(runs):
    """runs: (provider, usage or None) pairs; per provider, the known sum and how many runs are partial or unknown."""
    summary = {}
    for provider, usage in runs:
        entry = summary.setdefault(provider, {'known_runs': 0, 'partial_runs': 0, 'unknown_runs': 0, 'at_least': {}})
        if usage is None or usage.get('tokens') is None:
            entry['unknown_runs'] += 1
            continue
        entry['known_runs' if usage.get('complete', True) else 'partial_runs'] += 1
        for field, value in usage['tokens'].items():
            entry['at_least'][field] = entry['at_least'].get(field, 0) + value
    return summary
