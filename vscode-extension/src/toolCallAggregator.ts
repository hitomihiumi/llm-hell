import type { ToolCall } from "./tools";

/**
 * Reassembling a streamed tool call from its deltas.
 *
 * An OpenAI-shaped stream sends a tool call in pieces: the id and the name
 * arrive once, the arguments arrive as a run of JSON fragments, and every
 * piece is keyed by `index` rather than by the call itself. Both `@coder`
 * (the native chat participant) and the custom chat panel drive an identical
 * loop against the same event shape, so this is the one place that knows how
 * to put the pieces back together - shared rather than duplicated, because a
 * fix to one copy silently not reaching the other is exactly the kind of bug
 * that only shows up in whichever path nobody tested that day.
 */
export class ToolCallAggregator {
  private calls = new Map<number, { id?: string; type?: string; name?: string; args: string }>();

  feed(delta: {
    index?: number;
    id?: string;
    type?: string;
    function?: { name?: string; arguments?: string };
  }): void {
    const index = delta.index ?? 0;
    let call = this.calls.get(index);
    if (!call) {
      call = { args: "" };
      this.calls.set(index, call);
    }
    if (delta.id) call.id = delta.id;
    if (delta.type) call.type = delta.type;
    if (delta.function?.name) call.name = delta.function.name;
    if (delta.function?.arguments) call.args += delta.function.arguments;
  }

  /** Calls with everything a real one needs. A fragment still missing its
   * id or name is dropped rather than sent to a tool as half a call. */
  finalize(): ToolCall[] {
    return Array.from(this.calls.values())
      .filter((call): call is { id: string; type: "function"; name: string; args: string } =>
        Boolean(call.id && call.type && call.name),
      )
      .map((call) => ({
        id: call.id,
        type: call.type,
        function: { name: call.name, arguments: call.args },
      }));
  }
}
