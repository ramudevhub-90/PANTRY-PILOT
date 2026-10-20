// AWS event-stream framing, the binary format Amazon Transcribe streaming uses over WebSocket.
// Message: [total length][headers length][prelude CRC][headers][payload][message CRC], big-endian.

const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    table[n] = c >>> 0;
  }
  return table;
})();

function crc32(bytes: Uint8Array): number {
  let crc = 0xffffffff;
  for (const b of bytes) crc = CRC_TABLE[(crc ^ b) & 0xff] ^ (crc >>> 8);
  return (crc ^ 0xffffffff) >>> 0;
}

const encoder = new TextEncoder();
const decoder = new TextDecoder();

/** Encodes string headers (type 7) and a payload into one event-stream message. */
export function encodeMessage(headers: Record<string, string>, payload: Uint8Array): Uint8Array<ArrayBuffer> {
  const parts: number[] = [];
  for (const [name, value] of Object.entries(headers)) {
    const n = encoder.encode(name);
    const v = encoder.encode(value);
    parts.push(n.length, ...n, 7, v.length >> 8, v.length & 0xff, ...v);
  }
  const headerBytes = Uint8Array.from(parts);
  const total = 12 + headerBytes.length + payload.length + 4;
  const out = new Uint8Array(total);
  const view = new DataView(out.buffer);
  view.setUint32(0, total);
  view.setUint32(4, headerBytes.length);
  view.setUint32(8, crc32(out.subarray(0, 8)));
  out.set(headerBytes, 12);
  out.set(payload, 12 + headerBytes.length);
  view.setUint32(total - 4, crc32(out.subarray(0, total - 4)));
  return out;
}

export interface DecodedMessage {
  headers: Record<string, string>;
  payload: Uint8Array;
}

/** Decodes one message; only string headers are read (all Transcribe sends). */
export function decodeMessage(data: ArrayBuffer): DecodedMessage {
  const bytes = new Uint8Array(data);
  const view = new DataView(data);
  const total = view.getUint32(0);
  const headersLength = view.getUint32(4);
  const headers: Record<string, string> = {};
  let i = 12;
  const end = 12 + headersLength;
  while (i < end) {
    const nameLength = bytes[i++];
    const name = decoder.decode(bytes.subarray(i, i + nameLength));
    i += nameLength;
    const type = bytes[i++];
    if (type !== 7) break;
    const valueLength = view.getUint16(i);
    i += 2;
    headers[name] = decoder.decode(bytes.subarray(i, i + valueLength));
    i += valueLength;
  }
  return { headers, payload: bytes.subarray(end, total - 4) };
}

/** One chunk of 16-bit PCM audio as a Transcribe AudioEvent. */
export function audioEvent(pcm: Uint8Array): Uint8Array<ArrayBuffer> {
  return encodeMessage({ ':message-type': 'event', ':event-type': 'AudioEvent', ':content-type': 'application/octet-stream' }, pcm);
}
