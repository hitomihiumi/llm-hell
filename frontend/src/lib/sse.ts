/**
 * Reads an SSE stream from a POST response.
 *
 * `EventSource` cannot be used here: it only issues GETs and cannot set
 * headers, and this endpoint needs a JSON body and the CSRF header. So the
 * stream is read off `fetch`'s ReadableStream and framed by hand.
 */

import { csrfHeaders } from "@/lib/api";
import type { StreamEvent } from "@/lib/types";

export async function* streamSearch(
  body: unknown,
  signal?: AbortSignal,
): AsyncGenerator<StreamEvent> {
  const response = await fetch("/api/search/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...csrfHeaders() },
    body: JSON.stringify(body),
    signal,
  });

  if (response.status === 401) {
    window.location.href = "/login";
    return;
  }
  if (!response.ok || !response.body) {
    yield {
      event: "error",
      data: {
        message: `stream failed with ${response.status}`,
        stage: "transport",
      },
    };
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // Frames are separated by a blank line. Anything after the last one is
      // a partial frame and stays in the buffer.
      const frames = buffer.split("\n\n");
      buffer = frames.pop() ?? "";

      for (const frame of frames) {
        const parsed = parseFrame(frame);
        if (parsed) yield parsed;
      }
    }
  } finally {
    reader.releaseLock();
  }
}

function parseFrame(frame: string): StreamEvent | null {
  let event = "message";
  const dataLines: string[] = [];

  for (const line of frame.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
  }
  if (dataLines.length === 0) return null;

  try {
    return { event, data: JSON.parse(dataLines.join("\n")) } as StreamEvent;
  } catch {
    // A malformed frame should not kill a stream that is otherwise producing
    // a usable answer.
    return null;
  }
}
