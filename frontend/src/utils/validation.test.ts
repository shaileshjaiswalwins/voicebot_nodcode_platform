import { isValidUrl } from './validation';

describe('isValidUrl', () => {
  it('accepts empty string as valid (optional field)', () => {
    expect(isValidUrl('', ['http:', 'https:'])).toBe(true);
  });

  it('accepts a well-formed http/https URL', () => {
    expect(isValidUrl('http://192.168.8.67:8000', ['http:', 'https:'])).toBe(true);
    expect(isValidUrl('https://example.com/path', ['http:', 'https:'])).toBe(true);
  });

  it('accepts ws/wss URLs when allowed', () => {
    expect(isValidUrl('wss://livekit.example.com', ['ws:', 'wss:'])).toBe(true);
  });

  it('rejects garbage input', () => {
    expect(isValidUrl('not a url', ['http:', 'https:'])).toBe(false);
    expect(isValidUrl('htp:/broken', ['http:', 'https:'])).toBe(false);
  });

  it('rejects a URL with a disallowed protocol', () => {
    expect(isValidUrl('ftp://example.com', ['http:', 'https:'])).toBe(false);
  });
});
