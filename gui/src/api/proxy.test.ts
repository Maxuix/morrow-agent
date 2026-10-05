import { createHash } from 'node:crypto'
import { createServer as createHttpServer, request, type IncomingHttpHeaders } from 'node:http'
import type { AddressInfo, Socket } from 'node:net'
import { resolve } from 'node:path'
import { createServer } from 'vite'
import { expect, it } from 'vitest'

it('proxies HTTP and a real WebSocket upgrade with the Core same-origin headers', async () => {
  const headers: IncomingHttpHeaders[] = []
  const sockets = new Set<Socket>()
  let target = ''
  const valid = (value: IncomingHttpHeaders) => value.host === new URL(target).host &&
    value.origin === target && value.referer === `${target}/`
  const upstream = createHttpServer((req, res) => {
    headers.push(req.headers)
    res.writeHead(valid(req.headers) ? 200 : 403, { 'content-type': 'application/json' })
    res.end(JSON.stringify({ ok: valid(req.headers) }))
  })
  upstream.on('connection', socket => { sockets.add(socket); socket.on('close', () => sockets.delete(socket)) })
  upstream.on('upgrade', (req, socket) => {
    headers.push(req.headers)
    if (!valid(req.headers)) { socket.end('HTTP/1.1 403 Forbidden\r\n\r\n'); return }
    const accept = createHash('sha1').update(`${req.headers['sec-websocket-key']}258EAFA5-E914-47DA-95CA-C5AB0DC85B11`).digest('base64')
    socket.write(`HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: ${accept}\r\n\r\n`)
  })
  await new Promise<void>(done => upstream.listen(0, '127.0.0.1', done))
  target = `http://127.0.0.1:${(upstream.address() as AddressInfo).port}`
  const previousTarget = process.env.MORROW_API_PROXY
  process.env.MORROW_API_PROXY = target
  const vite = await createServer({
    configFile: resolve('vite.config.ts'),
    server: { host: '127.0.0.1', port: 0, hmr: false, watch: null },
    optimizeDeps: { noDiscovery: true, include: [] },
  })
  try {
    await vite.listen()
    const origin = `http://127.0.0.1:${(vite.httpServer!.address() as AddressInfo).port}`
    const response = await fetch(`${origin}/v1/meta`, { headers: { origin, referer: `${origin}/` } })
    expect(response.status).toBe(200)
    expect(await response.json()).toEqual({ ok: true })
    const key = 'dGhlIHNhbXBsZSBub25jZQ=='
    const expectedAccept = createHash('sha1').update(`${key}258EAFA5-E914-47DA-95CA-C5AB0DC85B11`).digest('base64')
    const handshake = await new Promise<{ status: number | undefined; accept: string | string[] | undefined }>((done, reject) => {
      const req = request(`${origin}/v1/events/stream`, {
        headers: { origin, referer: `${origin}/`, connection: 'Upgrade', upgrade: 'websocket', 'sec-websocket-version': '13', 'sec-websocket-key': key },
      })
      req.on('upgrade', (res, socket) => { socket.destroy(); done({ status: res.statusCode, accept: res.headers['sec-websocket-accept'] }) })
      req.on('response', res => { res.resume(); reject(new Error(`WebSocket upgrade rejected: ${res.statusCode}`)) })
      req.on('error', reject)
      req.setTimeout(5000, () => req.destroy(new Error('WebSocket upgrade timed out')))
      req.end()
    })
    expect(handshake).toEqual({ status: 101, accept: expectedAccept })
    expect(headers).toHaveLength(2)
    for (const value of headers) expect(valid(value)).toBe(true)
  } finally {
    await vite.close()
    sockets.forEach(socket => socket.destroy())
    await new Promise<void>((done, reject) => upstream.close(error => error ? reject(error) : done()))
    if (previousTarget === undefined) delete process.env.MORROW_API_PROXY
    else process.env.MORROW_API_PROXY = previousTarget
  }
}, 15000)
