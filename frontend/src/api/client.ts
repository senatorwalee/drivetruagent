/**
 * The only file that talks to the backend.
 *
 * Types come from schema.d.ts, which is generated from the backend's own API
 * description (`make gen-api`). If an endpoint changes shape, this file stops
 * compiling instead of failing at runtime.
 */
import type { components } from './schema'

type Schemas = components['schemas']

export type AgentInfo = Schemas['AgentInfoResponse']
export type Cart = Schemas['CartSnapshot']
export type CartLine = Schemas['LineView']
export type Discount = Schemas['DiscountView']
export type Session = Schemas['SessionView']
export type SessionState = Schemas['SessionState']
export type Language = Schemas['Language']
export type AgentEvent = Schemas['EventView']
export type MessageReply = Schemas['MessageResponse']
export type Menu = Schemas['MenuView']
export type HandoffSummary = Schemas['HandoffSummary']
export type StockLevel = Schemas['StockLevelView']
export type WeekOfSpecials = Schemas['WeekOfSpecialsView']
export type HandoffPackage = Schemas['HandoffPackage']
export type HandoffStep = Schemas['Step']
export type HeldStock = Schemas['HeldStock']
export type HandoffOutcome = Schemas['HandoffOutcome']
export type UnmetRequest = Schemas['UnmetRequest']
export type AdminMenu = Schemas['AdminMenuView']
export type AdminItem = Schemas['AdminItemView']
export type AdminCategory = Schemas['AdminCategoryView']
export type NewItem = Schemas['NewItem']
export type ItemChanges = Schemas['ItemChanges']
export type PhoneCall = Schemas['ActiveCall']
export type Pickup = Schemas['Pickup']
export type PickupMark = Schemas['MarkPickupRequest']['status']
export type Caller = Schemas['CallerView']
export type CallerStatus = Schemas['CallerStatus']
export type Standing = Schemas['Standing']
export type WebPhoneInfo = Schemas['WebPhoneInfo']
export type WebCall = Schemas['WebCall']
export type ConversationSummary = Schemas['SummaryView']
export type SummaryDetail = Schemas['SummaryDetail']
export type SessionFacts = Schemas['SessionFacts']
export type Mood = Schemas['Mood']

const BASE = '/api'

/** An error the backend reported, with its stable code. */
export class ApiError extends Error {
  readonly status: number
  readonly code: string

  constructor(status: number, code: string, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(BASE + path, {
    ...init,
    headers: init?.body ? { 'Content-Type': 'application/json' } : undefined,
  })
  if (!response.ok) {
    throw await toApiError(response)
  }
  return (await response.json()) as T
}

async function toApiError(response: Response): Promise<ApiError> {
  // The backend answers every expected failure as {error: {code, message}}.
  try {
    const body = (await response.json()) as { error?: { code?: string; message?: string } }
    if (body.error?.code) {
      return new ApiError(response.status, body.error.code, body.error.message ?? body.error.code)
    }
  } catch {
    // Not JSON: fall through to the generic error below.
  }
  return new ApiError(response.status, 'unexpected_error', `Request failed (${response.status})`)
}

function post<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, {
    method: 'POST',
    body: body === undefined ? undefined : JSON.stringify(body),
  })
}

function patch<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, { method: 'PATCH', body: JSON.stringify(body) })
}

export const api = {
  agentInfo: () => request<AgentInfo>('/agent'),
  menu: (language: Language) => request<Menu>(`/menu?language=${language}`),
  openSession: (lane: string) => post<Session>('/sessions', { lane }),
  session: (sessionId: string) => request<Session>(`/sessions/${sessionId}`),
  /** Phone calls in progress, newest first. */
  phoneCalls: () => request<PhoneCall[]>('/phone/calls'),
  cart: (sessionId: string) => request<Cart>(`/sessions/${sessionId}/cart`),
  events: (sessionId: string) => request<AgentEvent[]>(`/sessions/${sessionId}/events`),
  /** Stock for every tracked item: on hand, held in carts, available. */
  inventory: () => request<StockLevel[]>('/inventory'),
  /** Monday to Sunday of this week, with today marked. */
  weekOfSpecials: (language: Language) => request<WeekOfSpecials>(`/specials/week?language=${language}`),
  sendMessage: (sessionId: string, text: string) =>
    post<MessageReply>(`/sessions/${sessionId}/messages`, { text }),
  /** Stop the session's timers, for a presenter who wants to talk. */
  pause: (sessionId: string) => post<Session>(`/sessions/${sessionId}/pause`),
  resume: (sessionId: string) => post<Session>(`/sessions/${sessionId}/resume`),

  /** Start a voice call: our WebRTC offer in, OpenAI's answer out (both SDP text). */
  startVoice: async (sessionId: string, sdp: string): Promise<string> => {
    const response = await fetch(`${BASE}/sessions/${sessionId}/voice`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/sdp' },
      body: sdp,
    })
    if (!response.ok) throw await toApiError(response)
    return response.text()
  },
  endVoice: async (sessionId: string): Promise<void> => {
    const response = await fetch(`${BASE}/sessions/${sessionId}/voice`, { method: 'DELETE' })
    // No call to end is not a problem: the call is over either way.
    if (!response.ok && response.status !== 404) throw await toApiError(response)
  },

  /** The web phone. Every request carries the key from the page's link. */
  webPhoneLine: async (key: string): Promise<WebPhoneInfo> => {
    const response = await fetch(`${BASE}/web-phone/line`, { headers: { 'X-Web-Phone-Key': key } })
    if (!response.ok) throw await toApiError(response)
    return (await response.json()) as WebPhoneInfo
  },
  /** Ring the agent: our WebRTC offer in, the call (with OpenAI's answer) out. */
  callWebPhone: async (key: string, sdp: string): Promise<WebCall> => {
    const response = await fetch(`${BASE}/web-phone/calls`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/sdp', 'X-Web-Phone-Key': key },
      body: sdp,
    })
    if (!response.ok) throw await toApiError(response)
    return (await response.json()) as WebCall
  },
  webPhoneConnected: async (key: string, callKey: string): Promise<void> => {
    const response = await fetch(`${BASE}/web-phone/calls/${callKey}/connected`, {
      method: 'POST',
      headers: { 'X-Web-Phone-Key': key },
    })
    if (!response.ok) throw await toApiError(response)
  },
  hangUpWebPhone: async (key: string, callKey: string): Promise<void> => {
    const response = await fetch(`${BASE}/web-phone/calls/${callKey}`, {
      method: 'DELETE',
      headers: { 'X-Web-Phone-Key': key },
    })
    // No call to end is not a problem: the call is over either way.
    if (!response.ok && response.status !== 404) throw await toApiError(response)
  },

  /** Sessions handed to a person, the longest-waiting first. */
  handoffs: () => request<HandoffSummary[]>('/handoffs'),
  handoff: (handoffId: string) => request<HandoffPackage>(`/handoffs/${handoffId}`),
  acknowledgeHandoff: (handoffId: string, by: string) =>
    post<HandoffPackage>(`/handoffs/${handoffId}/acknowledge`, { by }),
  resolveHandoff: (handoffId: string, outcome: HandoffOutcome, note: string | null) =>
    post<HandoffPackage>(`/handoffs/${handoffId}/resolve`, { outcome, note }),

  /** Phone orders still to be collected, then the ones marked recently. */
  pickups: () => request<Pickup[]>('/pickups'),
  markPickup: (orderId: string, status: PickupMark, by: string) => post<Pickup>(`/pickups/${orderId}`, { status, by }),

  /** Phone numbers that have called, the latest call first. */
  callers: () => request<Caller[]>('/callers'),
  setCallerStatus: (callerId: string, status: CallerStatus, reason: string, by: string) =>
    post<Caller>(`/callers/${callerId}/status`, { status, reason, by }),
  /** Forgets the number and everything known about it. */
  deleteCaller: async (callerId: string): Promise<void> => {
    const response = await fetch(`${BASE}/callers/${callerId}`, { method: 'DELETE' })
    if (!response.ok) throw await toApiError(response)
  },

  /** The note written about each session after it ended, newest first. */
  summaries: (outcome?: SessionState) => request<ConversationSummary[]>(outcome ? `/summaries?outcome=${outcome}` : '/summaries'),
  summary: (sessionId: string) => request<SummaryDetail>(`/sessions/${sessionId}/summary`),
  /** Writes the note again: the model's words if they pass the checks. */
  rewriteSummary: (sessionId: string) => post<SummaryDetail>(`/sessions/${sessionId}/summary`),

  /** What guests asked for that the menu couldn't fill, most asked first. */
  unmetRequests: (days: number) => request<UnmetRequest[]>(`/insights/unmet-requests?days=${days}`),

  /** Every item, those taken off the menu too, for the menu page. */
  adminMenu: () => request<AdminMenu>('/admin/menu'),
  createItem: (item: NewItem) => post<AdminItem>('/admin/menu/items', item),
  /** Only the fields given change. `is_active: false` takes it off the menu. */
  updateItem: (itemCode: string, changes: ItemChanges) => patch<AdminItem>(`/admin/menu/items/${itemCode}`, changes),
  /** The image itself is the body: JPEG, PNG or WebP. */
  setPhoto: async (itemCode: string, photo: Blob): Promise<AdminItem> => {
    const response = await fetch(`${BASE}/admin/menu/items/${itemCode}/photo`, {
      method: 'PUT',
      headers: { 'Content-Type': photo.type || 'application/octet-stream' },
      body: photo,
    })
    if (!response.ok) throw await toApiError(response)
    return (await response.json()) as AdminItem
  },
  removePhoto: (itemCode: string) => request<AdminItem>(`/admin/menu/items/${itemCode}/photo`, { method: 'DELETE' }),
  /** Where an item's photo is. The version makes a new photo a new URL. */
  photoUrl: (itemCode: string, version: string) => `${BASE}/menu/items/${itemCode}/photo?v=${version}`,
}
