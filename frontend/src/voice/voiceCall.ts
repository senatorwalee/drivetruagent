/**
 * One voice call from the browser: microphone in, the agent's voice out.
 *
 * The audio goes over WebRTC straight between the browser and OpenAI. The
 * browser sends its offer to our backend, not to OpenAI, and gets the answer
 * back from it: the backend holds the key, sets the call up and runs every
 * tool the model asks for. Nothing here can change an order.
 *
 * OpenAI also sends a stream of events on a data channel. Only a few are read
 * here, to show who is speaking. What was said reaches the screen the usual
 * way, through the session's event log.
 *
 * Framework-free, with its browser parts passed in, so it is tested without a
 * microphone or a network.
 */

export type VoiceStatus = 'idle' | 'connecting' | 'live' | 'ended' | 'failed'

export interface VoiceState {
  status: VoiceStatus
  /** The customer's voice is coming through right now. */
  customerSpeaking: boolean
  /** The agent's voice is playing right now. */
  agentSpeaking: boolean
  /** What went wrong, in words, when the status is `failed`. */
  problem: string | null
}

export interface VoiceCallDeps {
  getMicrophone: () => Promise<MediaStream>
  createConnection: () => RTCPeerConnection
  /** Sends the offer to the backend and returns OpenAI's answer. */
  sendOffer: (sdp: string) => Promise<string>
  /** Tells the backend the call is over. */
  hangUp: () => Promise<void>
  /** Tells the backend the audio is up, for a call that waits for it. */
  connected?: () => Promise<void>
  /** Where the agent's voice is played. */
  speaker: HTMLAudioElement
}

export const IDLE: VoiceState = { status: 'idle', customerSpeaking: false, agentSpeaking: false, problem: null }

/** The name OpenAI expects for the events channel. */
const EVENTS_CHANNEL = 'oai-events'

export class VoiceCall {
  private state: VoiceState = IDLE
  private readonly listeners = new Set<(state: VoiceState) => void>()
  private connection: RTCPeerConnection | null = null
  private microphone: MediaStream | null = null
  private readonly deps: VoiceCallDeps

  constructor(deps: VoiceCallDeps) {
    this.deps = deps
  }

  get current(): VoiceState {
    return this.state
  }

  subscribe(listener: (state: VoiceState) => void): () => void {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  async start(): Promise<void> {
    if (this.state.status === 'connecting' || this.state.status === 'live') return
    this.update({ ...IDLE, status: 'connecting' })
    try {
      this.microphone = await this.deps.getMicrophone()
    } catch {
      this.fail('The microphone could not be used. Allow it in the browser and try again.')
      return
    }

    try {
      const connection = this.deps.createConnection()
      this.connection = connection
      for (const track of this.microphone.getTracks()) connection.addTrack(track, this.microphone)
      connection.ontrack = (event) => {
        this.deps.speaker.srcObject = event.streams[0] ?? null
      }
      connection.onconnectionstatechange = () => {
        const { connectionState } = connection
        if (connectionState === 'connected') {
          this.update({ status: 'live' })
          void this.deps.connected?.().catch(() => undefined)
        }
        if (connectionState === 'failed') this.fail('The call dropped.')
        if (connectionState === 'closed' || connectionState === 'disconnected') this.finish()
      }
      const events = connection.createDataChannel(EVENTS_CHANNEL)
      events.onmessage = (message) => this.read(message.data)
      events.onclose = () => this.finish()

      const offer = await connection.createOffer()
      await connection.setLocalDescription(offer)
      const answer = await this.deps.sendOffer(offer.sdp ?? '')
      if (this.connection !== connection) return // hung up while waiting
      await connection.setRemoteDescription({ type: 'answer', sdp: answer })
    } catch (error) {
      this.fail(error instanceof Error && error.message ? error.message : 'The call could not be started.')
    }
  }

  /** Stop sending the microphone, for a presenter who wants to talk. */
  setMuted(muted: boolean): void {
    for (const track of this.microphone?.getAudioTracks() ?? []) track.enabled = !muted
  }

  /** Hang up. The backend is told, so the call also ends on OpenAI's side. */
  async stop(): Promise<void> {
    const wasCalling = this.connection !== null
    this.release()
    this.update({ ...IDLE, status: 'ended' })
    if (wasCalling) await this.deps.hangUp().catch(() => undefined)
  }

  private read(data: unknown): void {
    if (typeof data !== 'string') return
    let type: unknown
    try {
      type = (JSON.parse(data) as { type?: unknown }).type
    } catch {
      return
    }
    if (type === 'input_audio_buffer.speech_started') this.update({ customerSpeaking: true })
    if (type === 'input_audio_buffer.speech_stopped') this.update({ customerSpeaking: false })
    if (type === 'output_audio_buffer.started') this.update({ agentSpeaking: true })
    if (type === 'output_audio_buffer.stopped' || type === 'output_audio_buffer.cleared') this.update({ agentSpeaking: false })
  }

  /** The call ended from the other side: the backend hung up, or it dropped. */
  private finish(): void {
    if (this.state.status === 'ended' || this.state.status === 'failed' || this.state.status === 'idle') return
    this.release()
    this.update({ ...IDLE, status: 'ended' })
  }

  private fail(problem: string): void {
    this.release()
    this.update({ ...IDLE, status: 'failed', problem })
  }

  private release(): void {
    const connection = this.connection
    this.connection = null
    if (connection) {
      connection.onconnectionstatechange = null
      connection.close()
    }
    for (const track of this.microphone?.getTracks() ?? []) track.stop()
    this.microphone = null
    this.deps.speaker.srcObject = null
  }

  private update(change: Partial<VoiceState>): void {
    this.state = { ...this.state, ...change }
    for (const listener of this.listeners) listener(this.state)
  }
}
