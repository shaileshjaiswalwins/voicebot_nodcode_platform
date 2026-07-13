export function isValidUrl(value: string, allowedProtocols: string[]): boolean {
  if (!value.trim()) return true;
  try {
    const url = new URL(value);
    return allowedProtocols.includes(url.protocol);
  } catch {
    return false;
  }
}
