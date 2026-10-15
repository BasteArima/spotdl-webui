// Обращения к серверу и вход. Ключ токена общий с прежним интерфейсом —
// вошли в одном, вошли и в другом.

const TOKEN_KEY = "spotdl_webui_token";

function getToken() {
  try { return localStorage.getItem(TOKEN_KEY) || ""; } catch (e) { return ""; }
}
function setToken(t) {
  try {
    if (t) localStorage.setItem(TOKEN_KEY, t);
    else localStorage.removeItem(TOKEN_KEY);
  } catch (e) { /* приватный режим: живём без запоминания */ }
}

// Токен — только в заголовке, никогда в адресе.
function headers(extra) {
  const h = Object.assign({}, extra || {});
  const t = getToken();
  if (t) h.Authorization = "Bearer " + t;
  return h;
}

export class ApiError extends Error {
  constructor(message, status) { super(message); this.status = status; }
}

export async function api(method, path, body) {
  const opts = { method, headers: headers() };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(path, opts);
  } catch (e) {
    throw new ApiError("Сервер недоступен", 0);
  }
  if (res.status === 401) {
    setToken("");
    window.dispatchEvent(new Event("auth:lost"));
    throw new ApiError("Нужно войти заново", 401);
  }
  let data = null;
  try { data = await res.json(); } catch (e) { /* пустой ответ */ }
  if (!res.ok) throw new ApiError((data && data.detail) || `Ошибка ${res.status}`, res.status);
  return data;
}

export async function authStatus() {
  const res = await fetch("/api/auth/status", { headers: headers() });
  return res.json();
}

// mode: "login" | "setup"
export async function signIn(mode, password) {
  const res = await fetch(mode === "setup" ? "/api/auth/setup" : "/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(data.detail || `Ошибка ${res.status}`, res.status);
  setToken(data.token || "");
  return data;
}

export function signOut() {
  setToken("");
  window.dispatchEvent(new Event("auth:lost"));
}

// файлы (cookies.txt, свой трек) — multipart, токен тот же
export async function apiForm(path, formData) {
  let res;
  try {
    res = await fetch(path, { method: "POST", body: formData, headers: headers() });
  } catch (e) {
    throw new ApiError("Сервер недоступен", 0);
  }
  if (res.status === 401) {
    setToken("");
    window.dispatchEvent(new Event("auth:lost"));
    throw new ApiError("Нужно войти заново", 401);
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(data.detail || `Ошибка ${res.status}`, res.status);
  return data;
}

// после смены пароля сервер выдаёт новый токен (старые сессии недействительны)
export function storeToken(t) { setToken(t); }
