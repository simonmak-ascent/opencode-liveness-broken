/**
 * OpenCode server plugin: the agent itself is a supervised operation, and the heartbeat is a timer.
 *
 * The previous version beat from `ctx.session.hook("context", …)` and produced no beats at all — the
 * hook never fired, so the supervisor saw an empty table and called it healthy. A 10-second interval
 * is the reliable signal: if the server's event loop freezes, ticks stop and NO-PROGRESS fires; if the
 * agent is merely slow between tool calls, ticks continue and nothing is interrupted.
 *
 * Registration is repeated on every tick, so a daemon restart self-heals within one beat instead of
 * erasing supervision for the rest of the session.
 */
import { Plugin } from "@opencode/plugin"
import { connect } from "node:net"

const SOCKET = process.env.LIVENESS_SOCKET ?? "/tmp/opencode-liveness.sock"
const OP_ID = `session:${process.pid}`
const TICK_MS = 10_000

function send(message: Record<string, unknown>): Promise<void> {
  return new Promise((resolve) => {
    const socket = connect(SOCKET)
    const done = () => resolve()
    socket.on("error", done) // a missing daemon must cost nothing
    socket.on("connect", () => socket.end(JSON.stringify(message) + "\n", done))
    setTimeout(done, 500)
  })
}

const registration = () => ({
  op: "register",
  id: OP_ID,
  pids: [process.pid],
  protected: [process.pid], // the daemon may never signal the harness
  remedy: "interrupt", // a stalled agent is interrupted, not killed
  profile: "agent",
})

const beat = (unit: string) => send({ op: "progress", id: OP_ID, unit })

export default Plugin.define({
  id: "opencode-liveness",
  async setup(ctx) {
    await send(registration())
    const timer = setInterval(() => {
      void send(registration())
      void beat("tick")
    }, TICK_MS)

    const tools = await ctx.tool.list()
    await ctx.tool.transform((editor) => {
      for (const tool of tools) {
        const original = tool.execute
        if (typeof original !== "function") continue
        editor.update(tool.id, (draft) => {
          draft.execute = async (input: unknown, context: any) => {
            await beat(`tool:${tool.id}:start`)
            try {
              return await original(input, context)
            } finally {
              await beat(`tool:${tool.id}:end`)
            }
          }
        })
      }
    })

    return () => {
      clearInterval(timer)
      void send({ op: "progress", id: OP_ID, unit: "session-end", done: true })
    }
  },
})
