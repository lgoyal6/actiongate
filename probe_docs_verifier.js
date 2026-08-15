/*
 * Why this receiver verifies raw bytes instead of JSON.stringify(req.body).
 *
 * Circleback's docs (support.circleback.ai article 11014015, read 2026-08-14)
 * give this TypeScript sample for verifying the x-signature header:
 *
 *     verifyWebhookSignature(JSON.stringify(requestBody), signature, signingSecret)
 *
 * where requestBody is req.body, i.e. already parsed by express.json().  A sender
 * can only sign the bytes it actually puts on the wire, so the receiver has to
 * hash those same bytes.  JSON.parse -> JSON.stringify is not byte-preserving,
 * so the sample rejects requests that are perfectly legitimate.
 *
 * Run:  node probe_docs_verifier.js
 */
const crypto = require('crypto');
const SECRET = 'whsec_test_0123456789';

// Verbatim from the docs.
const verifyWebhookSignature = (requestBody, signature, signingSecret) => {
  const expectedSignature = crypto
    .createHmac('sha256', signingSecret)
    .update(requestBody)
    .digest('hex');
  return expectedSignature === signature; // also not constant time
};

// A sender can only sign what it transmits.
const sign = (rawBody) =>
  crypto.createHmac('sha256', SECRET).update(rawBody).digest('hex');

// The documented receiver: parse, then re-serialize, then hash.
const docsReceiver = (rawBody, sig) =>
  verifyWebhookSignature(JSON.stringify(JSON.parse(rawBody)), sig, SECRET);

// This receiver: hash exactly the bytes received.
const rawReceiver = (rawBody, sig) => verifyWebhookSignature(rawBody, sig, SECRET);

const cases = [
  ['compact ASCII (control)', '{"id":"abc","duration":1306.09}'],
  ['trailing-zero decimal, as in their own example payload', '{"id":"abc","timestamp":800.00}'],
  ['escaped non-ASCII \\u00e9 (100+ languages supported)', '{"id":"abc","name":"Caf\\u00e9 review"}'],
  ['emoji as an escaped surrogate pair', '{"id":"abc","notes":"ship it \\ud83d\\ude80"}'],
  ['pretty-printed body', '{\n  "id": "abc",\n  "duration": 1306.09\n}'],
  ['exponent notation', '{"id":"abc","duration":1.30609e3}'],
  ['key order that is not insertion order', '{"z":1,"a":2}'],
];

let docsFail = 0;
console.log('docs    raw     case');
console.log('------  ------  ----------------------------------------------------');
for (const [name, raw] of cases) {
  const sig = sign(raw);
  const d = docsReceiver(raw, sig);
  const r = rawReceiver(raw, sig);
  if (!d) docsFail++;
  console.log(
    `${d ? 'ACCEPT' : 'REJECT'}  ${r ? 'ACCEPT' : 'REJECT'}  ${name}`
  );
}
console.log(
  `\n${docsFail} of ${cases.length} legitimately-signed bodies are rejected by the ` +
    `documented verifier.\nAll ${cases.length} are accepted when the raw bytes are hashed.`
);

// The documented example payload is also not parseable as JSON: the id value is
// unquoted (`"id": WRweb12iCBimU6Vo8cD7z`). Copy-pasting it into a test fixture
// fails before you get anywhere near the signature.
try {
  JSON.parse('{\n  "id": WRweb12iCBimU6Vo8cD7z,\n  "name": "Event Venue Review"\n}');
  console.log('\ndocs example payload parses');
} catch (e) {
  console.log(`\nSeparately, the docs example payload is not valid JSON: ${e.message}`);
}
