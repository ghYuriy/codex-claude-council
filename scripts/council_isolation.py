"""The environment a helper CLI receives.

The helper signs in only through its CLI's saved subscription login (`/login` in Claude Code, `codex login` with
ChatGPT); Council supports no other way. A task created from 1.5.0 gives the helper only the base set, the CLI's own
settings listed here, the location of its saved login and the names the task or the call adds. No variable of the
CLIs' own namespaces can be added, because any release may read new ones there that switch to another way of signing
in or carry the leading host's session; nor can a Bedrock sign-in token. Other added names pass as they are. Tasks created before 1.5.0 inherit the whole
environment, as before. Only names are ever recorded, never values.
"""
import re

# Paths, locale, temporary directory, session bus, proxies and certificates: what any CLI needs to start, read its
# saved login (the session bus reaches a login kept in the system keyring) and reach its service.
BASE_NAMES = frozenset({
    'PATH', 'HOME', 'USER', 'LOGNAME', 'SHELL', 'TMPDIR', 'TMP', 'TEMP', 'LANG', 'LANGUAGE', 'TZ', 'TERM',
    'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_STATE_HOME', 'XDG_CACHE_HOME', 'XDG_RUNTIME_DIR', 'DBUS_SESSION_BUS_ADDRESS',
    'HTTP_PROXY', 'HTTPS_PROXY', 'NO_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'no_proxy', 'all_proxy',
    'SSL_CERT_FILE', 'SSL_CERT_DIR', 'NODE_EXTRA_CA_CERTS', 'REQUESTS_CA_BUNDLE', 'CURL_CA_BUNDLE',
})
BASE_PREFIXES = ('LC_',)

# What each CLI reads for its own work besides signing in: client certificates, model choice and privacy opt-outs.
CLI_NAMES = {
    'claude': frozenset({
        'CLAUDE_CODE_CLIENT_CERT', 'CLAUDE_CODE_CLIENT_KEY', 'CLAUDE_CODE_CLIENT_KEY_PASSPHRASE', 'CLAUDE_CODE_CERT_STORE',
        'ANTHROPIC_MODEL', 'ANTHROPIC_DEFAULT_MODEL', 'ANTHROPIC_DEFAULT_OPUS_MODEL', 'ANTHROPIC_DEFAULT_SONNET_MODEL',
        'ANTHROPIC_DEFAULT_HAIKU_MODEL', 'ANTHROPIC_SMALL_FAST_MODEL', 'CLAUDE_CODE_SUBAGENT_MODEL',
        'CLAUDE_CODE_EFFORT_LEVEL', 'MAX_THINKING_TOKENS', 'CLAUDE_CODE_MAX_OUTPUT_TOKENS', 'DISABLE_TELEMETRY',
        'DO_NOT_TRACK', 'DISABLE_ERROR_REPORTING', 'DISABLE_AUTOUPDATER', 'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC',
    }),
    'codex': frozenset({'CODEX_CA_CERTIFICATE'}),
}

# Where each CLI keeps its saved subscription login.
LOGIN_NAMES = {'claude': frozenset({'CLAUDE_CONFIG_DIR'}), 'codex': frozenset({'CODEX_HOME'})}

# The CLIs' own namespaces. Only the names listed above pass from them; none can be added.
CLI_PREFIXES = ('ANTHROPIC_', 'CLAUDE', 'OPENAI_', 'CODEX_')
# Sign-in credentials of another method that a CLI reads outside its own namespaces.
OTHER_SIGN_IN_NAMES = frozenset({'AWS_BEARER_TOKEN_BEDROCK'})

NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')
PASS_ENV = 'COUNCIL_PASS_ENV'


def refused_names(names):
    """Names that cannot be added: not a plain environment name, a variable of a CLI's own namespace or a sign-in
    credential of another method."""
    return [name for name in names
            if not NAME.fullmatch(name or '') or name.startswith(CLI_PREFIXES) or name in OTHER_SIGN_IN_NAMES]


def once_names(env):
    """Names added for the current call only through COUNCIL_PASS_ENV; never saved into the task."""
    seen = []
    for name in re.split(r'[\s,]+', env.get(PASS_ENV, '')):
        if name and name not in seen:
            seen.append(name)
    return seen


def helper_env(provider, source_env, extra_names, once=()):
    """(environment, sorted names passed) for one helper launch; values stay in memory only. extra_names is None for
    a task created before 1.5.0, which inherits the whole environment."""
    if extra_names is None:
        env = dict(source_env)
    else:
        own = CLI_NAMES[provider] | LOGIN_NAMES[provider]
        added = {name for name in (*extra_names, *once) if not refused_names([name])}
        env = {name: value for name, value in source_env.items()
               if name in BASE_NAMES or name.startswith(BASE_PREFIXES) or name in own or name in added}
    return env, sorted(env)
