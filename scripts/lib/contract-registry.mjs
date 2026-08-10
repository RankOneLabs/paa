/**
 * The one ordered registry of normative PAA contracts. Both
 * generate-paa-contract-artifacts.mjs and validate-paa-contracts.mjs import
 * this list rather than each maintaining their own — a single source
 * prevents the generator and validator from silently covering different
 * contract sets.
 */

import { readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { compile } from 'json-schema-to-typescript';

export const root = resolve(fileURLToPath(import.meta.url), '..', '..', '..');

function contract(id, typeName) {
  return {
    id,
    typeName,
    schemaPath: resolve(root, 'schemas', `${id}.schema.json`),
    tsOutPath: resolve(root, 'src', 'generated', `${id}.ts`),
    publicSchemaPath: resolve(root, 'public', `${id}.schema.json`),
  };
}

export const CONTRACTS = [
  contract('paa-task', 'PAATaskDeclaration'),
  contract('paa-evidence-record', 'PAAEvidenceRecord'),
  contract('paa-decision-artifact', 'PAADecisionArtifact'),
  contract('paa-autonomy-event', 'PAAAutonomyEvent'),
];

const COMPILE_OPTIONS = {
  additionalProperties: false,
  enableConstEnums: true,
  strictIndexSignatures: true,
  style: { singleQuote: true, semi: true },
};

export function bannerFor(contractEntry) {
  return (
    `/**\n * Generated from schemas/${contractEntry.id}.schema.json — DO NOT EDIT.\n` +
    ' * Run `npm run contracts:generate` to regenerate.\n */'
  );
}

export function loadSchemaSource(contractEntry) {
  const source = readFileSync(contractEntry.schemaPath, 'utf8');
  return { source, schema: JSON.parse(source) };
}

export async function compileContractTs(contractEntry, schema) {
  return compile(schema, contractEntry.typeName, {
    bannerComment: bannerFor(contractEntry),
    ...COMPILE_OPTIONS,
  });
}

export function writeContractArtifacts(contractEntry, schemaSource, generatedTs) {
  writeFileSync(contractEntry.tsOutPath, generatedTs, 'utf8');
  writeFileSync(contractEntry.publicSchemaPath, schemaSource, 'utf8');
}

export function readExisting(path) {
  try {
    return readFileSync(path, 'utf8');
  } catch {
    return null;
  }
}
