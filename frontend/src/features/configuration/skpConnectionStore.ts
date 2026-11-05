import { z } from 'zod';

const STORAGE_KEY = 'syniti_skp_connection_v1';

export const SkpConnectionPayloadSchema = z.object({
  version: z.literal(1),
  skpUrl: z.string().max(2048),
  savedAt: z.string(),
});
export type SkpConnectionPayload = z.infer<typeof SkpConnectionPayloadSchema>;

export interface SkpValidationResult {
  valid: boolean;
  normalizedUrl?: string;
  error?: string;
}

export function validateSkpUrl(input: string): SkpValidationResult {
  const trimmed = input.trim();
  if (!trimmed) {
    return { valid: false, error: 'SKP URL is required.' };
  }
  if (trimmed.length > 2048) {
    return { valid: false, error: 'SKP URL cannot exceed 2,048 characters.' };
  }
  if (/\s/.test(trimmed)) {
    return { valid: false, error: 'SKP URL cannot contain whitespace or spaces.' };
  }

  let urlObj: URL;
  try {
    urlObj = new URL(trimmed);
  } catch {
    return { valid: false, error: 'Please enter a valid URL (e.g. https://skp.example.com).' };
  }

  if (urlObj.protocol !== 'http:' && urlObj.protocol !== 'https:') {
    return { valid: false, error: 'URL protocol must be http:// or https://.' };
  }

  if (urlObj.username || urlObj.password) {
    return { valid: false, error: 'Embedded credentials (user:pass@) are not allowed.' };
  }

  const hostname = urlObj.hostname.toLowerCase();
  const isLocal = hostname === 'localhost' || hostname === '127.0.0.1' || hostname === '::1';

  if (urlObj.protocol === 'http:' && !isLocal) {
    return {
      valid: false,
      error:
        'HTTPS is required for remote endpoints (HTTP allowed only for localhost / 127.0.0.1).',
    };
  }

  // Normalize: remove trailing slash from pathname
  let normalized = urlObj.origin + urlObj.pathname.replace(/\/+$/, '');
  if (urlObj.search) normalized += urlObj.search;

  return { valid: true, normalizedUrl: normalized };
}

export const skpConnectionStore = {
  getSkpUrl(): string | null {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return null;
      const parsed = JSON.parse(raw);
      const validated = SkpConnectionPayloadSchema.safeParse(parsed);
      if (validated.success) {
        return validated.data.skpUrl;
      }
      return null;
    } catch {
      return null;
    }
  },

  saveSkpUrl(url: string): string {
    const check = validateSkpUrl(url);
    if (!check.valid || !check.normalizedUrl) {
      throw new Error(check.error || 'Invalid URL');
    }

    const payload: SkpConnectionPayload = {
      version: 1,
      skpUrl: check.normalizedUrl,
      savedAt: new Date().toISOString(),
    };

    localStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
    return check.normalizedUrl;
  },

  reset(): void {
    localStorage.removeItem(STORAGE_KEY);
  },
};
