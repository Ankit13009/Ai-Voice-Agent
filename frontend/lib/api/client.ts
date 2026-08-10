/**
 * Typed HTTP client.
 *
 * Everything the app knows about talking to the API goes through here, which
 * buys three things no per-component `fetch` can:
 *
 * 1. The envelope is unwrapped in exactly one place. Callers receive `T`, or an
 *    `ApiError` is thrown. No component ever touches `response.success`.
 * 2. Expired access tokens are refreshed transparently, once, with concurrent
 *    requests sharing a single refresh rather than each firing their own.
 * 3. Errors arrive as one class carrying the backend's code, message, field
 *    details, and request id, so error handling is uniform.
 */

import type {
  ApiErrorCode,
  ApiFieldError,
  ApiResponse,
  Paginated,
  PaginationMeta,
  TokenPair,
} from "@/types/api";

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "") ?? "http://localhost:8000";

const ACCESS_TOKEN_KEY = "cr.access_token";
const REFRESH_TOKEN_KEY = "cr.refresh_token";

/**
 * The one error type the app handles.
 *
 * `message` is always safe to show a user: the backend guarantees it, and never
 * puts internal detail there.
 */
export class ApiError extends Error {
  readonly code: ApiErrorCode;
  readonly status: number;
  readonly details: ApiFieldError[];
  readonly requestId: string;

  constructor(
    code: ApiErrorCode,
    message: string,
    status: number,
    details: ApiFieldError[] = [],
    requestId = "",
  ) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
    this.details = details;
    this.requestId = requestId;
  }

  /** Field errors keyed by field name, for inline form validation. */
  get fieldErrors(): Record<string, string> {
    return Object.fromEntries(this.details.map((d) => [d.field, d.message]));
  }

  get isAuthError(): boolean {
    return (
      this.code === "UNAUTHENTICATED" ||
      this.code === "TOKEN_EXPIRED" ||
      this.code === "TOKEN_INVALID"
    );
  }

  /** True when the cause is a missing integration the business must connect. */
  get isIntegrationError(): boolean {
    return this.code === "INTEGRATION_NOT_CONFIGURED";
  }
}

// --------------------------------------------------------------------------- //
// Token storage
// --------------------------------------------------------------------------- //
export const tokenStore = {
  get access(): string | null {
    if (typeof window === "undefined") return null;
    return window.localStorage.getItem(ACCESS_TOKEN_KEY);
  },
  get refresh(): string | null {
    if (typeof window === "undefined") return null;
    return window.localStorage.getItem(REFRESH_TOKEN_KEY);
  },
  set(tokens: TokenPair) {
    if (typeof window === "undefined") return;
    window.localStorage.setItem(ACCESS_TOKEN_KEY, tokens.access_token);
    window.localStorage.setItem(REFRESH_TOKEN_KEY, tokens.refresh_token);
  },
  clear() {
    if (typeof window === "undefined") return;
    window.localStorage.removeItem(ACCESS_TOKEN_KEY);
    window.localStorage.removeItem(REFRESH_TOKEN_KEY);
  },
};

// --------------------------------------------------------------------------- //
// Refresh coordination
// --------------------------------------------------------------------------- //
/**
 * Shared in-flight refresh.
 *
 * A dashboard page fires several requests at once. If the access token has
 * expired they would all 401 together and each start its own refresh; because
 * refresh tokens are single-use and rotated, the second one to land would be
 * treated as a replayed token and log the user out. Holding one promise means
 * they all wait on the same refresh.
 */
let refreshInFlight: Promise<boolean> | null = null;

async function refreshTokens(): Promise<boolean> {
  const refreshToken = tokenStore.refresh;
  if (!refreshToken) return false;

  try {
    const response = await fetch(`${API_BASE}/api/v1/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });
    const body = (await response.json()) as ApiResponse<{ tokens: TokenPair }>;
    if (!response.ok || !body.success) {
      tokenStore.clear();
      return false;
    }
    tokenStore.set(body.data.tokens);
    return true;
  } catch {
    tokenStore.clear();
    return false;
  }
}

function ensureRefresh(): Promise<boolean> {
  if (!refreshInFlight) {
    refreshInFlight = refreshTokens().finally(() => {
      refreshInFlight = null;
    });
  }
  return refreshInFlight;
}

/** Called when a session ends unrecoverably, so the app can route to /login. */
type SessionExpiredHandler = () => void;
let onSessionExpired: SessionExpiredHandler | null = null;

export function setSessionExpiredHandler(handler: SessionExpiredHandler | null) {
  onSessionExpired = handler;
}

// --------------------------------------------------------------------------- //
// Request
// --------------------------------------------------------------------------- //
export type QueryParams = Record<
  string,
  string | number | boolean | null | undefined
>;

function buildUrl(path: string, params?: QueryParams): string {
  const url = new URL(`${API_BASE}${path}`);
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      // Skip empty values so an untouched filter does not become `?status=`.
      if (value === undefined || value === null || value === "") continue;
      url.searchParams.set(key, String(value));
    }
  }
  return url.toString();
}

interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  params?: QueryParams;
  /** Set for login/refresh, which must not attempt a token refresh on 401. */
  skipAuth?: boolean;
  signal?: AbortSignal;
}

async function rawRequest<T>(
  path: string,
  options: RequestOptions,
  isRetry = false,
): Promise<ApiSuccess<T>> {
  const { method = "GET", body, params, skipAuth = false, signal } = options;

  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";

  const token = tokenStore.access;
  if (token && !skipAuth) headers["Authorization"] = `Bearer ${token}`;

  let response: Response;
  try {
    response = await fetch(buildUrl(path, params), {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    });
  } catch (error) {
    // A network failure has no envelope, so synthesize one rather than letting
    // a raw TypeError reach a component.
    if ((error as Error)?.name === "AbortError") throw error;
    throw new ApiError(
      "SERVICE_UNAVAILABLE",
      "Could not reach the server. Check your connection and try again.",
      0,
    );
  }

  // 204 and other empty bodies still need to satisfy the caller's type.
  const text = await response.text();
  let payload: ApiResponse<T>;
  try {
    payload = text
      ? (JSON.parse(text) as ApiResponse<T>)
      : ({ success: true, data: null, meta: null, message: null, request_id: "", timestamp: "" } as ApiResponse<T>);
  } catch {
    throw new ApiError(
      "INTERNAL_ERROR",
      "The server returned an unreadable response.",
      response.status,
    );
  }

  if (payload.success) {
    return { data: payload.data, meta: payload.meta, message: payload.message };
  }

  const { code, message, details } = payload.error;

  // One transparent refresh-and-retry on an expired token.
  const canRetry =
    !isRetry &&
    !skipAuth &&
    (code === "TOKEN_EXPIRED" || code === "UNAUTHENTICATED") &&
    Boolean(tokenStore.refresh);

  if (canRetry) {
    const refreshed = await ensureRefresh();
    if (refreshed) return rawRequest<T>(path, options, true);
    onSessionExpired?.();
  } else if (code === "TOKEN_INVALID" || (isRetry && code === "UNAUTHENTICATED")) {
    tokenStore.clear();
    onSessionExpired?.();
  }

  throw new ApiError(code, message, response.status, details, payload.request_id);
}

interface ApiSuccess<T> {
  data: T;
  meta: PaginationMeta | null;
  message: string | null;
}

/** Request returning just the payload. Use for single objects. */
export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const result = await rawRequest<T>(path, options);
  return result.data;
}

/** Request returning the payload plus the server's message (for toasts). */
export async function requestWithMessage<T>(
  path: string,
  options: RequestOptions = {},
): Promise<{ data: T; message: string | null }> {
  const result = await rawRequest<T>(path, options);
  return { data: result.data, message: result.message };
}

/**
 * Request for a paginated list. Returns items and meta together, so a caller
 * never has to remember that `meta` lives outside `data`.
 */
export async function requestList<T>(
  path: string,
  options: RequestOptions = {},
): Promise<Paginated<T>> {
  const result = await rawRequest<T[]>(path, options);
  const items = result.data ?? [];
  return {
    items,
    meta:
      result.meta ?? {
        page: 1,
        page_size: items.length,
        total: items.length,
        total_pages: 1,
        has_next: false,
        has_prev: false,
      },
  };
}

export const api = {
  get: <T>(path: string, params?: QueryParams, signal?: AbortSignal) =>
    request<T>(path, { method: "GET", params, signal }),
  list: <T>(path: string, params?: QueryParams, signal?: AbortSignal) =>
    requestList<T>(path, { method: "GET", params, signal }),
  post: <T>(path: string, body?: unknown, params?: QueryParams) =>
    requestWithMessage<T>(path, { method: "POST", body, params }),
  patch: <T>(path: string, body?: unknown, params?: QueryParams) =>
    requestWithMessage<T>(path, { method: "PATCH", body, params }),
  put: <T>(path: string, body?: unknown, params?: QueryParams) =>
    requestWithMessage<T>(path, { method: "PUT", body, params }),
  delete: <T>(path: string, params?: QueryParams) =>
    requestWithMessage<T>(path, { method: "DELETE", params }),
};

export { API_BASE };
