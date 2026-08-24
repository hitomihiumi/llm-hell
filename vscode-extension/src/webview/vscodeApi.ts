import type { HostMessage, WebviewMessage } from "../webviewProtocol";

/**
 * The one global a webview's own script is handed: a typed wrapper over
 * `acquireVsCodeApi()`, called exactly once per page load - calling it twice
 * throws, which is why this file owns the single call and everything else
 * imports the wrapper instead.
 */

interface RawVsCodeApi {
  postMessage(message: unknown): void;
  getState(): unknown;
  setState(state: unknown): void;
}

declare function acquireVsCodeApi(): RawVsCodeApi;

export interface VsCodeApi {
  post(message: WebviewMessage): void;
  onMessage(handler: (message: HostMessage) => void): void;
}

export function connectVsCodeApi(): VsCodeApi {
  const raw = acquireVsCodeApi();
  return {
    post: (message) => raw.postMessage(message),
    onMessage: (handler) => {
      window.addEventListener("message", (event: MessageEvent<HostMessage>) => handler(event.data));
    },
  };
}
