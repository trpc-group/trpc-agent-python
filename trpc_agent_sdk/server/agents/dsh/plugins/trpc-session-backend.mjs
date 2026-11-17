/**
 * In-memory SessionPersistence backend for the tRPC-Agent DSHAgent runtime.
 *
 * The backend owns the per-turn working copy of a DSH session and supports
 * both sides of the lifecycle:
 *
 * - first turn: the server calls `ctx.agents.create({ meta: { cwd } })`, DSH
 *   builds the session header, and this backend stores it through `create()`;
 * - later turns: the tRPC-Agent side sends the full JSONL snapshot, `seed()`
 *   replaces the in-memory session, and DSH resumes it through `open()`.
 *
 * DSH is the only writer of the session format. The tRPC-Agent side just
 * carries the snapshot between turns; no session files are written under
 * DSH_HOME.
 *
 * @module trpc-session-backend
 */

import {
  SessionAlreadyExistsError,
  SessionAlreadyOwnedError,
  SessionHandleClosedError,
  SessionPersistence,
  SessionPersistenceNotFoundError,
  SessionPersistenceRevision,
  SessionReadOnlyError,
} from '@deepseek-ai/dsh-session-persistence'
import { SESSION_FORMAT_VERSION, SessionId } from '@deepseek-ai/dsh-session'

export const name = 'trpc-session-backend'

/** Parse one wire JSONL snapshot into a DSH session header plus events. */
function parseJsonl(jsonl, sessionId, version) {
  const text = (jsonl ?? '').trim()
  if (text.length === 0) {
    throw new Error(
      'trpc-session-backend: cannot seed an empty session JSONL; '
      + 'create the session through ctx.agents.create() first',
    )
  }

  const lines = text.split('\n').filter(line => line.trim().length > 0)
  let rawHeader
  try {
    rawHeader = JSON.parse(lines[0])
  } catch (error) {
    throw new Error(`trpc-session-backend: session header is not valid JSON: ${String(error)}`)
  }
  if (rawHeader === null || typeof rawHeader !== 'object' || Array.isArray(rawHeader)) {
    throw new Error('trpc-session-backend: session JSONL first line is not a session header')
  }
  if (rawHeader.type !== 'session') {
    throw new Error('trpc-session-backend: session JSONL first line is not a session header')
  }
  if (rawHeader.version !== version) {
    throw new Error(
      `trpc-session-backend: session JSONL version ${rawHeader.version} `
      + `does not match request version ${version}`,
    )
  }
  if (String(rawHeader.id) !== String(sessionId)) {
    throw new Error(
      `trpc-session-backend: session JSONL id ${rawHeader.id} `
      + `does not match sessionId ${sessionId}`,
    )
  }

  const events = lines.slice(1).map((line, index) => {
    let event
    try {
      event = JSON.parse(line)
    } catch (error) {
      throw new Error(
        `trpc-session-backend: session event line ${index + 1} is not valid JSON: ${String(error)}`,
      )
    }
    if (event === null || typeof event !== 'object' || Array.isArray(event)) {
      throw new Error(
        `trpc-session-backend: session event line ${index + 1} is not a JSON object`,
      )
    }
    return event
  })
  for (let index = 0; index < events.length; index += 1) {
    if (events[index].seq !== index) {
      throw new Error(
        `trpc-session-backend: session JSONL event seq `
        + `${events[index].seq} is not contiguous at ${index}`,
      )
    }
  }

  const { type: _type, ...header } = rawHeader
  return { header, events, inheritedEventCount: 0 }
}

/** Serialize one in-memory session back into the wire JSONL snapshot. */
function serialize(session) {
  const { type: _type, ...header } = session.header
  const headerLine = JSON.stringify({ type: 'session', ...header })
  if (session.events.length === 0) return `${headerLine}\n`
  return `${headerLine}\n${session.events.map(event => JSON.stringify(event)).join('\n')}\n`
}

/** One in-memory SessionHandle over a seeded or newly created session. */
class MemorySessionHandle {
  constructor(backend, session, access) {
    this.backend = backend
    this.session = session
    this.access = access
    this.closed = false
  }

  get id() {
    return SessionId(String(this.session.header.id))
  }

  get header() {
    return this.session.header
  }

  get inheritedEventCount() {
    return this.session.inheritedEventCount
  }

  async read(offset = 0, length, _options) {
    if (this.closed) throw new SessionHandleClosedError(this.id, 'read')
    const start = offset ?? 0
    const end = length === undefined ? this.session.events.length : start + length
    return {
      eventState: 'detached',
      events: this.session.events.slice(start, end),
    }
  }

  async append(events, _options) {
    if (this.closed) throw new SessionHandleClosedError(this.id, 'append')
    if (this.access !== 'write') throw new SessionReadOnlyError(this.id, 'append')
    if (!Array.isArray(events)) {
      throw new TypeError('trpc-session-backend: append requires an array of events')
    }
    if (events.length === 0) return
    const expected = this.session.events.length
    for (let index = 0; index < events.length; index += 1) {
      if (events[index].seq !== expected + index) {
        throw new Error(
          `trpc-session-backend: session "${this.id}" append is not contiguous: `
          + `expected seq ${expected + index}, got ${events[index].seq}`,
        )
      }
    }
    this.session.events.push(...events)
  }

  async flush(_options) {
    if (this.closed) throw new SessionHandleClosedError(this.id, 'flush')
    if (this.access !== 'write') throw new SessionReadOnlyError(this.id, 'flush')
    this.session.jsonl = serialize(this.session)
  }

  async close() {
    if (this.closed) return
    this.closed = true
    if (this.session.writer === this) this.session.writer = undefined
  }

  async [Symbol.asyncDispose]() {
    await this.close()
  }
}

/** SessionPersistence provider stored in `ctx.sessionPersistence`. */
class TrpcSessionBackend extends SessionPersistence {
  name = 'trpc-session-backend'

  constructor(ctx) {
    super(ctx)
    this.sessions = new Map()
    this.revision = 0
  }

  /** Create the in-memory record for a session that DSH just built. */
  async create(header, options = {}) {
    options.signal?.throwIfAborted?.()
    if (header === null || typeof header !== 'object' || Array.isArray(header)) {
      throw new TypeError('trpc-session-backend: create requires a session header object')
    }
    const id = String(header.id)
    if (id.length === 0) throw new TypeError('trpc-session-backend: session header requires an id')
    if (!Number.isSafeInteger(header.version) || header.version <= 0) {
      throw new TypeError('trpc-session-backend: session header requires a positive integer version')
    }
    if (this.sessions.has(id)) throw new SessionAlreadyExistsError(header.id)

    const session = {
      header: { ...header },
      events: [],
      inheritedEventCount: options.inheritedEventCount ?? 0,
      writer: undefined,
      jsonl: '',
    }
    this.sessions.set(id, session)
    const handle = new MemorySessionHandle(this, session, 'write')
    session.writer = handle
    return handle
  }

  async open(id, access, _options = {}) {
    const key = String(id)
    const session = this.sessions.get(key)
    if (session === undefined) throw new SessionPersistenceNotFoundError(SessionId(key))
    if (access === 'write') {
      if (session.writer !== undefined) throw new SessionAlreadyOwnedError(SessionId(key))
      const handle = new MemorySessionHandle(this, session, 'write')
      session.writer = handle
      return handle
    }
    return new MemorySessionHandle(this, session, 'read')
  }

  async flush(_options = {}) {
    for (const session of this.sessions.values()) {
      if (session.writer !== undefined && !session.writer.closed) await session.writer.flush()
    }
  }

  async stat(id, _options = {}) {
    const session = this.sessions.get(String(id))
    if (session === undefined) return undefined
    return {
      header: session.header,
      revision: SessionPersistenceRevision(`trpc:${++this.revision}`),
      eventCount: session.events.length,
      sizeBytes: serialize(session).length,
    }
  }

  async list(_options = {}) {
    return [...this.sessions.values()].map(session => ({
      header: session.header,
      revision: SessionPersistenceRevision(`trpc:${++this.revision}`),
      eventCount: session.events.length,
      sizeBytes: serialize(session).length,
    }))
  }

  /** Replace the in-memory session from one request snapshot. */
  seed(sessionId, jsonl, version) {
    const key = String(sessionId)
    const current = this.sessions.get(key)
    if (current?.writer !== undefined) {
      throw new Error(`session "${key}" already has an active writer`)
    }
    const parsed = parseJsonl(jsonl, key, version || SESSION_FORMAT_VERSION)
    this.sessions.set(key, {
      header: parsed.header,
      events: parsed.events,
      inheritedEventCount: parsed.inheritedEventCount,
      writer: undefined,
      jsonl: jsonl ?? '',
    })
  }

  /** Whether the backend currently holds this session. */
  has(sessionId) {
    return this.sessions.has(String(sessionId))
  }

  /** Number of events currently seeded for one session. */
  eventCount(sessionId) {
    return this.sessions.get(String(sessionId))?.events.length ?? 0
  }

  /** Return the current JSONL snapshot for diagnostics and tests. */
  snapshot(sessionId) {
    const session = this.sessions.get(String(sessionId))
    if (session === undefined) return undefined
    return { jsonl: serialize(session), version: session.header.version }
  }

  /** Append one live event routed from the session store. */
  async appendLive(sessionId, event) {
    const writer = this.sessions.get(String(sessionId))?.writer
    if (writer === undefined || writer.closed) return
    await writer.append([event])
  }

  /** Flush one live session through its active writer. */
  async flushSession(sessionId) {
    const writer = this.sessions.get(String(sessionId))?.writer
    if (writer === undefined || writer.closed) return
    await writer.flush()
  }

  /** Close the active writer of one disposed session. */
  async closeSession(sessionId) {
    const writer = this.sessions.get(String(sessionId))?.writer
    if (writer === undefined || writer.closed) return
    await writer.close()
  }

  /** Forget one session after its turn has fully settled. */
  drop(sessionId) {
    const key = String(sessionId)
    const session = this.sessions.get(key)
    if (session === undefined) return
    if (session.writer !== undefined) throw new Error(`session "${key}" still has an active writer`)
    this.sessions.delete(key)
  }
}

export function apply(ctx) {
  const backend = new TrpcSessionBackend(ctx)
  ctx.provide('trpcSessionBackend', backend)

  ctx.effect(() => {
    const disposers = []
    disposers.push(ctx.on('session/event', (session, event) => {
      void backend.appendLive(session.id, event).catch((error) => {
        ctx.logger.warn(
          `trpc-session-backend: live append for session "${session.id}" failed: ${String(error)}`,
        )
      })
    }))
    disposers.push(ctx.on('session/flush', async (session) => {
      await backend.flushSession(session.id)
    }))
    disposers.push(ctx.on('session/disposed', async (session) => {
      await backend.closeSession(session.id)
    }))
    return () => {
      for (const dispose of disposers) dispose()
    }
  }, 'trpc-session-backend.live-routing')
}
