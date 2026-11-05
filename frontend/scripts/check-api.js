import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import { generateApi } from './gen-api.js';

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  try {
    const { schemaGenPath, schemaContent, limitsPath, limitsContent } = await generateApi();

    if (!fs.existsSync(schemaGenPath)) {
      console.error(`Missing file: ${schemaGenPath}. Run 'npm run gen:api'.`);
      process.exit(1);
    }
    if (!fs.existsSync(limitsPath)) {
      console.error(`Missing file: ${limitsPath}. Run 'npm run gen:api'.`);
      process.exit(1);
    }

    const currentSchema = fs.readFileSync(schemaGenPath, 'utf-8');
    const currentLimits = fs.readFileSync(limitsPath, 'utf-8');

    // Normalize line endings for cross-platform comparison
    const norm = (s) => s.replace(/\r\n/g, '\n').trim();

    if (norm(currentSchema) !== norm(schemaContent)) {
      console.error(`Stale schema detected in ${schemaGenPath}. Run 'npm run gen:api'.`);
      process.exit(1);
    }

    if (norm(currentLimits) !== norm(limitsContent)) {
      console.error(`Stale limits detected in ${limitsPath}. Run 'npm run gen:api'.`);
      process.exit(1);
    }

    console.log('check:api passed: generated API files are up to date with docs/openapi.json.');
  } catch (err) {
    console.error('Error during check:api:', err);
    process.exit(1);
  }
}

