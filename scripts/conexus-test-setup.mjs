// Hosted timeout tests assume a live server keeps the Node event loop running.
// A bare node:test process otherwise exits while AbortSignal.timeout is pending.
import { after } from 'node:test'
const keepAlive = setInterval(() => {}, 1000)
after(() => clearInterval(keepAlive))
