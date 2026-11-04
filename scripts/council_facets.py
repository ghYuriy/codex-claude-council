"""Analysis facets: the registry, the unit plan and the checks of report format 3.

A facet is one direction of analysis (framing, premises, premortem, probe, support). A unit is one facet applied to
one object. All units of one call are answered in one report; each facet has its own required section, its own fields
and its own checks. The controller owns state; this module only builds and checks data.
"""
import re

STRING = {'type': 'string'}
STRINGS = {'type': 'array', 'items': STRING}
BOOLEAN = {'type': 'boolean'}
UNIT_ID = re.compile(r'U-[A-Za-z0-9_.-]{1,64}')
UNIT_FIELDS = {'id', 'facet', 'object', 'requirement_ids', 'materials', 'obligation'}
DISCUSSION = ('DESIGN', 'CONSULT')
ALL_PHASES = ('DESIGN', 'CONSULT', 'REVIEW')


def obj(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}


def array(item):
    return {'type': 'array', 'items': item}


def choice(*values):
    return {'type': 'string', 'enum': list(values)}


FACETS = {
    'framing': {
        'phases': DISCUSSION,
        'purpose': 'state the goal; list constraints separately from any proposed solution; give an alternative '
                   'framing, or leave it empty and give the reason there is none; list material gaps and whether '
                   'each blocks the answer and why.',
        'fields': lambda refs: {'goal': STRING, 'constraints': STRINGS, 'proposed_solution': STRING,
                                'alternative_framing': STRING, 'no_alternative_reason': STRING,
                                'gaps': array(obj({'gap': STRING, 'blocking': BOOLEAN, 'reason': STRING}))}},
    'premises': {
        'phases': ALL_PHASES,
        'purpose': 'list the premises the answer depends on; for each say whether it is decisive and verified, cite '
                   'its source or name the check that would verify it. Copy a decisive unverified premise verbatim '
                   'into the report unverified list.',
        'fields': lambda refs: {'premises': array(obj({'premise': STRING, 'decisive': BOOLEAN, 'verified': BOOLEAN,
                                                       'source': refs, 'needed_check': STRING}))}},
    'premortem': {
        'phases': DISCUSSION,
        'purpose': 'list plausible failures in ranked order: mechanism with its basis in actual dependencies, '
                   'conditions, early signal, proportionate check, and the reason for its rank. No probabilities '
                   'and no fixed count.',
        'fields': lambda refs: {'failures': array(obj({'mechanism': STRING, 'basis': STRING, 'conditions': STRING,
                                                       'early_signal': STRING, 'check': STRING,
                                                       'rank_reason': STRING}))}},
    'probe': {
        'phases': ALL_PHASES,
        'purpose': 'name at least two competing hypotheses and what each outcome would mean, one distinguishing '
                   'check, the basis of its cost estimate, and whether the check is planned or performed. Performed '
                   'requires a recorded check reference.',
        'fields': lambda refs: {'hypotheses': array(obj({'hypothesis': STRING, 'outcome_meaning': STRING})),
                                'distinguishing_check': STRING, 'cost_basis': STRING,
                                'check_status': choice('planned', 'performed'), 'check_evidence': refs}},
    'support': {
        'phases': ALL_PHASES,
        'purpose': 'restate the claim; cite supporting and contradicting evidence, describing the search when either '
                   'is not found; rate it SUPPORTED, OVERSTATED or UNSUPPORTED and give the wording the evidence '
                   'allows. An overstated or unsupported claim needs a linked issue in this report.',
        'fields': lambda refs: {'claim': STRING, 'supporting': refs, 'contradicting': refs, 'search': STRING,
                                'rating': choice('SUPPORTED', 'OVERSTATED', 'UNSUPPORTED'),
                                'acceptable_wording': STRING}},
}


class AnalysisError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise AnalysisError(message)


def unit_record(uid, facet, target, source, requirement_ids, materials, obligation):
    return {'id': uid, 'facet': facet, 'object': target, 'object_source': source, 'requirement_ids': requirement_ids,
            'materials': materials, 'obligation': obligation}


def auto_units(session_key, phase, goal, block, requirement_ids):
    """Units the controller adds to the first call of a session: framing and premises for DESIGN and CONSULT,
    and a premortem of a substantial block for DESIGN."""
    if phase not in DISCUSSION:
        return []
    facets = ['framing', 'premises']
    targets = {'framing': goal, 'premises': goal}
    if phase == 'DESIGN' and block and block.get('risk') == 'substantial':
        facets.append('premortem')
        targets['premortem'] = block['goal']
    return [unit_record('A-' + facet + '-' + session_key, facet, targets[facet], 'auto', list(requirement_ids), [],
                        'required') for facet in facets]


def lead_units(data, phase, requirement_ids, files, user_materials):
    """Validate an --analysis file and return its units. The origin is decided here, never by the file:
    `user` only when the object is a literal fragment of the named user material, otherwise `lead`."""
    require(isinstance(data, dict) and set(data) == {'units'} and isinstance(data['units'], list) and data['units'],
            'Analysis file must be an object with units: a nonempty list')
    units, seen = [], set()
    for n, item in enumerate(data['units']):
        label = 'Analysis unit ' + str(n)
        require(isinstance(item, dict), label + ' must be an object')
        require(UNIT_FIELDS <= set(item) <= UNIT_FIELDS | {'user_material'},
                'Unknown analysis unit fields or missing ones; expected: ' + ', '.join(sorted(UNIT_FIELDS)) + ' and optional user_material')
        uid = item['id']
        require(isinstance(uid, str) and UNIT_ID.fullmatch(uid), 'Analysis unit IDs must match U-<letters, digits, ._->')
        require(uid not in seen, 'Duplicate analysis unit ID: ' + uid)
        seen.add(uid)
        require(item['facet'] in FACETS, 'Unknown facet: ' + str(item['facet']))
        require(phase in FACETS[item['facet']]['phases'], 'Facet ' + item['facet'] + ' is not used in ' + phase)
        target = item['object']
        require(isinstance(target, str) and target.strip(), uid + ' needs an object: the text it analyses')
        ids = item['requirement_ids']
        require(isinstance(ids, list) and ids and len(set(ids)) == len(ids) and set(ids) <= set(requirement_ids),
                'Analysis requirement_ids must come from this call: ' + uid)
        materials = item['materials']
        require(isinstance(materials, list) and len(set(materials)) == len(materials) and
                all(isinstance(m, str) and m in files for m in materials),
                'Analysis materials must be scoped project files of the current snapshot: ' + uid)
        require(item['obligation'] in ('required', 'optional'), 'obligation must be required or optional: ' + uid)
        record = unit_record(uid, item['facet'], target, 'lead', list(ids), list(materials), item['obligation'])
        if 'user_material' in item:
            name = item['user_material']
            require(isinstance(name, str) and name in user_materials, 'Unknown user material: ' + str(name))
            if target in user_materials[name]['text']:
                record['object_source'] = 'user'
                record['user_material'] = {'path': user_materials[name]['path'], 'sha256': user_materials[name]['sha256']}
        units.append(record)
    return units


def changed_unit(old, new):
    """Units are immutable once planned; repeating the same unit is allowed."""
    keys = ('facet', 'object', 'object_source', 'requirement_ids', 'materials', 'obligation', 'user_material')
    return any(old.get(k) != new.get(k) for k in keys)


def schema_properties(facets, refs):
    """Format-3 additions: position changes, and one required section per facet when this call sends units."""
    common = {'unit_id': STRING, 'status': choice('assessed', 'not_applicable'), 'explanation': STRING,
              'evidence': refs, 'issue_ids': STRINGS}
    changes = array(obj({'prior_request_id': STRING, 'prior_position': STRING, 'new_position': STRING, 'evidence': refs}))
    if not facets:
        return {'position_changes': changes}
    analysis = obj({facet: array(obj(dict(common, **FACETS[facet]['fields'](refs)))) for facet in sorted(facets)})
    return {'analysis': analysis, 'position_changes': changes}


def notes(facets):
    if not facets:
        return ('position_changes records a change of an accepted position of this session (prior_request_id) with the '
                'evidence that changed it; correcting your own rejected report is not a position change. ')
    lines = ['Analysis units are listed in analysis_units. Return exactly one result per unit under analysis.<facet>, '
             'with its unit_id. status assessed needs current evidence; not_applicable needs an explanation of what was '
             'considered, and a required unit stays open until the user decides otherwise. Every finding from a unit '
             'goes into issues and is linked by issue_ids. An object marked lead is the lead\'s wording, not a '
             'verified fact or the user\'s request. position_changes records a change of an accepted position of this '
             'session (prior_request_id) with the evidence that changed it; correcting your own rejected report is '
             'not a position change. Facets in this call:']
    lines.extend(facet + ': ' + FACETS[facet]['purpose'] for facet in sorted(facets))
    return ' '.join(lines) + ' '


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def seq(value):
    """A list as recorded, or nothing: the shape pass already reports any other type."""
    return value if isinstance(value, list) else []


def rows(value):
    return [(n, item) for n, item in enumerate(seq(value)) if isinstance(item, dict)]


def facet_errors(report, request, ctx, errors=None):
    """Semantic checks of format 3, appended to `errors` as they are found. `ctx` supplies the controller's
    reference checks: ctx.evidence(label, refs, required), ctx.has_check(refs), ctx.issue_ids, ctx.report_issue_ids,
    ctx.accepted_requests."""
    errors = [] if errors is None else errors

    def check(label, condition, message):
        if not condition:
            errors.append(label + ': ' + message)

    units = {u['id']: u for u in request['analysis_units']}
    analysis = report.get('analysis')
    if units and isinstance(analysis, dict):
        seen = []
        for facet, results in analysis.items():
            for n, result in rows(results):
                label = 'analysis.' + facet + '[' + str(n) + ']'
                uid = result.get('unit_id')
                if not isinstance(uid, str):
                    continue
                seen.append(uid)
                unit = units.get(uid)
                check(label + '.unit_id', unit is not None and unit['facet'] == facet, 'Unknown unit for this facet: ' + uid)
                if unit is not None and unit['facet'] == facet:
                    errors.extend(result_errors(label, facet, result, report, ctx))
        expected = sorted(units)
        check('analysis', sorted(seen) == expected, 'expected one result per analysis unit: ' + ', '.join(expected))
    for n, change in rows(report.get('position_changes')):
        label = 'position_changes[' + str(n) + ']'
        prior = change.get('prior_request_id')
        check(label + '.prior_request_id', isinstance(prior, str) and prior in ctx.accepted_requests,
              'A position change must name an accepted report of this session')
        for field in ('prior_position', 'new_position'):
            if isinstance(change.get(field), str):
                check(label + '.' + field, nonempty(change[field]), 'Empty position')
        errors.extend(ctx.evidence(label + '.evidence', change.get('evidence'), True))
    return errors


def result_errors(label, facet, result, report, ctx):
    """Every reference of a result is checked, whatever its status; required content only for an assessed result."""
    errors = []
    assessed = result.get('status') == 'assessed'

    def check(field, condition, message):
        if not condition:
            errors.append(label + ('.' + field if field else '') + ': ' + message)

    def refs(field, value, needed=False):
        errors.extend(ctx.evidence(label + '.' + field, value, needed))

    def filled(item, prefix, fields):
        for field in fields:
            if isinstance(item.get(field), str):
                check(prefix + field, nonempty(item[field]), 'Empty ' + field)

    if isinstance(result.get('explanation'), str):
        check('explanation', nonempty(result['explanation']), 'Missing explanation')
    linked = [i for i in seq(result.get('issue_ids')) if isinstance(i, str)]
    unknown = [i for i in linked if i not in ctx.issue_ids]
    check('issue_ids', not unknown, 'Unknown issue IDs: ' + ', '.join(unknown))
    refs('evidence', result.get('evidence'), assessed)
    verdict = report.get('verdict')
    if facet == 'framing':
        for n, gap in rows(result.get('gaps')):
            if assessed:
                filled(gap, 'gaps[' + str(n) + '].', ('gap', 'reason'))
            if assessed and gap.get('blocking') is True:
                check('gaps[' + str(n) + ']', verdict in ('NEEDS_CONTEXT', 'NEEDS_DECISION'),
                      'A blocking gap requires NEEDS_CONTEXT or NEEDS_DECISION')
        if assessed:
            filled(result, '', ('goal',))
            if isinstance(result.get('alternative_framing'), str) and isinstance(result.get('no_alternative_reason'), str):
                check('alternative_framing', nonempty(result['alternative_framing']) or nonempty(result['no_alternative_reason']),
                      'Give an alternative framing or the reason there is none')
    elif facet == 'premises':
        unverified = [u for u in seq(report.get('unverified')) if isinstance(u, str)]
        premises = rows(result.get('premises'))
        if assessed and isinstance(result.get('premises'), list):
            check('premises', bool(result['premises']), 'An assessed result lists at least one premise')
        for n, item in premises:
            field = 'premises[' + str(n) + ']'
            refs(field + '.source', item.get('source'), assessed and item.get('verified') is True)
            if not assessed:
                continue
            filled(item, field + '.', ('premise',))
            if item.get('verified') is False:
                filled(item, field + '.', ('needed_check',))
                text = item.get('premise')
                if item.get('decisive') is True and nonempty(text):
                    check(field, any(text in entry for entry in unverified),
                          'A decisive unverified premise must be quoted in unverified')
    elif facet == 'premortem':
        if assessed and isinstance(result.get('failures'), list):
            check('failures', bool(result['failures']), 'An assessed premortem lists at least one failure')
        for n, item in rows(result.get('failures')):
            if assessed:
                filled(item, 'failures[' + str(n) + '].', ('mechanism', 'basis', 'conditions', 'early_signal', 'check', 'rank_reason'))
    elif facet == 'probe':
        performed = result.get('check_status') == 'performed'
        refs('check_evidence', result.get('check_evidence'), assessed and performed)
        if assessed:
            if isinstance(result.get('hypotheses'), list):
                check('hypotheses', len(result['hypotheses']) >= 2, 'A probe compares at least two hypotheses')
            for n, item in rows(result.get('hypotheses')):
                filled(item, 'hypotheses[' + str(n) + '].', ('hypothesis', 'outcome_meaning'))
            filled(result, '', ('distinguishing_check', 'cost_basis'))
            if performed:
                check('check_evidence', ctx.has_check(result.get('check_evidence')), 'A performed probe must cite a recorded check')
    elif facet == 'support':
        supporting, contradicting = result.get('supporting'), result.get('contradicting')
        refs('supporting', supporting, assessed and result.get('rating') == 'SUPPORTED')
        refs('contradicting', contradicting)
        if assessed:
            filled(result, '', ('claim', 'acceptable_wording'))
            if (supporting == [] or contradicting == []) and isinstance(result.get('search'), str):
                check('search', nonempty(result['search']), 'Describe the search when supporting or contradicting evidence was not found')
            if result.get('rating') in ('OVERSTATED', 'UNSUPPORTED') and isinstance(result.get('issue_ids'), list):
                check('issue_ids', any(i in ctx.report_issue_ids for i in linked),
                      'An overstated or unsupported claim needs a linked issue in this report')
    return errors


def results(report):
    """unit_id -> result of a validated format-3 report."""
    return {r['unit_id']: r for items in (report.get('analysis') or {}).values() for r in items}


def session_keys(key):
    """A replacement reviewer after a delegated repair continues the units of the review it supersedes."""
    suffix = ':claude-repair'
    return {key, key[:-len(suffix)]} if key.endswith(suffix) else {key}
