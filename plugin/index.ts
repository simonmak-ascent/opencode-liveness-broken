/**
 * OpenCode server plugin: every tool call becomes a registered operation.
 *
 * V2 exposes hooks on session/model/HTTP domains and a `tool.transform` editor, so the wrapper is
 * installed by transforming each registered tool's `execute` rather than by a tool-level hook.
 *
 * Honest limitation: the plugin cannot see the *child* pid a tool spawns — it registers the server's
 * own tree. The daemon follows parent/child edges, so a tool's subprocess is sampled, but two tools
 * running concurrently are one operation from the daemon's point of view unless they register
 * themselves.
 */
import { Plugin } from "@opencode/plugin"
import { connect } from "node:net"

const SOCKET = process.env.LIVENESS_SOCKET ?? "/run/opencode-liveness.sock"

function send(message: Record<string, unknown>): Promise<void> {
  return new Promise((resolve) => {
    const socket = connect(SOCKET)
    const done = () => resolve()
    socket.on("error", done)
    socket.on("connect", () => socket.end(JSON.stringify(message) + "\n", done))
    setTimeout(done, 500) // never let the watchdog wrapper become the hang
  })
}

export default Plugin.define({
  id: "opencode-liveness",
  async setup(ctx) {
    const tools = await ctx.tool.list()

    await ctx.tool.transform((editor) => {
      for (const tool of tools) {
        const original = tool.execute
        if (typeof original !== "function") continue
        editor.update(tool.id, (draft) => {
          draft.execute = async (input: unknown, context: any) => {
            const id = `${tool.id}:${context?.messageID ?? Date.now()}`
            await send({ op: "register", id, pids: [process.pid], profile: "foreground" })
            try {
              const result = await original(input, context)
              await send({ op: "progress", id, unit: tool.id, done: true })
              return result
            } catch (error) {
              await send({ op: "progress", id, unit: `${tool.id}:error`, done: true })
              throw error
            }
          }
        })
      }
    })

    return () => {
      // registrations are disposed with the plugin runtime
    }
  },
})
