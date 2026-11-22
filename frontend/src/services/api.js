const BASE = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000';

async function request(path, options = {}) {
  let response;
  try {
    response = await fetch(`${BASE}${path}`, {
      ...options,
      headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    });
  } catch {
    throw new Error(`Cannot reach the Python server at ${BASE}. Run: python3 -m uvicorn api.server:app --reload`);
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = body.detail;
    throw new Error(typeof detail === 'string' ? detail : detail?.message || JSON.stringify(detail || body) || `HTTP ${response.status}`);
  }
  return body;
}

const sessionPath = (id) => `/games/${encodeURIComponent(id)}`;
export const api = {
  health: () => request('/health'),
  characters: () => request('/characters'),
  rooms: () => request('/rooms'),
  saves: () => request('/games'),
  start: (character, room_id) => request('/games', { method: 'POST', body: JSON.stringify({ character, room_id }) }),
  state: (id) => request(sessionPath(id)),
  action: (id, payload) => request(`${sessionPath(id)}/actions`, { method: 'POST', body: JSON.stringify(payload) }),
  reachable: (id, actorId) => request(`${sessionPath(id)}/reachable?actor_id=${encodeURIComponent(actorId)}`),
  save: (id) => request(`${sessionPath(id)}/save`, { method: 'POST' }),
  load: (id) => request(`${sessionPath(id)}/load`, { method: 'POST' }),
};

export function actionRequest(actorId, actions, rawText = '') {
  return { schema_version: '1.0.0', actor_id: actorId, raw_text: rawText, confidence: 1, actions };
}