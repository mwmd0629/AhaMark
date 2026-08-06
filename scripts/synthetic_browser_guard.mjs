const SYNTHETIC_EMAIL_PATTERN =
  /^[a-z0-9](?:[a-z0-9._+-]{0,62}[a-z0-9])?@[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.synthetic\.invalid$/;
const SYNTHETIC_PROJECT_PATTERN =
  /^ahamark-(?:business-e2e|structured-only-[a-z0-9]+(?:-[a-z0-9]+)*|synthetic-[a-z0-9]+(?:-[a-z0-9]+)*)$/;
const SYNTHETIC_RUN_PATTERN =
  /^(?:business-e2e|business-exceptions|report-retry-e2e(?:-[a-z0-9]+)*|structured-only-[a-z0-9]+(?:-[a-z0-9]+)*|synthetic-[a-z0-9]+(?:-[a-z0-9]+)*)$/;
const SYNTHETIC_MARKER_SUFFIX_PATTERN =
  /^[a-z0-9]+(?:-[a-z0-9]+)*\.synthetic\.invalid$/;
const LOOPBACK_HOSTS = Object.freeze(["localhost", "127.0.0.1", "[::1]"]);
const TARGET_POLICIES = Object.freeze({
  assignment_preprod: Object.freeze({
    protocol: "https:",
    ports: Object.freeze(["8443", "9443", "9543"]),
  }),
  business_web: Object.freeze({
    protocol: "http:",
    ports: Object.freeze(["3300", "43387"]),
  }),
  business_api: Object.freeze({
    protocol: "http:",
    ports: Object.freeze(["8800", "48887"]),
  }),
});
const GUARD_OPTION_KEYS = Object.freeze([
  "allowSyntheticMutations",
  "teacherEmail",
  "targets",
  "composeProject",
  "runPrefix",
  "markerSuffix",
]);
const TARGET_OPTION_KEYS = Object.freeze(["name", "value", "policy"]);
const REQUIRED_GUARD_OPTION_KEYS = Object.freeze([
  "allowSyntheticMutations",
  "teacherEmail",
  "targets",
]);
const MAX_TARGETS = 8;
const TARGET_NAME_PATTERN = /^[A-Z][A-Z0-9_]{0,63}$/;
const RAW_ASCII_ORIGIN_PATTERN =
  /^(http|https):\/\/(localhost|127\.0\.0\.1|\[::1\]):([0-9]{1,5})$/;

function guardError(code, message) {
  return new Error(`${code}: ${message}`);
}

function requireExactString(name, value) {
  if (typeof value !== "string" || value.length === 0 || value !== value.trim())
    throw guardError(
      "SYNTHETIC_GUARD_VALUE_INVALID",
      `${name} must be a non-empty exact string`,
    );
  return value;
}

function reflectOrFail(operation, code, subject) {
  try {
    return operation();
  } catch {
    throw guardError(code, `${subject} could not be safely reflected`);
  }
}

function readJsonRecord(
  value,
  { allowedKeys, requiredKeys, codePrefix, subject },
) {
  if (value === null || typeof value !== "object")
    throw guardError(
      `${codePrefix}_RECORD_INVALID`,
      `${subject} must be a plain record`,
    );
  if (utilTypes.isProxy(value))
    throw guardError(
      `${codePrefix}_PROXY_UNSUPPORTED`,
      `${subject} must not be a Proxy`,
    );
  if (Array.isArray(value))
    throw guardError(
      `${codePrefix}_RECORD_INVALID`,
      `${subject} must be a plain record`,
    );
  const prototype = reflectOrFail(
    () => Reflect.getPrototypeOf(value),
    `${codePrefix}_REFLECTION_FAILED`,
    subject,
  );
  if (prototype !== Object.prototype && prototype !== null)
    throw guardError(
      `${codePrefix}_RECORD_INVALID`,
      `${subject} must have a plain or null prototype`,
    );
  const ownKeys = reflectOrFail(
    () => Reflect.ownKeys(value),
    `${codePrefix}_REFLECTION_FAILED`,
    subject,
  );
  if (
    ownKeys.some((key) => typeof key !== "string" || !allowedKeys.includes(key))
  )
    throw guardError(
      `${codePrefix}_OPTIONS_UNSUPPORTED`,
      `${subject} contains unsupported options`,
    );
  for (const requiredKey of requiredKeys) {
    if (!ownKeys.includes(requiredKey))
      throw guardError(
        `${codePrefix}_PROPERTY_REQUIRED`,
        `${subject} is missing a required property`,
      );
  }
  const result = Object.create(null);
  for (const key of ownKeys) {
    const descriptor = reflectOrFail(
      () => Reflect.getOwnPropertyDescriptor(value, key),
      `${codePrefix}_REFLECTION_FAILED`,
      subject,
    );
    if (
      descriptor === undefined ||
      !("value" in descriptor) ||
      descriptor.enumerable !== true
    )
      throw guardError(
        `${codePrefix}_PROPERTY_INVALID`,
        `${subject} properties must be enumerable data properties`,
      );
    result[key] = descriptor.value;
  }
  return result;
}

function readJsonTargets(value) {
  if (value !== null && typeof value === "object" && utilTypes.isProxy(value))
    throw guardError(
      "SYNTHETIC_TARGETS_PROXY_UNSUPPORTED",
      "targets must not be a Proxy",
    );
  let isArray;
  try {
    isArray = Array.isArray(value);
  } catch {
    throw guardError(
      "SYNTHETIC_TARGETS_REFLECTION_FAILED",
      "targets could not be safely reflected",
    );
  }
  if (!isArray)
    throw guardError(
      "SYNTHETIC_TARGETS_ARRAY_INVALID",
      "targets must be a standard dense array",
    );
  const prototype = reflectOrFail(
    () => Reflect.getPrototypeOf(value),
    "SYNTHETIC_TARGETS_REFLECTION_FAILED",
    "targets",
  );
  if (prototype !== Array.prototype)
    throw guardError(
      "SYNTHETIC_TARGETS_ARRAY_INVALID",
      "targets must use the standard Array prototype",
    );
  const ownKeys = reflectOrFail(
    () => Reflect.ownKeys(value),
    "SYNTHETIC_TARGETS_REFLECTION_FAILED",
    "targets",
  );
  const lengthDescriptor = reflectOrFail(
    () => Reflect.getOwnPropertyDescriptor(value, "length"),
    "SYNTHETIC_TARGETS_REFLECTION_FAILED",
    "targets",
  );
  if (
    lengthDescriptor === undefined ||
    !("value" in lengthDescriptor) ||
    !Number.isSafeInteger(lengthDescriptor.value) ||
    lengthDescriptor.value < 1 ||
    lengthDescriptor.value > MAX_TARGETS
  )
    throw guardError(
      "SYNTHETIC_TARGETS_COUNT_INVALID",
      `targets must contain between 1 and ${MAX_TARGETS} entries`,
    );
  const length = lengthDescriptor.value;
  const expectedKeys = [
    ...Array.from({ length }, (_, index) => String(index)),
    "length",
  ];
  if (
    ownKeys.length !== expectedKeys.length ||
    ownKeys.some(
      (key) => typeof key !== "string" || !expectedKeys.includes(key),
    )
  )
    throw guardError(
      "SYNTHETIC_TARGETS_SHAPE_INVALID",
      "targets must be dense and contain no extra properties",
    );
  const result = [];
  for (let index = 0; index < length; index += 1) {
    const descriptor = reflectOrFail(
      () => Reflect.getOwnPropertyDescriptor(value, String(index)),
      "SYNTHETIC_TARGETS_REFLECTION_FAILED",
      "targets",
    );
    if (
      descriptor === undefined ||
      !("value" in descriptor) ||
      descriptor.enumerable !== true
    )
      throw guardError(
        "SYNTHETIC_TARGETS_ELEMENT_INVALID",
        "targets entries must be enumerable data properties",
      );
    result.push(descriptor.value);
  }
  return result;
}

function parseRawAllowlistedOrigin(name, raw, reasonCode) {
  if (!/^[\x21-\x7e]+$/.test(raw))
    throw guardError(
      reasonCode,
      `${name} must use the strict ASCII synthetic origin format`,
    );
  const match = RAW_ASCII_ORIGIN_PATTERN.exec(raw);
  if (!match)
    throw guardError(
      reasonCode,
      `${name} must use a canonical loopback synthetic origin`,
    );
  return { protocol: `${match[1]}:`, hostname: match[2], port: match[3] };
}

function requireLocalTestTarget(target) {
  const fields = readJsonRecord(target, {
    allowedKeys: TARGET_OPTION_KEYS,
    requiredKeys: TARGET_OPTION_KEYS,
    codePrefix: "SYNTHETIC_TARGET",
    subject: "synthetic target",
  });
  const name = requireExactString("synthetic target name", fields.name);
  if (!TARGET_NAME_PATTERN.test(name))
    throw guardError(
      "SYNTHETIC_TARGET_NAME_INVALID",
      "synthetic target name must use the safe environment-key format",
    );
  const policyName = requireExactString(`${name} policy`, fields.policy);
  if (!Object.hasOwn(TARGET_POLICIES, policyName))
    throw guardError(
      "SYNTHETIC_TARGET_POLICY_UNKNOWN",
      `${name} uses an unknown synthetic target policy`,
    );
  const policy = TARGET_POLICIES[policyName];
  const raw = requireExactString(name, fields.value);
  const rawParts = parseRawAllowlistedOrigin(
    name,
    raw,
    "SYNTHETIC_TARGET_RAW_INVALID",
  );
  if (
    rawParts.protocol !== policy.protocol ||
    !policy.ports.includes(rawParts.port)
  )
    throw guardError(
      "SYNTHETIC_TARGET_NOT_ALLOWED",
      `${name} is not allowed by its synthetic target policy`,
    );

  let parsed;
  try {
    parsed = new URL(raw);
  } catch {
    throw guardError(
      "SYNTHETIC_TARGET_PARSE_INVALID",
      `${name} failed canonical URL parsing`,
    );
  }
  if (
    parsed.origin !== raw ||
    parsed.protocol !== rawParts.protocol ||
    parsed.hostname !== rawParts.hostname ||
    parsed.port !== rawParts.port ||
    parsed.host !== `${rawParts.hostname}:${rawParts.port}` ||
    !LOOPBACK_HOSTS.includes(parsed.hostname) ||
    parsed.username ||
    parsed.password ||
    parsed.pathname !== "/" ||
    parsed.search ||
    parsed.hash
  )
    throw guardError(
      "SYNTHETIC_TARGET_CANONICAL_INVARIANT_FAILED",
      `${name} changed during canonical URL parsing`,
    );
  return { name, origin: raw };
}

export function requireSyntheticMutationGuard(options) {
  const fields = readJsonRecord(options, {
    allowedKeys: GUARD_OPTION_KEYS,
    requiredKeys: REQUIRED_GUARD_OPTION_KEYS,
    codePrefix: "SYNTHETIC_GUARD",
    subject: "synthetic mutation guard",
  });
  const {
    allowSyntheticMutations,
    teacherEmail,
    targets,
    composeProject,
    runPrefix,
    markerSuffix,
  } = fields;
  if (allowSyntheticMutations !== "1")
    throw guardError(
      "SYNTHETIC_MUTATIONS_NOT_ALLOWED",
      'ALLOW_SYNTHETIC_MUTATIONS must be exactly "1" before synthetic browser mutations',
    );
  const email = requireExactString("synthetic teacher email", teacherEmail);
  if (!SYNTHETIC_EMAIL_PATTERN.test(email))
    throw guardError(
      "SYNTHETIC_TEACHER_EMAIL_INVALID",
      "synthetic teacher email must use the allowed *.synthetic.invalid test domain",
    );
  const targetRecords = readJsonTargets(targets);
  const validatedTargets = targetRecords.map((target) =>
    requireLocalTestTarget(target),
  );
  const targetNames = new Set();
  for (const validated of validatedTargets) {
    if (targetNames.has(validated.name))
      throw guardError(
        "SYNTHETIC_TARGET_NAME_DUPLICATE",
        "synthetic target names must be unique",
      );
    targetNames.add(validated.name);
  }
  const origins = Object.create(null);
  for (const validated of validatedTargets) {
    origins[validated.name] = validated.origin;
  }

  let project = null;
  let run = null;
  let suffix = null;
  const hasBusinessIdentity =
    composeProject !== undefined ||
    runPrefix !== undefined ||
    markerSuffix !== undefined;
  if (hasBusinessIdentity) {
    project = requireExactString(
      "BUSINESS_E2E_COMPOSE_PROJECT",
      composeProject,
    );
    run = requireExactString("BUSINESS_E2E_RUN_PREFIX", runPrefix);
    suffix = requireExactString("BUSINESS_E2E_MARKER_SUFFIX", markerSuffix);
    if (
      project.includes("user-test") ||
      !SYNTHETIC_PROJECT_PATTERN.test(project)
    )
      throw new Error(
        "BUSINESS_E2E_COMPOSE_PROJECT must identify an allowed synthetic project",
      );
    if (!SYNTHETIC_RUN_PATTERN.test(run))
      throw new Error(
        "BUSINESS_E2E_RUN_PREFIX must identify an allowed synthetic run",
      );
    if (!SYNTHETIC_MARKER_SUFFIX_PATTERN.test(suffix))
      throw new Error(
        "BUSINESS_E2E_MARKER_SUFFIX must use the synthetic.invalid test suffix",
      );
  }

  return Object.freeze({
    teacherEmail: email,
    origins: Object.freeze(origins),
    evidence: Object.freeze({
      policy: "synthetic-browser-mutation-v1",
      local_origins: Object.freeze(origins),
      compose_project: project,
      run_prefix: run,
      marker_suffix: suffix,
    }),
  });
}
import { types as utilTypes } from "node:util";
