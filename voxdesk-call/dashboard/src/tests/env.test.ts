import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const runtime = window as unknown as { __ENV__?: Record<string, string> };

describe('production environment validation', () => {
  beforeEach(() => {
    vi.resetModules();
    runtime.__ENV__ = {
      VITE_APP_ENV: 'production',
      VITE_API_BASE_URL: '/api',
      VITE_PUBLIC_BASE_URL: 'https://app.example.com',
    };
  });

  afterEach(() => {
    delete runtime.__ENV__;
  });

  it('allows a same-origin API path with an HTTPS public origin', async () => {
    runtime.__ENV__!.VITE_API_BASE_URL = '/api/';
    const { getEnv } = await import('../config/env');

    expect(getEnv().apiBaseUrl).toBe('/api');
    expect(getEnv().publicBaseUrl).toBe('https://app.example.com');
  });

  it('refuses an insecure absolute API URL in production', async () => {
    runtime.__ENV__!.VITE_API_BASE_URL = 'http://api.example.com';
    const { getEnv } = await import('../config/env');

    expect(() => getEnv()).toThrow('VITE_API_BASE_URL must use HTTPS in production');
  });

  it('refuses an insecure public origin in production', async () => {
    runtime.__ENV__!.VITE_PUBLIC_BASE_URL = 'http://app.example.com';
    const { getEnv } = await import('../config/env');

    expect(() => getEnv()).toThrow('VITE_PUBLIC_BASE_URL must use HTTPS in production');
  });
});
