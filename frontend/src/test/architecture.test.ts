import { describe, it, expect } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

function getFilesRecursively(dir: string): string[] {
  let results: string[] = [];
  const list = fs.readdirSync(dir);
  for (const file of list) {
    const fullPath = path.join(dir, file);
    const stat = fs.statSync(fullPath);
    if (stat && stat.isDirectory()) {
      results = results.concat(getFilesRecursively(fullPath));
    } else {
      results.push(fullPath);
    }
  }
  return results;
}

describe('Architecture & Project Rules Enforcement', () => {
  it('enforces that ESLint sets react/no-danger to error', () => {
    const eslintConfigPath = path.resolve(__dirname, '../../.eslintrc.cjs');
    expect(fs.existsSync(eslintConfigPath)).toBe(true);
    const content = fs.readFileSync(eslintConfigPath, 'utf-8');
    expect(content).toContain("'react/no-danger': 'error'");
  });

  it('forbids production source code from importing MSW or test fixtures', () => {
    const srcDir = path.resolve(__dirname, '../');
    const allFiles = getFilesRecursively(srcDir);

    const prodFiles = allFiles.filter((filePath) => {
      // Exclude test directories and test files
      const normalized = filePath.replace(/\\/g, '/');
      if (normalized.includes('/src/test/')) return false;
      if (normalized.endsWith('.test.ts') || normalized.endsWith('.test.tsx')) return false;
      if (normalized.endsWith('.spec.ts') || normalized.endsWith('.spec.tsx')) return false;
      return normalized.endsWith('.ts') || normalized.endsWith('.tsx');
    });

    expect(prodFiles.length).toBeGreaterThan(5);

    const forbiddenImports = ['msw', '/test/', '@/test/'];
    const forbiddenPatterns = [
      /\bmockAnalysis\b/,
      /\bcannedResponse\b/,
      /\bofflineResult\b/,
      /\bdemoData\b/,
      /\bfakeAnalysis\b/,
      /\bofflineMode\b/,
    ];

    for (const file of prodFiles) {
      const code = fs.readFileSync(file, 'utf-8');

      // Check imports
      for (const forbidden of forbiddenImports) {
        const importRegex = new RegExp(`from\\s+['"][^'"]*${forbidden}[^'"]*['"]`, 'i');
        expect(
          importRegex.test(code),
          `Production file ${file} imports test double or fixture: ${forbidden}`
        ).toBe(false);
      }

      // Check forbidden identifiers
      for (const pattern of forbiddenPatterns) {
        expect(
          pattern.test(code),
          `Production file ${file} contains forbidden mock/offline identifier: ${pattern}`
        ).toBe(false);
      }
    }
  });
});
