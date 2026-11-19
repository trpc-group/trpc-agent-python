/**
 * tRPC-Agent JSON-RPC server for the DSH runtime.
 *
 * Replaces the official `sdk-jsonrpc-server` with the small protocol the
 * Python DSHAgent needs:
 *
 *   initialize            { cwd, model? }              -> { serverInfo }
 *   session/trpc-prompt   { sessionId, contentBlocks,
 *                           jsonl, version, cwd?,
 *                           model?, meta }            -> { messageId, jsonl?, version? }
 *   session/trpc-cancel   { sessionId } (notification)
 *   shutdown                                          -> {}
 *
 * An empty `jsonl` means this is the first turn: the server creates the
 * session through `ctx.agents.create()` and DSH builds the header. A non-empty
 * `jsonl` is the snapshot from the tRPC session state: the backend seeds it
 * and DSH resumes through `ctx.agents.resume()`.
 *
 * Output is pushed as `session.assistant.delta`, `session.event`, and
 * `session.status` notifications. `session/trpc-prompt` itself only returns
 * the receipt; completion is `session.status: idle`.
 *
 * @module trpc-jsonrpc-server
 */

import { resolve } from 'node:path'
import { admitEncodedFile, admitEncodedImages } from '@deepseek-ai/dsh-attachment'
import { createUserMessage } from '@deepseek-ai/dsh-llm'
import { SessionId } from '@deepseek-ai/dsh-session'
import { JsonRpcLineTransport } from '@deepseek-ai/dsh-sdk-protocol'

export const name = 'trpc-jsonrpc-server'
export const inject = ['sdkAppStartup', 'agents', 'llm', 'trpcSessionBackend', 'trpcRequestContext']

/** Upper bound for the post-turn snapshot cache; see rememberSnapshot(). */
const MAX_CACHED_SNAPSHOTS = 64

function normalizeModel(params) {
  if (params?.model && typeof params.model === 'object') {
    const value = params.model
    if (typeof value.provider !== 'string' || typeof value.model !== 'string') {
      throw new TypeError('initialize model requires provider and model strings')
    }
    return {
      provider: value.provider,
      model: value.model,
      ...(value.reasoningEffort === undefined ? {} : { reasoningEffort: value.reasoningEffort }),
      ...(value.maxTokens === undefined ? {} : { maxTokens: value.maxTokens }),
    }
  }
  if (typeof params?.provider === 'string' && typeof params?.model === 'string') {
    return {
      provider: params.provider,
      model: params.model,
      ...(params.reasoningEffort === undefined ? {} : { reasoningEffort: params.reasoningEffort }),
      ...(params.maxTokens === undefined ? {} : { maxTokens: params.maxTokens }),
    }
  }
  return undefined
}

function selectionConfig(selection) {
  return {
    provider: selection.provider,
    model: selection.model,
    ...(selection.reasoningEffort === undefined ? {} : { reasoningEffort: selection.reasoningEffort }),
    ...(selection.maxTokens === undefined ? {} : { maxTokens: selection.maxTokens }),
  }
}

function isEncodedImage(block) {
  return block !== null && typeof block === 'object'
    && block.type === 'image' && typeof block.data === 'string'
}

function isEncodedFile(block) {
  return block !== null && typeof block === 'object'
    && block.type === 'file' && typeof block.data === 'string'
}

/**
 * Turn encoded image/file blocks into durable attachment references.
 *
 * Mirrors the official SDK server's durablePromptContent(): image bytes are
 * admitted as a batch, file bytes one by one, and every other block passes
 * through unchanged.
 */
async function durablePromptContent(ctx, blocks) {
  const images = blocks.filter(isEncodedImage)
  const files = blocks.filter(isEncodedFile)
  if (images.length === 0 && files.length === 0) return blocks

  const attachments = ctx.get('attachments')
  if (attachments === undefined) {
    throw new Error('DSH prompt content requires an attachment store')
  }

  const imageRefs = images.length === 0 ? [] : await admitEncodedImages(
    attachments,
    images.map(image => ({
      data: image.data,
      mediaType: image.mimeType,
      ...(image.name === undefined ? {} : { name: image.name }),
    })),
  )

  let imageIndex = 0
  const content = []
  for (const block of blocks) {
    if (isEncodedImage(block)) {
      content.push({ type: 'image', attachment: imageRefs[imageIndex++] })
    } else if (isEncodedFile(block)) {
      const ref = await admitEncodedFile(attachments, {
        data: block.data,
        ...(block.name === undefined ? {} : { name: block.name }),
      })
      content.push({ type: 'file', attachment: ref })
    } else {
      content.push(block)
    }
  }
  return content
}

/** One active prompt turn. */
class RunSession {
  constructor(server, req, selection) {
    this.server = server
    this.req = req
    this.selection = selection
    this.agent = undefined
    this.handle = undefined
    this.messageId = undefined
    this.started = false
    this.disposed = false
    this.seq = 0
  }

  async start() {
    const backend = this.server.backend
    const hasSnapshot = this.req.jsonl.trim().length > 0

    if (hasSnapshot) {
      // Resume: replay the tRPC-side snapshot into the in-memory backend.
      backend.seed(this.req.sessionId, this.req.jsonl, this.req.version)
    } else if (backend.has(this.req.sessionId)) {
      // A previous create left a session behind without the tRPC side
      // persisting its snapshot. The tRPC session state is authoritative, so
      // discard the stale in-memory record and let DSH create a fresh one.
      backend.drop(this.req.sessionId)
    }

    if (this.selection === undefined && backend.eventCount(this.req.sessionId) === 0) {
      this.selection = this.server.defaultModel
    }

    const agentOptions = this.selection === undefined
      ? {}
      : selectionConfig(this.selection)
    if (this.selection !== undefined && this.selection !== this.server.defaultModel) {
      await this.server.ctx.llm.resolveCallConfig(agentOptions)
    }

    if (hasSnapshot) {
      this.handle = await this.server.ctx.agents.resume({
        resumeSessionId: SessionId(this.req.sessionId),
        agentOptions,
      })
    } else {
      // First turn: DSH builds the session header (including cwd) and the
      // backend stores it through persistence.create(); Python never writes
      // the JSONL header itself.
      const cwd = this.req.cwd ?? this.server.cwd
      this.handle = await this.server.ctx.agents.create({
        sessionId: SessionId(this.req.sessionId),
        meta: { cwd },
        agentOptions,
      })
    }
    this.agent = this.handle.agent

    this.server.agentRuns.set(this.agent, this)
    this.server.requestContext.set({ sessionId: this.req.sessionId, meta: this.req.meta ?? {} })

    const content = await durablePromptContent(this.server.ctx, this.req.contentBlocks)
    const message = createUserMessage({
      content,
      source: { kind: 'user' },
    })
    this.messageId = message.id
    const snapshot = this.server.backend.snapshot(this.req.sessionId)
    this.agent.followup(message)
    this.started = true
    return {
      messageId: message.id,
      ...(snapshot === undefined ? {} : { jsonl: snapshot.jsonl, version: snapshot.version }),
    }
  }

  onAssistantFrame(frame) {
    if (frame.type !== 'chunk') return
    const chunk = frame.chunk
    let delta
    if (chunk.type === 'text-delta') {
      delta = { kind: 'text', index: chunk.index, text: chunk.text }
    } else if (chunk.type === 'reasoning-delta') {
      delta = { kind: 'reasoning', index: chunk.index, text: chunk.text }
    } else if (chunk.type === 'tool-call-delta') {
      delta = {
        kind: 'tool-call',
        index: chunk.index,
        toolCall: {
          id: chunk.id,
          ...(chunk.name === undefined ? {} : { name: chunk.name }),
          argumentsDelta: chunk.argumentsDelta,
        },
      }
    } else {
      return
    }

    this.server.transport.notify('session.assistant.delta', {
      sessionId: this.req.sessionId,
      seq: this.seq++,
      attemptId: frame.attemptId,
      revision: frame.revision,
      ...delta,
    })
  }

  cancel() {
    this.agent?.cancel({ kind: 'user' })
  }

  async dispose() {
    if (this.disposed) return
    this.disposed = true
    if (this.agent !== undefined) this.server.agentRuns.delete(this.agent)
    this.server.requestContext.clear(this.req.sessionId)
    try {
      if (this.handle !== undefined) await this.handle.dispose()
    } finally {
      try {
        this.server.rememberSnapshot(this.req.sessionId)
        this.server.backend.drop(this.req.sessionId)
      } catch {
        // A partially opened run may still own the writer; the next seed will replace it.
      }
    }
  }
}

class TrpcJsonRpcServer {
  constructor(ctx, transport) {
    this.ctx = ctx
    this.transport = transport
    this.cwd = process.cwd()
    this.defaultModel = undefined
    this.initialized = false
    this.shuttingDown = false
    this.runs = new Map()
    this.cleanups = new Map()
    this.agentRuns = new Map()
    this.lastSnapshots = new Map()
    this.disposers = []

    this.backend = ctx.get('trpcSessionBackend')
    this.requestContext = ctx.get('trpcRequestContext')
    if (this.backend === undefined || this.requestContext === undefined) {
      throw new Error('trpc-jsonrpc-server requires trpcSessionBackend and trpcRequestContext services')
    }

    this.disposers.push(ctx.on('session/event', (session, event) => {
      this.transport.notify('session.event', { sessionId: String(session.id), event })
    }))
    this.disposers.push(ctx.on('agent/status', ({ agent, status }) => {
      const sessionId = String(agent.session.id)
      this.transport.notify('session.status', { sessionId, status })
      if (status === 'idle') this.finish(sessionId)
    }))
    this.disposers.push(ctx.on('agent/assistant-stream', ({ agent, frame }) => {
      const run = this.agentRuns.get(agent)
      if (run !== undefined) run.onAssistantFrame(frame)
    }))
  }

  async initialize(params = {}) {
    if (this.initialized) throw new Error('trpc-jsonrpc-server is already initialized')
    if (this.shuttingDown) throw new Error('trpc-jsonrpc-server is shutting down')

    const cwd = resolve(typeof params.cwd === 'string' ? params.cwd : process.cwd())
    const model = normalizeModel(params)
    if (model !== undefined) {
      await this.ctx.llm.resolveCallConfig(selectionConfig(model))
    }

    this.cwd = cwd
    this.defaultModel = model
    this.initialized = true
    return { serverInfo: { name: 'deepseek-harness-sdk-runtime', version: '0.0.1' } }
  }

  async prompt(params = {}) {
    if (!this.initialized) throw new Error('trpc-jsonrpc-server is not initialized')
    if (this.shuttingDown) throw new Error('trpc-jsonrpc-server is shutting down')
    if (typeof params.sessionId !== 'string' || params.sessionId.length === 0) {
      throw new TypeError('session/trpc-prompt requires a non-empty sessionId')
    }
    if (!Array.isArray(params.contentBlocks)) {
      throw new TypeError('session/trpc-prompt requires contentBlocks')
    }
    if (typeof params.jsonl !== 'string') {
      throw new TypeError('session/trpc-prompt requires a jsonl string')
    }
    if (!Number.isSafeInteger(params.version) || params.version <= 0) {
      throw new TypeError('session/trpc-prompt requires a positive integer version')
    }

    return this.startRun({
      sessionId: params.sessionId,
      contentBlocks: params.contentBlocks,
      jsonl: params.jsonl,
      version: params.version,
      cwd: typeof params.cwd === 'string' && params.cwd.length > 0 ? params.cwd : undefined,
      model: normalizeModel({ model: params.model }),
      meta: params.meta && typeof params.meta === 'object' ? params.meta : {},
    })
  }

  async startRun(req) {
    const sessionId = req.sessionId
    if (this.runs.has(sessionId)) throw new Error(`session busy: ${sessionId}`)

    const pendingCleanup = this.cleanups.get(sessionId)
    if (pendingCleanup !== undefined) await pendingCleanup
    if (this.runs.has(sessionId)) throw new Error(`session busy: ${sessionId}`)

    const run = new RunSession(this, req, req.model)
    this.runs.set(sessionId, run)
    try {
      return await run.start()
    } catch (error) {
      this.runs.delete(sessionId)
      await run.dispose().catch(() => {})
      throw error
    }
  }

  rememberSnapshot(sessionId) {
    const snapshot = this.backend.snapshot(sessionId)
    if (snapshot === undefined) return
    this.lastSnapshots.set(sessionId, snapshot)
    // The post-turn request normally consumes and deletes this entry. The cap
    // is a backstop for turns that end without a snapshot request, so a
    // long-lived process does not retain every session's full JSONL.
    while (this.lastSnapshots.size > MAX_CACHED_SNAPSHOTS) {
      const oldest = this.lastSnapshots.keys().next().value
      if (oldest === undefined) break
      this.lastSnapshots.delete(oldest)
    }
  }

  finish(sessionId) {
    const run = this.runs.get(sessionId)
    if (run === undefined || !run.started) return
    this.runs.delete(sessionId)
    const cleanup = run.dispose().finally(() => {
      if (this.cleanups.get(sessionId) === cleanup) this.cleanups.delete(sessionId)
    })
    this.cleanups.set(sessionId, cleanup)
    void cleanup.catch(() => {})
  }

  handleNotification(method, params = {}) {
    if (method !== 'session/trpc-cancel') return
    if (typeof params.sessionId !== 'string') return
    this.runs.get(params.sessionId)?.cancel()
  }

  async handleRequest(method, params = {}) {
    switch (method) {
      case 'initialize': return this.initialize(params)
      case 'session/trpc-prompt': return this.prompt(params)
      case 'session/trpc-snapshot': {
        const snapshot = this.lastSnapshots.get(params.sessionId)
          ?? this.backend.snapshot(params.sessionId)
        // The post-turn snapshot is consumed exactly once. Keeping every
        // session's full JSONL here would leak memory in a long-lived DSH
        // process that serves many tRPC sessions.
        this.lastSnapshots.delete(params.sessionId)
        if (snapshot === undefined) throw new Error(`unknown session: ${params.sessionId}`)
        return snapshot
      }
      case 'shutdown': return this.shutdown()
      default: throw new Error(`unknown tRPC DSH runtime method: ${method}`)
    }
  }

  async shutdown() {
    this.shuttingDown = true
    while (this.disposers.length > 0) {
      try {
        this.disposers.pop()?.()
      } catch {
        // Teardown continues; listener errors are already best-effort.
      }
    }
    const runs = [...this.runs.values()]
    this.runs.clear()
    await Promise.allSettled(runs.map(run => run.dispose()))
    await Promise.allSettled([...this.cleanups.values()])
    this.cleanups.clear()
    return {}
  }
}

export function apply(ctx) {
  const rootFiber = ctx.root.fiber
  const transport = new JsonRpcLineTransport(process.stdin, process.stdout)
  const server = new TrpcJsonRpcServer(ctx, transport)

  let exitTask
  const disposeAndExit = () => {
    exitTask ??= (async () => {
      await Promise.allSettled([Promise.resolve().then(() => transport.flush())])
      await Promise.allSettled([Promise.resolve().then(() => rootFiber.dispose())])
      process.exit(0)
    })()
    return exitTask
  }

  transport.onRequest(async (method, params) => {
    if (method === 'initialize') await ctx.get('loader')?.await()
    const result = await server.handleRequest(method, params)
    if (method === 'shutdown') setImmediate(() => { void disposeAndExit() })
    return result
  })
  transport.onNotification((method, params) => {
    server.handleNotification(method, params)
  })

  ctx.effect(() => {
    transport.start()
    return async () => {
      await server.shutdown()
      transport.close()
    }
  }, 'trpc-jsonrpc.serve')
}
