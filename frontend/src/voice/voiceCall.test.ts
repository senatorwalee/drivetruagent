/**
 * The browser side of a voice call, with the microphone, the WebRTC
 * connection and the backend all replaced. jsdom has none of them.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../api/client'
import { VoiceCall, type VoiceCallDeps, type VoiceState } from './voiceCall'

class FakeTrack {
  enabled = true
  stopped = false
  stop() {
    this.stopped = true
  }
}

class FakeStream {
  readonly track = new FakeTrack()
  getTracks() {
    return [this.track]
  }
  getAudioTracks() {
    return [this.track]
  }
}

class FakeChannel {
  onmessage: ((message: { data: unknown }) => void) | null = null
  onclose: (() => void) | null = null
  /** OpenAI sends an event on the channel. */
  receive(event: unknown) {
    this.onmessage?.({ data: typeof event === 'string' ? event : JSON.stringify(event) })
  }
}

class FakeConnection {
  connectionState = 'new'
  closed = false
  added: unknown[] = []
  channelName: string | null = null
  readonly channel = new FakeChannel()
  remote: RTCSessionDescriptionInit | null = null
  ontrack: ((event: { streams: unknown[] }) => void) | null = null
  onconnectionstatechange: (() => void) | null = null

  addTrack(track: unknown) {
    this.added.push(track)
  }
  createDataChannel(name: string) {
    this.channelName = name
    return this.channel
  }
  async createOffer() {
    return { type: 'offer', sdp: 'v=0 offer' }
  }
  async setLocalDescription() {}
  async setRemoteDescription(description: RTCSessionDescriptionInit) {
    this.remote = description
  }
  close() {
    this.closed = true
  }
  /** WebRTC reports a change in the connection. */
  become(state: string) {
    this.connectionState = state
    this.onconnectionstatechange?.()
  }
}

let microphone: FakeStream
let connection: FakeConnection
let speaker: { srcObject: unknown }
let deps: VoiceCallDeps & { sendOffer: ReturnType<typeof vi.fn>; hangUp: ReturnType<typeof vi.fn> }

beforeEach(() => {
  microphone = new FakeStream()
  connection = new FakeConnection()
  speaker = { srcObject: null }
  deps = {
    getMicrophone: async () => microphone as unknown as MediaStream,
    createConnection: () => connection as unknown as RTCPeerConnection,
    sendOffer: vi.fn(async () => 'v=0 answer'),
    hangUp: vi.fn(async () => {}),
    speaker: speaker as unknown as HTMLAudioElement,
  }
})

async function started(): Promise<{ call: VoiceCall; states: VoiceState[] }> {
  const call = new VoiceCall(deps)
  const states: VoiceState[] = []
  call.subscribe((state) => states.push(state))
  await call.start()
  return { call, states }
}

describe('starting a call', () => {
  it('sends the offer to the backend and uses the answer it gets back', async () => {
    await started()

    expect(deps.sendOffer).toHaveBeenCalledWith('v=0 offer')
    expect(connection.remote).toEqual({ type: 'answer', sdp: 'v=0 answer' })
  })

  it('sends the microphone and opens the channel OpenAI expects', async () => {
    await started()

    expect(connection.added).toEqual([microphone.track])
    expect(connection.channelName).toBe('oai-events')
  })

  it('is connecting until WebRTC says it is connected, then live', async () => {
    const { call } = await started()
    expect(call.current.status).toBe('connecting')

    connection.become('connected')

    expect(call.current.status).toBe('live')
  })

  it("plays the agent's voice through the speaker", async () => {
    await started()
    const voice = { id: 'agent-voice' }

    connection.ontrack?.({ streams: [voice] })

    expect(speaker.srcObject).toBe(voice)
  })

  it('explains when the microphone is refused, and asks nothing of the backend', async () => {
    deps.getMicrophone = async () => {
      throw new DOMException('Permission denied', 'NotAllowedError')
    }

    const { call } = await started()

    expect(call.current).toMatchObject({ status: 'failed', problem: expect.stringMatching(/microphone could not be used/) })
    expect(deps.sendOffer).not.toHaveBeenCalled()
  })

  it("passes on the backend's reason when it refuses the call", async () => {
    deps.sendOffer.mockRejectedValueOnce(new ApiError(503, 'agent_unavailable', 'Voice uses OpenAI Realtime and needs OPENAI_API_KEY.'))

    const { call } = await started()

    expect(call.current).toMatchObject({ status: 'failed', problem: 'Voice uses OpenAI Realtime and needs OPENAI_API_KEY.' })
    expect(connection.closed).toBe(true)
    expect(microphone.track.stopped).toBe(true)
  })

  it('does nothing if a call is already starting', async () => {
    const call = new VoiceCall(deps)
    const first = call.start()
    const second = call.start()
    await Promise.all([first, second])

    expect(deps.sendOffer).toHaveBeenCalledTimes(1)
  })
})

describe('who is speaking', () => {
  it('follows the customer and the agent from the events channel', async () => {
    const { call } = await started()

    connection.channel.receive({ type: 'input_audio_buffer.speech_started' })
    expect(call.current).toMatchObject({ customerSpeaking: true, agentSpeaking: false })

    connection.channel.receive({ type: 'input_audio_buffer.speech_stopped' })
    connection.channel.receive({ type: 'output_audio_buffer.started' })
    expect(call.current).toMatchObject({ customerSpeaking: false, agentSpeaking: true })

    connection.channel.receive({ type: 'output_audio_buffer.stopped' })
    expect(call.current.agentSpeaking).toBe(false)
  })

  it('stops showing the agent speaking when it is interrupted', async () => {
    const { call } = await started()
    connection.channel.receive({ type: 'output_audio_buffer.started' })

    connection.channel.receive({ type: 'output_audio_buffer.cleared' })

    expect(call.current.agentSpeaking).toBe(false)
  })

  it('ignores events it does not use, and anything that is not JSON', async () => {
    const { call, states } = await started()
    const before = states.length

    connection.channel.receive({ type: 'response.output_audio.delta', delta: '…' })
    connection.channel.receive('not json')

    expect(states.length).toBe(before)
    expect(call.current.status).toBe('connecting')
  })
})

describe('ending a call', () => {
  it('hangs up: tells the backend, closes the connection and frees the microphone', async () => {
    const { call } = await started()
    connection.become('connected')

    await call.stop()

    expect(deps.hangUp).toHaveBeenCalledOnce()
    expect(connection.closed).toBe(true)
    expect(microphone.track.stopped).toBe(true)
    expect(speaker.srcObject).toBeNull()
    expect(call.current.status).toBe('ended')
  })

  it('ends when the other side hangs up', async () => {
    const { call } = await started()
    connection.become('connected')

    connection.become('closed')

    expect(call.current.status).toBe('ended')
    // The backend ended it, so there is nothing to tell it.
    expect(deps.hangUp).not.toHaveBeenCalled()
  })

  it('says so when the call drops', async () => {
    const { call } = await started()
    connection.become('connected')

    connection.become('failed')

    expect(call.current).toMatchObject({ status: 'failed', problem: 'The call dropped.' })
  })

  it('does not finish setting up a call that was hung up while waiting for the answer', async () => {
    let answer: (sdp: string) => void = () => {}
    deps.sendOffer.mockImplementationOnce(() => new Promise<string>((resolve) => (answer = resolve)))
    const call = new VoiceCall(deps)
    const starting = call.start()
    await vi.waitFor(() => expect(deps.sendOffer).toHaveBeenCalled())

    await call.stop()
    answer('v=0 late answer')
    await starting

    expect(connection.remote).toBeNull()
    expect(call.current.status).toBe('ended')
  })

  it('still ends if the backend cannot be told', async () => {
    deps.hangUp.mockRejectedValueOnce(new Error('offline'))
    const { call } = await started()

    await call.stop()

    expect(call.current.status).toBe('ended')
  })
})

describe('muting', () => {
  it('stops and restarts sending the microphone without ending the call', async () => {
    const { call } = await started()

    call.setMuted(true)
    expect(microphone.track.enabled).toBe(false)

    call.setMuted(false)
    expect(microphone.track.enabled).toBe(true)
    expect(call.current.status).toBe('connecting')
  })
})
