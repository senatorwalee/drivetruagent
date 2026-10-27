/**
 * The web phone page, with the backend, the microphone and WebRTC replaced.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { api, ApiError, type WebCall } from '../api/client'
import type { PhoneParts } from './useWebPhone'
import { WebPhonePage } from './WebPhonePage'

class FakeConnection {
  connectionState = 'new'
  ontrack: ((event: { streams: unknown[] }) => void) | null = null
  onconnectionstatechange: (() => void) | null = null
  addTrack() {}
  createDataChannel() {
    return { onmessage: null, onclose: null }
  }
  async createOffer() {
    return { type: 'offer', sdp: 'v=0 offer' }
  }
  async setLocalDescription() {}
  async setRemoteDescription() {}
  close() {}
  become(state: string) {
    this.connectionState = state
    this.onconnectionstatechange?.()
  }
}

const ANSWERED: WebCall = { call_key: 'call-1', answer_sdp: 'v=0 answer', session_id: 'session-1', turned_away: null }

let connection: FakeConnection
let parts: PhoneParts

beforeEach(() => {
  connection = new FakeConnection()
  const track = { enabled: true, stop() {} }
  parts = {
    getMicrophone: async () => ({ getTracks: () => [track], getAudioTracks: () => [track] }) as unknown as MediaStream,
    createConnection: () => connection as unknown as RTCPeerConnection,
    createSpeaker: () => ({ srcObject: null, play: async () => {} }) as unknown as HTMLAudioElement,
  }
  window.history.replaceState(null, '', '/call#key=demo-key')
  vi.spyOn(api, 'webPhoneLine').mockResolvedValue({ restaurant_name: 'Maple Lane Burgers' })
})

afterEach(() => {
  window.history.replaceState(null, '', '/')
})

function showPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <WebPhonePage parts={parts} />
    </QueryClientProvider>,
  )
}

describe('before a call', () => {
  it('checks the key from the link and shows who it calls', async () => {
    showPage()

    expect(await screen.findByRole('heading', { name: 'Maple Lane Burgers' })).toBeInTheDocument()
    expect(api.webPhoneLine).toHaveBeenCalledWith('demo-key')
    expect(screen.getByText('Tap to call')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Call' })).toBeEnabled()
  })

  it('says so, and cannot call, when the link has no key', async () => {
    window.history.replaceState(null, '', '/call')
    showPage()

    expect(await screen.findByText(/This link has no key/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Call' })).toBeDisabled()
    expect(api.webPhoneLine).not.toHaveBeenCalled()
  })

  it('says so, and cannot call, when the key is wrong', async () => {
    vi.mocked(api.webPhoneLine).mockRejectedValue(new ApiError(403, 'wrong_web_phone_key', 'Wrong key.'))
    showPage()

    expect(await screen.findByText(/Ask for a new link/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Call' })).toBeDisabled()
  })
})

describe('a call', () => {
  it('rings with the key, tells the backend when the audio is up, and hangs up', async () => {
    const callWebPhone = vi.spyOn(api, 'callWebPhone').mockResolvedValue(ANSWERED)
    const connected = vi.spyOn(api, 'webPhoneConnected').mockResolvedValue()
    const hangUp = vi.spyOn(api, 'hangUpWebPhone').mockResolvedValue()
    const user = userEvent.setup()
    showPage()

    await user.click(await screen.findByRole('button', { name: 'Call' }))

    expect(await screen.findByText('Calling…')).toBeInTheDocument()
    expect(callWebPhone).toHaveBeenCalledWith('demo-key', 'v=0 offer')
    expect(connected).not.toHaveBeenCalled()

    act(() => connection.become('connected'))

    expect(await screen.findByText('0:00')).toBeInTheDocument()
    expect(connected).toHaveBeenCalledWith('demo-key', 'call-1')

    await user.click(screen.getByRole('button', { name: 'End' }))

    expect(hangUp).toHaveBeenCalledWith('demo-key', 'call-1')
    expect(await screen.findByText(/Call ended/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Call again' })).toBeEnabled()
  })

  it('mutes and unmutes the microphone', async () => {
    vi.spyOn(api, 'callWebPhone').mockResolvedValue(ANSWERED)
    vi.spyOn(api, 'webPhoneConnected').mockResolvedValue()
    const user = userEvent.setup()
    showPage()
    await user.click(await screen.findByRole('button', { name: 'Call' }))

    const mute = await screen.findByRole('button', { name: 'Mute' })
    await user.click(mute)

    expect(mute).toHaveAttribute('aria-pressed', 'true')
  })

  it('explains a busy line', async () => {
    vi.spyOn(api, 'callWebPhone').mockRejectedValue(new ApiError(409, 'voice_call_in_progress', 'x'))
    const user = userEvent.setup()
    showPage()

    await user.click(await screen.findByRole('button', { name: 'Call' }))

    expect(await screen.findByText(/The line is busy/)).toBeInTheDocument()
  })

  it('explains a call that was turned away', async () => {
    vi.spyOn(api, 'callWebPhone').mockResolvedValue({ ...ANSWERED, session_id: null, turned_away: 'blocked' })
    const user = userEvent.setup()
    showPage()

    await user.click(await screen.findByRole('button', { name: 'Call' }))

    expect(await screen.findByText(/blocked from phone orders/)).toBeInTheDocument()
  })
})
