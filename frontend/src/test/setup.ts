import '@testing-library/jest-dom';
import { afterEach } from 'vitest';
import { cleanup, configure } from '@testing-library/react';

// Increase async utility timeout for slower test environments / full parallel test runs
configure({ asyncUtilTimeout: 10000 });

// Clean up DOM after each test
afterEach(() => {
  cleanup();
});

// Storage polyfill for Node / jsdom
const createStorageMock = () => {
  let store: Record<string, string> = {};
  return {
    getItem: (key: string) => store[key] ?? null,
    setItem: (key: string, value: string) => {
      store[key] = String(value);
    },
    removeItem: (key: string) => {
      delete store[key];
    },
    clear: () => {
      store = {};
    },
    get length() {
      return Object.keys(store).length;
    },
    key: (i: number) => Object.keys(store)[i] ?? null,
  };
};

const localMock = createStorageMock();
const sessionMock = createStorageMock();

try {
  Object.defineProperty(window, 'localStorage', {
    value: localMock,
    writable: true,
    configurable: true,
  });
  Object.defineProperty(global, 'localStorage', {
    value: localMock,
    writable: true,
    configurable: true,
  });
  Object.defineProperty(window, 'sessionStorage', {
    value: sessionMock,
    writable: true,
    configurable: true,
  });
  Object.defineProperty(global, 'sessionStorage', {
    value: sessionMock,
    writable: true,
    configurable: true,
  });
} catch {
  // Ignore if already configured
}

// Polyfill window.matchMedia
if (typeof window !== 'undefined') {
  if (!window.matchMedia) {
    window.matchMedia = (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    });
  }

  // Polyfill ResizeObserver
  if (!window.ResizeObserver) {
    window.ResizeObserver = class ResizeObserver {
      observe() {}
      unobserve() {}
      disconnect() {}
    };
  }

  // Polyfill clipboard
  try {
    Object.defineProperty(navigator, 'clipboard', {
      value: {
        writeText: async () => {},
        readText: async () => '',
      },
      writable: true,
      configurable: true,
    });
  } catch {
    // Ignore
  }

  // CodeMirror / Range polyfills for jsdom
  if (typeof document !== 'undefined') {
    const originalCreateRange = document.createRange ? document.createRange.bind(document) : null;
    document.createRange = () => {
      const range = originalCreateRange ? originalCreateRange() : ({} as Range);
      range.getBoundingClientRect = () => ({
        x: 0,
        y: 0,
        bottom: 0,
        height: 0,
        left: 0,
        right: 0,
        top: 0,
        width: 0,
        toJSON: () => {},
      });
      range.getClientRects = () =>
        ({
          item: () => null,
          length: 0,
          [Symbol.iterator]: function* () {},
        }) as unknown as DOMRectList;
      return range;
    };
  }
}
