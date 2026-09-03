// Imported with its extension because `node --test` loads this file as an
// ES module, where an extensionless specifier does not resolve. The bundler
// is happy either way; the test runner is not.
import { parseCookies, readSetCookie, withScheme } from "./http.ts";
import { type ServerEvent, SseParser } from "./sse.ts";
import type { ToolDefinition } from "./tools.ts";
import type { Content, CredentialStatus, GoogleAuthStart, SearchResponse } from "./types";

/**
 * The HTTP side of the extension.
 *
 * **Why a session and not a token.** The backend has two authentication
 * mechanisms and they are deliberately separate: `/v1/*` takes a bearer API
 * key, and `/api/*` — search, content, sources — takes a cookie session. This
 * extension wants `/api/*`, so it signs in the way the web app does rather
 * than asking the backend to grow a third way in. Nothing here needs a change
 * on the server.
 *
 * That costs two things a browser would handle by itself, and both are done
 * below: cookies are kept by hand, because `fetch` in the extension host has
 * no jar, and the CSRF token is echoed back in a header, because the API's
 * double-submit check requires it on anything that is not a GET.
 *
 * The password is never held here. It lives in the editor's secret storage
 * and is read only when a session has to be established.
 */

export class AuthError extends Error {}
export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

const SESSION_COOKIE = "kb_session";
const CSRF_COOKIE = "kb_csrf";
const KEPT_COOKIES = [SESSION_COOKIE, CSRF_COOKIE] as const;
const PASSWORD_KEY = "knowledgeBase.password";
const ACCESS_KEY = "knowledgeBase.accessKey";

interface Credentials {
  username: string;
  password: string;
}

interface RequestOptions {
  accept?: string;
  signal?: AbortSignal;
  /** An access key, sent instead of the session cookie. */
  bearer?: string;
}

/**
 * What this needs of `vscode.SecretStorage`, which the real one satisfies.
 *
 * Named structurally rather than imported so this module pulls in nothing
 * from the editor at all - which is what lets the test below drive it against
 * a stub of the API instead of only being exercised by hand.
 */
export interface SecretStore {
  get(key: string): Thenable<string | undefined>;
  store(key: string, value: string): Thenable<void>;
  delete(key: string): Thenable<void>;
}

export interface ChatMessage {
  role: "system" | "user" | "assistant" | "tool";
  content?: string;
  tool_call_id?: string;
  tool_calls?: unknown[];
}

export class KnowledgeBaseClient {
  /** name -> value. Only the two cookies the API sets are ever kept. */
  private cookies = new Map<string, string>();

  // Fields are declared and assigned rather than written as constructor
  // parameter properties. That is the one TypeScript convenience this file
  // gives up, and it buys the integration test: `node --test` strips types
  // rather than compiling them, and a parameter property has to emit code.
  private readonly secrets: SecretStore;
  private readonly settings: () => { baseUrl: string; username: string };

  constructor(secrets: SecretStore, settings: () => { baseUrl: string; username: string }) {
    this.secrets = secrets;
    this.settings = settings;
  }

  get signedIn(): boolean {
    return this.cookies.has(SESSION_COOKIE);
  }

  /**
   * Whether a sign-in could be attempted without asking for anything.
   *
   * Read on activation, because a session lives in memory and a restarted
   * editor has none — while the credential that would establish one has been
   * in the keychain since the first sign-in. Without this the view would
   * offer "Sign in" to somebody who already had.
   */
  async hasCredentials(): Promise<boolean> {
    // A key is enough to work: it grants the model, which is what the panel
    // is for. It grants no knowledge-base search, but nagging "sign in" at
    // somebody whose coder runs would be the wrong thing to say.
    if (await this.accessKey()) return true;
    const { username } = this.settings();
    return Boolean(username) && Boolean(await this.secrets.get(PASSWORD_KEY));
  }

  /**
   * An access key for the backend, which is what grants the model.
   *
   * The backend issues these with `manage.py issue-key` and accepts them as
   * `Authorization: Bearer llmhell_...` on its `/v1` routes - which reach the
   * same coding provider the cookie route does. So a key alone is enough to
   * run the agent, with no username and no password stored anywhere.
   *
   * It does **not** cover the knowledge-base routes: `/api/search` and
   * `/api/content` are cookie-only, so the search tools still need a
   * sign-in. Both can be configured at once, and usually are.
   */
  async accessKey(): Promise<string | undefined> {
    return (await this.secrets.get(ACCESS_KEY)) || undefined;
  }

  async setAccessKey(key: string | undefined): Promise<void> {
    if (key) await this.secrets.store(ACCESS_KEY, key);
    else await this.secrets.delete(ACCESS_KEY);
  }

  /** Store a password and prove it works by signing in with it. */
  async signIn(credentials: Credentials): Promise<void> {
    await this.login(credentials);
    await this.secrets.store(PASSWORD_KEY, credentials.password);
  }

  /**
   * Drop the session without logging out.
   *
   * For a change of backend, where `signOut` would be wrong twice over: the
   * logout would go to whichever backend is configured *now* rather than the
   * one the session belongs to, and it would delete the stored password,
   * which is not what changing an address asks for. The cookies have no host
   * attached to them, so leaving them in place would present one backend's
   * session to another.
   */
  forgetSession(): void {
    this.cookies.clear();
  }

  async signOut(): Promise<void> {
    // Best effort: the local session is dropped either way, so a backend that
    // is down cannot leave the extension believing it is still signed in.
    try {
      await this.send("POST", "/api/auth/logout");
    } catch {
      // Nothing to do. The cookies are cleared below regardless.
    }
    this.cookies.clear();
    await this.secrets.delete(PASSWORD_KEY);
    // The key grants the model on its own, so leaving it behind would mean a
    // panel that still works after saying it signed out.
    await this.secrets.delete(ACCESS_KEY);
  }

  async search(
    query: string,
    options: { sources?: string[]; limit?: number; answer?: boolean },
  ): Promise<SearchResponse> {
    return this.json<SearchResponse>("POST", "/api/search", {
      query,
      sources: options.sources?.length ? options.sources : null,
      limit: options.limit ?? null,
      answer: options.answer ?? true,
    });
  }

  /**
   * OpenAI-compatible chat completions backed by the backend's coding provider.
   *
   * Two routes to the same place. With an access key configured this uses the
   * `/v1` proxy and a bearer header; otherwise the cookie-authenticated
   * `/api/chat/completions`. Both hand the request to the identical provider
   * with the identical model id, so which one is in use changes nothing about
   * the answer - only what had to be typed to get it.
   */
  async *chatCompletionsStream(
    messages: ChatMessage[],
    signal?: AbortSignal,
    tools?: ToolDefinition[],
  ): AsyncGenerator<ServerEvent> {
    const requestBody: Record<string, unknown> = {
      model: "llmhell/coder",
      stream: true,
      messages,
    };
    if (tools?.length) {
      requestBody.tools = tools;
    }
    const key = await this.accessKey();
    const response = key
      ? await this.keyed("POST", "/v1/chat/completions", key, requestBody, {
          accept: "text/event-stream",
          signal,
        })
      : await this.authorised("POST", "/api/chat/completions", requestBody, {
          accept: "text/event-stream",
          signal,
        });

    const body = response.body;
    if (!body) {
      throw new ApiError("The server sent no stream to read.", response.status);
    }

    const reader = body.getReader();
    const decoder = new TextDecoder();
    const parser = new SseParser();
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        for (const event of parser.push(decoder.decode(value, { stream: true }))) {
          yield event;
        }
      }
      for (const event of parser.flush()) {
        yield event;
      }
    } finally {
      reader.releaseLock();
      if (!signal?.aborted) await body.cancel().catch(() => undefined);
    }
  }

  /**
   * The full text behind a result, by its id.
   *
   * Takes the bare id rather than a whole SearchHit, so anything holding on
   * to just the id from an earlier search - a tool result the model is
   * replying to two turns later, say - can still open it.
   */
  async content(id: string): Promise<Content> {
    // The id carries a colon (`google_drive:1AbC…`) and the route matches it
    // as a path, so each segment is encoded but the separators are kept.
    const path = id.split("/").map(encodeURIComponent).join("/");
    return this.json<Content>("GET", `/api/content/${path}`);
  }

  // --- the user's own credentials ------------------------------------------
  //
  // Signing in to this application says who you are. It does not say what
  // you may read: Drive and GitLab are other people's systems and want their
  // own consent. These four are that second conversation.

  /** Connection state per provider. Never carries a secret. */
  async credentials(): Promise<CredentialStatus[]> {
    return this.json<CredentialStatus[]>("GET", "/api/credentials");
  }

  /**
   * Hand over a GitLab token.
   *
   * The backend verifies it against the instance before storing it, so a
   * token with the wrong scopes is refused here, with a reason, rather than
   * three days later as a search that quietly returns nothing.
   */
  async connectGitLab(token: string, apiUrl?: string): Promise<CredentialStatus> {
    return this.json<CredentialStatus>("PUT", "/api/credentials/gitlab", {
      token,
      api_url: apiUrl || null,
    });
  }

  /** Begin Google consent. Returns the URL the user has to visit. */
  async startGoogle(): Promise<GoogleAuthStart> {
    return this.json<GoogleAuthStart>("POST", "/api/credentials/google/start");
  }

  /**
   * Record the consent, once it has actually been given.
   *
   * The backend asks Google's MCP server whether the account is really
   * authenticated rather than taking our word for it, so calling this too
   * early fails loudly instead of leaving a connection that does not work.
   */
  async confirmGoogle(): Promise<CredentialStatus> {
    return this.json<CredentialStatus>("POST", "/api/credentials/google/confirm");
  }

  async disconnect(provider: string): Promise<void> {
    await this.authorised("DELETE", `/api/credentials/${encodeURIComponent(provider)}`);
  }

  // --- the plumbing --------------------------------------------------------

  private async json<T>(method: string, path: string, body?: unknown): Promise<T> {
    const response = await this.authorised(method, path, body);
    return (await response.json()) as T;
  }

  /**
   * One request, signing in first if there is no session and once more if the
   * session turns out to be stale.
   *
   * The retry is the point: sessions expire after a week, and an editor left
   * open across a weekend should not answer a search with "not authenticated"
   * when it has the credentials to fix that itself.
   */
  private async authorised(
    method: string,
    path: string,
    body?: unknown,
    options: RequestOptions = {},
  ): Promise<Response> {
    if (!this.signedIn) {
      await this.loginFromStorage();
    }

    let response = await this.send(method, path, body, options);
    if (response.status === 401) {
      this.cookies.clear();
      await this.loginFromStorage();
      response = await this.send(method, path, body, options);
    }

    if (response.status === 401 || response.status === 403) {
      throw new AuthError(await describe(response));
    }
    if (!response.ok) {
      throw new ApiError(await describe(response), response.status);
    }
    return response;
  }

  private async loginFromStorage(): Promise<void> {
    const { username } = this.settings();
    const password = await this.secrets.get(PASSWORD_KEY);
    if (!username || !password) {
      // Said in full, because there is a configuration in which the panel
      // works and this still fails: an access key grants the model but not
      // the knowledge base, and "Not signed in" alone would look like a bug
      // to somebody whose coder is answering perfectly well.
      throw new AuthError(
        (await this.accessKey())
          ? "The knowledge base needs a sign-in. The access key grants the model, not search."
          : "Not signed in.",
      );
    }
    await this.login({ username, password });
  }

  private async login(credentials: Credentials): Promise<void> {
    this.cookies.clear();
    const response = await this.send("POST", "/api/auth/login", credentials);
    if (response.status === 401) {
      throw new AuthError("Incorrect username or password.");
    }
    if (!response.ok) {
      throw new ApiError(await describe(response), response.status);
    }
    if (!this.signedIn) {
      // A 200 with no cookie means something between here and the API ate it
      // — a proxy, or a `Secure` cookie over plain HTTP. Saying so beats
      // failing on the next request with "not authenticated".
      throw new AuthError("Signed in, but the server set no session cookie.");
    }
  }

  /**
   * A request that carries a bearer key instead of a session.
   *
   * No sign-in to attempt and nothing to retry: a key either works or does
   * not, and re-sending it would only ask the same question twice.
   */
  private async keyed(
    method: string,
    path: string,
    key: string,
    body?: unknown,
    options: RequestOptions = {},
  ): Promise<Response> {
    const response = await this.send(method, path, body, { ...options, bearer: key });
    if (response.status === 401 || response.status === 403) {
      throw new AuthError(`The backend refused the access key: ${await describe(response)}`);
    }
    if (!response.ok) {
      throw new ApiError(await describe(response), response.status);
    }
    return response;
  }

  private async send(
    method: string,
    path: string,
    body?: unknown,
    options: RequestOptions = {},
  ): Promise<Response> {
    const { baseUrl } = this.settings();
    const headers: Record<string, string> = { accept: options.accept ?? "application/json" };

    if (options.bearer) {
      headers.authorization = `Bearer ${options.bearer}`;
    }
    const cookie = this.cookieHeader();
    if (cookie && !options.bearer) {
      headers.cookie = cookie;
    }
    // Double-submit CSRF: the API rejects a cookie-authenticated write unless
    // the readable token comes back in a header. Safe methods are exempt.
    const csrf = this.cookies.get(CSRF_COOKIE);
    if (csrf && method !== "GET") {
      headers["x-csrf-token"] = csrf;
    }
    if (body !== undefined) {
      headers["content-type"] = "application/json";
    }

    let response: Response;
    try {
      response = await fetch(new URL(path, withScheme(baseUrl)), {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: options.signal,
      });
    } catch (error) {
      // An abort is the caller's own doing and must not be dressed up as the
      // backend being unreachable.
      if (options.signal?.aborted) throw error;
      // A refused connection is the single most common failure here and the
      // stack trace says nothing a user can act on.
      throw new ApiError(`Could not reach ${baseUrl}: ${(error as Error).message}`, 0);
    }

    this.remember(response);
    return response;
  }

  private remember(response: Response): void {
    for (const [name, value] of parseCookies(readSetCookie(response), KEPT_COOKIES)) {
      // An empty value is how a server deletes a cookie.
      if (value) this.cookies.set(name, value);
      else this.cookies.delete(name);
    }
  }

  private cookieHeader(): string {
    return [...this.cookies].map(([name, value]) => `${name}=${value}`).join("; ");
  }
}

async function describe(response: Response): Promise<string> {
  // FastAPI puts the reason in `detail`; anything else is shown as it came.
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string") return body.detail;
  } catch {
    // Not JSON. Fall through to the status line.
  }
  return `${response.status} ${response.statusText}`;
}
