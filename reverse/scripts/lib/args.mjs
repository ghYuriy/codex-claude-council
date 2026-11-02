// Modified for Council: integrated paths, command namespace and host-specific collaboration rules.
export function parseArgs(argv, config = {}) {
  const valueOptions = new Set(config.valueOptions ?? []);
  const booleanOptions = new Set(config.booleanOptions ?? []);
  const aliasMap = config.aliasMap ?? {};
  const options = {};
  const positionals = [];
  let passthrough = false;
  let firstPositional;

  for (let index = 0; index < argv.length; index += 1) {
    const token = argv[index];

    if (passthrough) {
      firstPositional ??= index;
      positionals.push(token);
      continue;
    }

    if (token === "--") {
      passthrough = true;
      continue;
    }

    if (!token.startsWith("-") || token === "-") {
      firstPositional ??= index;
      positionals.push(token);
      // Free text such as a task prompt ends option parsing, so words like --write stay text.
      passthrough = Boolean(config.stopAtPositional);
      continue;
    }

    if (token.startsWith("--")) {
      const [rawKey, inlineValue] = token.slice(2).split("=", 2);
      const key = aliasMap[rawKey] ?? rawKey;

      if (booleanOptions.has(key)) {
        options[key] = inlineValue === undefined ? true : inlineValue !== "false";
        continue;
      }

      if (valueOptions.has(key)) {
        const nextValue = inlineValue ?? argv[index + 1];
        if (nextValue === undefined) {
          throw new Error(`Missing value for --${rawKey}`);
        }
        options[key] = nextValue;
        if (inlineValue === undefined) {
          index += 1;
        }
        continue;
      }

      firstPositional ??= index;
      positionals.push(token);
      passthrough = Boolean(config.stopAtPositional);
      continue;
    }

    const shortKey = token.slice(1);
    const key = aliasMap[shortKey] ?? shortKey;

    if (booleanOptions.has(key)) {
      options[key] = true;
      continue;
    }

    if (valueOptions.has(key)) {
      const nextValue = argv[index + 1];
      if (nextValue === undefined) {
        throw new Error(`Missing value for -${shortKey}`);
      }
      options[key] = nextValue;
      index += 1;
      continue;
    }

    firstPositional ??= index;
    positionals.push(token);
    passthrough = Boolean(config.stopAtPositional);
  }

  return { options, positionals, firstPositional };
}

export function splitRawArgumentString(raw, { offsets = false } = {}) {
  const tokens = [];
  let current = "";
  let start = null;
  let quote = null;
  let escaping = false;

  const finish = () => {
    if (current) {
      tokens.push(offsets ? { value: current, start } : current);
    }
    current = "";
    start = null;
  };

  for (let index = 0; index < raw.length; index += 1) {
    const character = raw[index];
    if (start === null && !/\s/.test(character)) {
      start = index;
    }

    if (escaping) {
      current += character;
      escaping = false;
      continue;
    }

    if (character === "\\") {
      escaping = true;
      continue;
    }

    if (quote) {
      if (character === quote) {
        quote = null;
      } else {
        current += character;
      }
      continue;
    }

    if (character === "'" || character === "\"") {
      quote = character;
      continue;
    }

    if (/\s/.test(character)) {
      finish();
      continue;
    }

    current += character;
  }

  if (escaping) {
    current += "\\";
  }

  finish();
  return tokens;
}
