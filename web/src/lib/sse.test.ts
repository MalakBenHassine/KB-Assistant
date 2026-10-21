import { describe, expect, it } from "vitest";

import { SseParser, readEvents } from "@/lib/sse";

describe("SseParser", () => {
  it("parses events split across arbitrary chunks", () => {
    const parser = new SseParser();
    const stream = 'event: meta\ndata: {"a":1}\n\nevent: sentence\ndata: {"text":"Hi [S1]."}\n\n';
    const events = [];
    for (let i = 0; i < stream.length; i += 7) events.push(...parser.feed(stream.slice(i, i + 7)));
    expect(events).toEqual([
      { event: "meta", data: '{"a":1}' },
      { event: "sentence", data: '{"text":"Hi [S1]."}' },
    ]);
  });

  it("handles CRLF, comments, multi-line data and the default event name", () => {
    const parser = new SseParser();
    expect(parser.feed(": keep-alive\r\ndata: line one\r\ndata: line two\r\n\r\n")).toEqual([
      { event: "message", data: "line one\nline two" },
    ]);
  });

  it("keeps an incomplete event until it ends", () => {
    const parser = new SseParser();
    expect(parser.feed("event: done\ndata: {}")).toEqual([]);
    expect(parser.feed("\n\n")).toEqual([{ event: "done", data: "{}" }]);
  });
});

describe("readEvents", () => {
  it("reads a byte stream, including a multi-byte character split between chunks", async () => {
    const bytes = new TextEncoder().encode('event: sentence\ndata: {"text":"Café [S1]."}\n\n');
    const split = bytes.indexOf(0xc3) + 1; // inside "é"
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(bytes.slice(0, split));
        controller.enqueue(bytes.slice(split));
        controller.close();
      },
    });
    const events = [];
    for await (const e of readEvents(body)) events.push(e);
    expect(events).toEqual([{ event: "sentence", data: '{"text":"Café [S1]."}' }]);
  });
});
