import { describe, it, expect, vi } from 'vitest';
import { parseSSEStream } from './sse';
import { NetworkError } from './client';

/**
 * Helper to create a ReadableStream from string chunks
 */
function createStreamFromChunks(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(encoder.encode(chunk));
      }
      controller.close();
    },
  });
}

describe('SSE Parser', () => {
  it('handles standard progress, result, and done events', async () => {
    const rawData =
      'event: progress\ndata: {"stage":"parsing","message":"Parsing query","elapsed_ms":12.5,"detail":{}}\n\n' +
      'event: done\ndata: {}\n\n';

    const stream = createStreamFromChunks([rawData]);
    const events = [];
    for await (const ev of parseSSEStream(stream)) {
      events.push(ev);
    }

    expect(events).toHaveLength(2);
    expect(events[0]).toEqual({
      type: 'progress',
      data: {
        stage: 'parsing',
        message: 'Parsing query',
        elapsed_ms: 12.5,
        detail: {},
      },
    });
    expect(events[1]?.type).toBe('done');
  });

  it('handles chunk splits mid-line and mid-event', async () => {
    // Split the stream across multiple arbitrary boundaries
    const chunks = [
      'event: pro',
      'gress\ndata: {"stage":"guard',
      'rails","message":"Checking input',
      '","elapsed_ms":5.0,"detail":{}}\n\n',
    ];

    const stream = createStreamFromChunks(chunks);
    const events = [];
    for await (const ev of parseSSEStream(stream)) {
      events.push(ev);
    }

    expect(events).toHaveLength(1);
    expect(events[0]?.type).toBe('progress');
    if (events[0]?.type === 'progress') {
      expect(events[0].data.stage).toBe('guardrails');
      expect(events[0].data.message).toBe('Checking input');
    }
  });

  it('handles CRLF line endings', async () => {
    const rawData =
      'event: progress\r\ndata: {"stage":"synthesizing","message":"Synthesizing","elapsed_ms":20.0,"detail":{}}\r\n\r\n';

    const stream = createStreamFromChunks([rawData]);
    const events = [];
    for await (const ev of parseSSEStream(stream)) {
      events.push(ev);
    }

    expect(events).toHaveLength(1);
    expect(events[0]?.type).toBe('progress');
  });

  it('handles comment heartbeat lines and triggers callback', async () => {
    const onHeartbeat = vi.fn();
    const rawData =
      ': heartbeat\n\n' +
      'event: progress\ndata: {"stage":"done","message":"Complete","elapsed_ms":40.0,"detail":{}}\n\n';

    const stream = createStreamFromChunks([rawData]);
    const events = [];
    for await (const ev of parseSSEStream(stream, { onHeartbeat })) {
      events.push(ev);
    }

    expect(onHeartbeat).toHaveBeenCalled();
    expect(events).toHaveLength(1);
  });

  it('handles multi-line data', async () => {
    // When JSON is formatted with newlines in data lines
    const rawData =
      'event: progress\n' +
      'data: {\n' +
      'data:   "stage": "validating",\n' +
      'data:   "message": "Validating output",\n' +
      'data:   "elapsed_ms": 30.5,\n' +
      'data:   "detail": {}\n' +
      'data: }\n\n';

    const stream = createStreamFromChunks([rawData]);
    const events = [];
    for await (const ev of parseSSEStream(stream)) {
      events.push(ev);
    }

    expect(events).toHaveLength(1);
    if (events[0]?.type === 'progress') {
      expect(events[0].data.stage).toBe('validating');
    }
  });

  it('silently ignores unknown events', async () => {
    const rawData =
      'event: ping\ndata: {"time":123}\n\n' +
      'event: progress\ndata: {"stage":"done","message":"Done","elapsed_ms":10.0,"detail":{}}\n\n';

    const stream = createStreamFromChunks([rawData]);
    const events = [];
    for await (const ev of parseSSEStream(stream)) {
      events.push(ev);
    }

    expect(events).toHaveLength(1);
    expect(events[0]?.type).toBe('progress');
  });

  it('throws on malformed JSON payload in known events', async () => {
    const rawData = 'event: progress\ndata: {unclosed json...\n\n';
    const stream = createStreamFromChunks([rawData]);

    await expect(async () => {
      for await (const _ of parseSSEStream(stream)) {
        void _;
      }
    }).rejects.toThrow(/Malformed JSON in progress event/);
  });

  it('cancels when abort signal is triggered', async () => {
    const controller = new AbortController();
    const stream = new ReadableStream({
      start(c) {
        c.enqueue(
          new TextEncoder().encode(
            'event: progress\ndata: {"stage":"parsing","message":"Parsing","elapsed_ms":1.0,"detail":{}}\n\n'
          )
        );
      },
    });

    controller.abort('Cancelled by user');

    await expect(async () => {
      for await (const _ of parseSSEStream(stream, { signal: controller.signal })) {
        void _;
      }
    }).rejects.toThrow('Cancelled by user');
  });

  it('triggers idle timeout if no bytes received within threshold', async () => {
    vi.useFakeTimers();
    // A stream that never emits
    const stream = new ReadableStream({
      start() {
        // do not enqueue anything
      },
    });

    const promise = (async () => {
      for await (const _ of parseSSEStream(stream, { idleTimeoutMs: 1000 })) {
        void _;
      }
    })();

    vi.advanceTimersByTime(1500);

    await expect(promise).rejects.toThrow(NetworkError);
    vi.useRealTimers();
  });
});
