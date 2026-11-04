"""final.md: the Council result, written only from what the task recorded, most important first.

The report belongs to the result and snapshot of the finalize that wrote it; `status` never rewrites it and reports
whether it still holds for the current content (STALE). No model is called and nothing is inferred. A recorded file is
used only when it matches the hash recorded with it, otherwise the report says so. Recorded text is shown as text:
inline it is escaped, and the user's saved decisions are quoted verbatim in code blocks, so it can neither add
structure to the report nor hide words. Every record is rendered on its own: a damaged one is reported in its place
and never removes the others, and the report is always written.
"""
import hashlib
import json
from pathlib import Path

CLOSED = ('VERIFIED_FIXED', 'REJECTED_WITH_EVIDENCE', 'ACCEPTED_RISK_BY_USER')
REVIEWER_CLOSURES = ('VERIFIED_FIXED', 'REJECTED_WITH_EVIDENCE')
QUOTE_LIMIT = 1000
SPECIAL = set('\\`*_[]<>#|~!&')
# Values the controller validates itself; shown as they are.
STATUSES = {'OPEN', 'FIXED_PENDING_CHECK', 'DISPUTED', 'BLOCKED', 'PENDING', 'PROMOTED', 'WITHDRAWN', 'COMPLETE',
            'INVALID', 'INTERRUPTED', 'RUNNING', 'MANUAL', 'passed', 'failed', 'unknown', *CLOSED}


def render_final(task, state, snap, failures, reused, usage):
    """The text of final.md. failures: acceptance failures; reused: checks reused from earlier snapshots;
    usage: the per-helper usage summary."""
    task = Path(task)
    lines = ['# Council result', '', 'Status: ' + ('BLOCKED' if failures else 'PASSED'), 'Snapshot: ' + snap['id'], '',
             'This report records the result for this snapshot when this finalize wrote it. Run `status` to see whether '
             'it still holds for the current content (STALE).', '']
    lines += guarded(lambda: summary(state), 'The task summary')
    reports = guarded(lambda: verified_reports(task, state), None)
    if reports is None:
        reports = [('The list of accepted reports', None, 'could not be built from state.json; no report was used')]
    for heading, build in (('Blocking and unverified', lambda: blocking(state, failures, reports)),
                           ('User decisions', lambda: user_decisions(task, state)),
                           ('Findings and their outcome', lambda: findings(task, state)),
                           ('Limits of checks and reviews', lambda: limits(state, reused, reports)),
                           ('Recorded reasons', lambda: recorded_reasons(task, state)),
                           ('Usage', lambda: usage_lines(usage)),
                           ('Technical record', lambda: technical(state, reused))):
        lines += ['', '## ' + heading, *(guarded(build, 'This section') or ['None.'])]
    # Text that cannot be encoded, such as a lone surrogate in a record, must not stop the report from being written.
    return ('\n'.join(lines) + '\n').encode('utf-8', 'replace').decode('utf-8')


def guarded(build, what):
    """The lines of one record or section; a record that cannot be rendered is reported in its place."""
    try:
        return build()
    except Exception as error:  # The result must still be written; an unreadable record is reported, not fatal.
        if what is None:
            return None
        return [f'- {what} could not be built from the recorded data ({type(error).__name__}); '
                'see the technical record and state.json.']


def each(records, build, what):
    lines = []
    for record in records:
        lines += guarded(lambda: build(record), what)
    return lines


def text(value):
    """Recorded text shown inline: line breaks marked, Markdown and HTML characters escaped."""
    value = '' if value is None else str(value)
    value = value.replace('\r\n', '\n').replace('\r', '\n').replace('\n', ' ↵ ')
    return ''.join('\\' + ch if ch in SPECIAL else ch for ch in value)


def word(value):
    return value if value in STATUSES else text(value)


def number(value):
    return str(value) if isinstance(value, int) else repr(value)


def items(value):
    return value if isinstance(value, list) else []


def summary(state):
    changed = {rid: record['decision'] for rid, record in state.get('preservation_records', {}).items()
               if isinstance(record, dict) and record.get('decision')}

    def requirement(r):
        kind = 'mandatory' if r.get('mandatory', True) else 'optional'
        checks = ', '.join(text(c) for c in r['checks']) or 'none'
        if r['id'] in changed:
            return [f"- {text(r['id'])} ({text(r['stage'])}, {kind}): changed by the user to: "
                    f"{text(changed[r['id']]['criterion'])}; originally: {text(r['criterion'])}; checks: {checks}"]
        return [f"- {text(r['id'])} ({text(r['stage'])}, {kind}): {text(r['criterion'])}; checks: {checks}"]

    return [f"Task: {text(state['task_id'])} ({text(state.get('task_key', 'no key'))}); lead: "
            f"{text(state.get('lead', 'codex'))}; state version {state['version']}.", '', 'Requirements:',
            *each(state['requirements'], requirement, 'A requirement')]


def verified_reports(task, state):
    """Accepted reports that still match their recorded hash: the latest complete one of every discussion, plus the
    stage reviews. Each entry: (label, report or None, note); a report is None when it cannot be used."""
    latest, entries = {}, []
    for run in state['runs']:
        try:
            if run.get('status') == 'COMPLETE':
                latest[run.get('session_key', run['phase'] + ':' + run['stage'])] = run
        except Exception:  # A damaged run record is reported, the others are still read.
            entries.append(('A run record', None, 'could not be read from state.json; its report was not used'))
    sources = []
    for label, build in [(key, lambda run=run: (str(Path(run['directory']) / 'report.json'), run.get('report_hash')))
                         for key, run in latest.items()] + \
                        [('review of ' + str(stage), lambda record=record: (record['report'], record.get('hash')))
                         for stage, record in state['reviews'].items()]:
        try:
            sources.append((label, *build()))
        except Exception:
            entries.append((label, None, 'its record could not be read from state.json; report not used'))
    seen = set()
    for label, relative, recorded in sources:
        if relative in seen:
            continue
        seen.add(relative)
        try:
            report = json.loads((task / relative).read_text())
        except FileNotFoundError:
            entries.append((label, None, 'report unavailable'))
            continue
        except Exception:  # Unreadable, not JSON or not valid text: reported like a changed file.
            entries.append((label, None, 'report unreadable; not used'))
            continue
        try:
            matches = isinstance(report, dict) and recorded is not None and object_hash(report) == recorded
        except Exception:  # Content that cannot be encoded, such as a lone surrogate, cannot match a recorded hash.
            entries.append((label, None, 'report unreadable; not used'))
            continue
        if recorded is None:
            entries.append((label, None, 'report has no recorded hash; not used'))
        elif not matches:
            entries.append((label, None, 'report changed after it was accepted; not used'))
        else:
            entries.append((label, report, None))
    return entries


def blocking(state, failures, reports):
    lines = ['- ' + text(failure) for failure in failures]

    def issue(i):
        if i['status'] in CLOSED:
            return []
        return [f"- Finding {text(i['id'])} is {word(i['status'])} ({text(i.get('severity', 'unknown severity'))}): "
                f"{text(i.get('claim'))}"]

    def candidate(c):
        if c['status'] != 'PENDING':
            return []
        return [f"- Provisional finding {text(c['id'])} is unresolved: {text(c['finding'].get('claim'))}"]

    def report(entry):
        label, body, _ = entry
        if body is None:
            return []
        found = [f'- Not verified in {text(label)}: {text(item)}' for item in items(body.get('unverified'))]
        found += [f"- Requirement {text(item.get('requirement_id'))} not verified in {text(label)}: "
                  f"{text(item.get('explanation'))}"
                  for item in items(body.get('coverage')) if isinstance(item, dict) and item.get('status') == 'unverified']
        return found

    lines += each(state['issues'], issue, 'A finding')
    lines += each(state.get('candidates', []), candidate, 'A provisional finding')
    lines += each(reports, report, 'A report')
    return lines


def issue_decisions(state):
    """Each recorded resolve of an issue, in order: the transcript keeps them after a later closure replaces it."""
    found = []
    for event in state.get('events', []):
        if not isinstance(event, dict):
            continue
        detail = event.get('detail')
        if event.get('event') == 'issue_decision' and isinstance(detail, dict) and isinstance(detail.get('resolution'), dict):
            found.append(detail)
    return found


def user_decisions(task, state):
    current = {issue.get('id'): issue for issue in state['issues'] if isinstance(issue, dict)}
    risks, quoted = [], set()
    for detail in [d for d in issue_decisions(state) if d.get('status') == 'ACCEPTED_RISK_BY_USER'] + \
                  [i for i in state['issues'] if isinstance(i, dict) and i.get('status') == 'ACCEPTED_RISK_BY_USER']:
        resolution = detail.get('resolution')
        file = resolution.get('user_decision') if isinstance(resolution, dict) else None
        if file and file not in quoted:
            quoted.add(file)
            risks.append(detail)

    def risk(detail):
        resolution = detail['resolution']
        now = current.get(detail.get('id'), {}).get('status')
        reopened = '' if now == 'ACCEPTED_RISK_BY_USER' else f'; the finding was reopened later and is now {word(now)}'
        return [f"- Risk accepted for {text(detail['id'])} ({text(detail.get('severity', 'unknown severity'))}): "
                f"{text(detail.get('claim'))}{reopened}",
                '  Reason recorded: ' + text(resolution.get('reason')),
                *quote(task, resolution['user_decision'], resolution.get('sha256'))]

    def extension(entry):
        added = ', '.join(f'{key}: +{number(value)}' for key, value in entry['additions'].items() if value)
        return ['- Budget extended (' + added + ')', '  Reason recorded: ' + text(entry['reason']),
                *quote(task, entry['user_decision'], entry['sha256'])]

    def preserved(pair):
        rid, record = pair
        decision = record.get('decision') if isinstance(record, dict) else None
        if not decision:
            return []
        return [f"- Preserved condition {text(rid)} changed by the user; new criterion: {text(decision['criterion'])}",
                *quote(task, decision['artifact'], decision['sha256'])]

    def continued(pair):
        stage, decision = pair
        return [f'- Repair of {text(stage)} continued after a failed repair',
                *quote(task, decision['file'], decision['sha256'])]

    repairs = [(stage, decision) for stage, intervention in sorted(state.get('interventions', {}).items())
               for decision in intervention.get('decisions', [])]
    return (each(risks, risk, 'An accepted risk') +
            each(state.get('budget_extensions', []), extension, 'A budget extension') +
            each(sorted(state.get('preservation_records', {}).items()), preserved, 'A preservation decision') +
            each(repairs, continued, 'A repair continuation decision'))


def quote(task, file, sha256):
    """The user's saved words verbatim in a code block, or a note that the file cannot be used."""
    path = task / file
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        data = None
    except OSError:
        return [f'  Decision ({text(file)}): the saved decision file cannot be read; not quoted.']
    if data is None or hashlib.sha256(data).hexdigest() != sha256:
        return [f'  Decision ({text(file)}): the saved decision file is missing or changed; not quoted.']
    words = data.decode('utf-8', 'replace')
    shown = words if len(words) <= QUOTE_LIMIT else words[:QUOTE_LIMIT]
    run, longest = 0, 0
    for ch in shown:
        run = run + 1 if ch == '`' else 0
        longest = max(longest, run)
    fence = '`' * max(3, longest + 1)
    lines = [f'  Decision ({text(file)}):', '  ' + fence]
    lines += ['  ' + line for line in shown.replace('\r\n', '\n').replace('\r', '\n').split('\n')]
    if lines[-1] == '  ' and shown.endswith('\n'):
        lines.pop()  # The file's final line break, not an empty line of the decision.
    lines.append('  ' + fence)
    if shown != words:
        lines.append(f'  (quoted in part: the first {QUOTE_LIMIT} characters; full text in {text(file)})')
    return lines


def reference(item):
    if not isinstance(item, dict):
        return text(item)
    if item.get('kind') == 'check':
        shown = 'check ' + text(item.get('check_id'))
    else:
        shown = text(item.get('path'))
        if item.get('start_line'):
            shown += f":{text(item['start_line'])}-{text(item.get('end_line', item['start_line']))}"
    if item.get('explanation'):
        shown += f" ({text(item['explanation'])})"
    return shown


def evidence(values):
    shown = ', '.join(reference(e) for e in items(values))
    return f'; evidence: {shown}' if shown else ''


def repair_actions(task, state):
    """Each issue's actions from delegated repairs whose report still matches its recorded hash."""
    actions = {}

    def record_actions(record):
        if record.get('status') != 'COMPLETE' or not record.get('report_hash'):
            return []
        source = str(Path(record['directory']) / 'report.json')
        report = json.loads((task / source).read_text())
        if object_hash(report) != record['report_hash']:
            return [f'- Delegated repair report {text(source)} changed after it was accepted; not used']
        for action in items(report.get('issue_actions')):
            if isinstance(action, dict):
                actions.setdefault(action.get('id'), []).append(
                    f"  - {word(action.get('action'))} by the delegated implementer ({text(source)}): "
                    f"{text(action.get('reason'))}")
        return []

    records = [r for intervention in state.get('interventions', {}).values() for r in intervention.get('repairs', [])]
    notes = each(records, record_actions, 'A delegated repair report')
    return actions, notes


def findings(task, state):
    decisions = {}
    for detail in issue_decisions(state):
        decisions.setdefault(detail.get('id'), []).append(detail)
    delegated, notes = repair_actions(task, state)

    def issue(i):
        lines = [f"- {text(i['id'])} ({text(i.get('severity', 'unknown severity'))}): {text(i.get('claim'))} — "
                 f"{word(i['status'])}"]
        for detail in decisions.get(i['id'], []):
            resolution = detail['resolution']
            lines.append(f"  - {word(detail.get('status'))} by {text(resolution.get('actor', 'unknown'))}: "
                         f"{text(resolution.get('reason'))}")
        lines += delegated.get(i['id'], [])
        closures = i.get('closures')
        if closures is None:
            # Early releases kept the reviewer's closure only in the resolution.
            resolution = i.get('resolution') or {}
            closures = [resolution] if resolution.get('status') in REVIEWER_CLOSURES else []
        for closure in items(closures):
            lines.append(f"  - {word(closure.get('status'))} by the reviewer: {text(closure.get('reason'))}"
                         + evidence(closure.get('evidence')))
        lines += [f'  - Evidence of the finding: {reference(e)}' for e in items(i.get('evidence'))]
        return lines

    def candidate(c):
        line = f"- Provisional finding {text(c['id'])}: {text(c['finding'].get('claim'))} — {word(c['status'])}"
        resolution = c.get('resolution')
        if resolution:
            decision = resolution['decision']
            line += f"; reason: {text(decision.get('reason'))} (request {text(resolution['request_id'])})"
            line += evidence(decision.get('evidence'))
            if resolution.get('issue_id'):
                line += '; became ' + text(resolution['issue_id'])
        return [line]

    return (notes + each(state['issues'], issue, 'A finding') +
            each(state.get('candidates', []), candidate, 'A provisional finding'))


def limits(state, reused, reports):
    reused_ids = {item['check_id'] for item in reused}

    def check(pair):
        check_id, item = pair
        # The command records hold the command Council ran; an attestation holds a claim, whatever its method is named.
        if 'command' in item and 'claim' not in item:
            line = f"- {text(check_id)}: command run by Council, result {word(item.get('result'))}"
        else:
            line = (f"- {text(check_id)}: attested by {text(item.get('method'))}, not run by Council; claim: "
                    f"{text(item.get('claim'))}; result {word(item.get('result'))}")
        line += f"; snapshot {text(item.get('snapshot_id'))}"
        if check_id in reused_ids:
            line += '; reused for the current snapshot'
        if item.get('excluded_paths'):
            line += '; excluded paths: ' + ', '.join(text(p) for p in item['excluded_paths'])
        return [line]

    def report(entry):
        label, body, note = entry
        if body is None:
            return [f'- {text(label)}: {note}']
        return [f'- {text(label)}: {text(item)}' for item in items(body.get('limitations'))]

    return (each(sorted(state['checks'].items()), check, 'A check') +
            [f'- {text(check_id)}: an execution was interrupted' for check_id in sorted(state.get('interrupted_checks', {}))] +
            each(reports, report, 'A report') +
            ['- Council checks process, report shape, files, budgets and freshness; it does not prove semantic '
             'correctness.',
             '- A saved user decision is a recorded assertion, not proof of its author.'])


def recorded_reasons(task, state):
    def plan(pair):
        stage, record = pair
        source = str(Path(record['directory']) / 'plan.json')
        try:
            body = json.loads((task / source).read_text())
            intact = isinstance(body, dict) and object_hash(body) == record.get('plan_hash')
        except Exception:  # Missing, unreadable or not encodable: it cannot match its recorded hash.
            intact = False
        if not intact:
            return [f'- Repair plan for {text(stage)} ({text(source)}): missing or changed']
        return [f"- Repair plan for {text(stage)} ({text(source)}): cause ({text(body.get('cause_status', 'unstated'))}): "
                f"{text(body.get('cause'))}; strategy: {text(body.get('strategy'))}; "
                f"findings: {', '.join(text(i) for i in items(body.get('issue_ids')))}"]

    runs = {run['request_id']: run for run in state['runs']}

    def revalidation(event):
        detail = event.get('detail')
        if event.get('event') != 'offline_revalidation' or not isinstance(detail, dict):
            return []
        run = runs.get(detail.get('request_id'), {})
        # Recorded before the attempt; its outcome is the response's status now.
        return [f"- An attempted offline revalidation of response {text(detail.get('request_id'))} "
                f"(recorded in transcript.jsonl): {text(detail.get('reason'))}; the response is now "
                f"{word(run.get('status', 'unknown'))}"]

    plans = [(stage, record) for stage, intervention in sorted(state.get('interventions', {}).items())
             for record in intervention.get('repairs', [])]
    return each(plans, plan, 'A repair plan') + each(state.get('events', []), revalidation, 'A revalidation record')


def usage_lines(usage):
    def provider(pair):
        name, entry = pair
        tokens = ', '.join(f'{field} {value}' for field, value in sorted(entry['at_least'].items())) or 'none known'
        return [f"- {name}: known runs: {entry['known_runs']}, partial: {entry['partial_runs']}, "
                f"unknown: {entry['unknown_runs']}; at least: {tokens}"]

    lines = each(sorted(usage.items()), provider, 'The usage of a helper')
    if lines:
        lines.append('- Tokens as each CLI reports them, not cost; a partial run counts what was received before it '
                     'stopped.')
    return lines


def technical(state, reused):
    # Raw records in JSON, kept as they are; the readable sections above come from them.
    return ['### Findings', '```json', *[encoded(i) for i in state['issues']], '```',
            '', '### Provisional findings', '```json', *[encoded(c) for c in state.get('candidates', [])], '```',
            '', '### Reused check evidence', '```json', *[encoded(c) for c in reused], '```',
            '', '### Preservation baselines, deviations and explicit decisions', '```json',
            encoded(state.get('preservation_records', {})), '```']


# The controller's own encoding, so the hashes recorded with its files can be checked here.
def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)


def object_hash(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()
