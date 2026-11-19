/**
 * Per-run business metadata side store for user JS plugins.
 *
 * `session/trpc-prompt` carries `meta` from Python's
 * `RunConfig.custom_data`; the tRPC JSON-RPC server stores it before
 * `agent.followup()` and clears it when the run settles. Tools and policies
 * read it through `ctx.get('trpcRequestContext')` keyed by session id.
 *
 * @module trpc-request-context
 */

export const name = 'trpc-request-context'

export function apply(ctx) {
  const contexts = new Map()

  ctx.provide('trpcRequestContext', {
    set({ sessionId, meta }) {
      const key = String(sessionId)
      contexts.set(key, { sessionId: key, meta: meta ?? {} })
    },
    get(sessionId) {
      return contexts.get(String(sessionId))
    },
    clear(sessionId) {
      contexts.delete(String(sessionId))
    },
  })
}
