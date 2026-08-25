/**
 * Reading the server's event stream.
 *
 * `/api/search/stream` sends results about a second in, then the answer a
 * token at a time — which is the whole reason the chat participant feels like
 * a chat rather than a spinner. Parsing that is small, fiddly and entirely
 * pure, so it lives here where `node --test` can reach it.
 *
 * Chunks arrive on network boundaries, not message boundaries: one read can
 * hold three events, or half of one. The parser therefore keeps a buffer and
 * only emits what is terminated by a blank line.
 */

export interface ServerEvent {
  /** The `event:` name. Defaults to "message", as the spec says. */
  event: string;
  /** The `data:` lines, parsed as JSON when they are JSON. */
  data: unknown;
}

export class SseParser {
  private buffer = "";

  /** The events completed by this chunk. */
  push(chunk: string): ServerEvent[] {
    // Normalised because the spec allows CRLF and a lone CR as line endings,
    // and a proxy is free to rewrite them.
    this.buffer += chunk.replace(/\r\n?/g, "\n");

    const events: ServerEvent[] = [];
    let boundary = this.buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const block = this.buffer.slice(0, boundary);
      this.buffer = this.buffer.slice(boundary + 2);
      const event = parseBlock(block);
      if (event) events.push(event);
      boundary = this.buffer.indexOf("\n\n");
    }
    return events;
  }

  /**
   * Whatever is left when the connection ends.
   *
   * A stream cut off mid-event has nothing usable in the tail, but a server
   * that ends without a trailing blank line has a whole one — and losing the
   * `done` event would leave the chat looking like it never finished.
   */
  flush(): ServerEvent[] {
    const rest = this.buffer.trim();
    this.buffer = "";
    if (!rest) return [];
    const event = parseBlock(rest);
    return event ? [event] : [];
  }
}

function parseBlock(block: string): ServerEvent | undefined {
  let name = "";
  const data: string[] = [];

  for (const line of block.split("\n")) {
    if (!line || line.startsWith(":")) continue; // Blank, or a keep-alive comment.
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    // One optional space after the colon belongs to the framing, not the value.
    const value = colon === -1 ? "" : line.slice(colon + 1).replace(/^ /, "");
    if (field === "event") name = value;
    else if (field === "data") data.push(value);
  }

  if (!data.length) return undefined;
  const raw = data.join("\n");
  try {
    return { event: name || "message", data: JSON.parse(raw) };
  } catch {
    // Not JSON. Handing the text back beats dropping an event whose name may
    // still matter.
    return { event: name || "message", data: raw };
  }
}
