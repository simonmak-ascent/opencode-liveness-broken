/**
 * OpenCode server plugin: the agent itself is a supervised operation.
 *
 * The earlier design supervised tool calls only, so a stall *between* calls — which is where the agent
 * actually spends its time — was invisible. This registers one session-scoped operation that outlives
 * every tool call, and feeds it a heartbeat:
 *
 *   session op   remedy "interrupt", protected pid — the daemon may never signal the harness
 *   heartbeat    on each model request (ctx.session.hook("context")) and on each tool result
 *   verdict      no heartbeat for the agent profile budget means the agent is stalled, and the client
 *                is asked to interrupt the session instead of killing a process
 */
import { Plugin } from "@opencode/plugin"
import { connect } from "node:net"

const SOCKET = process.env.LIVENESS_SOCKET ?? "/tmp/opencode-liveness.sock"
const SESSION_OP = `session:${process.pid}`

function send(message: Record<string, unknown>): Promise<void> {
  return new Promise((resolve) => {
    const socket = connect(SOCKET)
    const done = () => resolve()
    socket.on("error", done) // a missing daemon must cost nothing
    socket.on("connect", () => socket.end(JSON.stringify(message) + "\n", done))
    setTimeout(done, 500)
  })
}

const beat = (unit: string) => send({ op: "progress", id: SESSION_OP, unit })

export default Plugin.define({
  id: "opencode-liveness",
  async setup(ctx) {
    await send({
      op: "register",
      id: SESSION_OP,
      pids: [process.pid],
      protected: [process.pid],
      remedy: "interrupt",
      profile: "agent",
    })

    // The agent's own heartbeat: one model request, one beat.
    await ctx.session.hook("context", async () => {
      await beat("model-request")
    })

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
      void send({ op: "progress", id: SESSION_OP, unit: "session-end", done: true })
    }
  },
})
