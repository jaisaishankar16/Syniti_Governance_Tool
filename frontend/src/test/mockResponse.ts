export function mockJsonResponse(
  data: unknown,
  status = 200,
  headers: Record<string, string> = {}
): Promise<Response> {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({
      'Content-Type': 'application/json',
      'X-Request-ID': 'req-mock-id',
      ...headers,
    }),
    json: async () => data,
    text: async () => (typeof data === 'string' ? data : JSON.stringify(data)),
  } as unknown as Response);
}
