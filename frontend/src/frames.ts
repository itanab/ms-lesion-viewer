/** Decoded-slice cache (bounded) and request de-duplication. Scan pixels live only in memory, never localStorage. */

const MAX_BYTES = 128 * 1024 * 1024;

const cache = new Map<string, ImageBitmap>();
const inflight = new Map<string, Promise<ImageBitmap>>();
let bytes = 0;

const size = (b: ImageBitmap) => b.width * b.height * 4;

function put(url: string, bmp: ImageBitmap) {
  const old = cache.get(url);
  if (old) bytes -= size(old);
  cache.set(url, bmp);
  bytes += size(bmp);
  while (bytes > MAX_BYTES && cache.size > 1) {
    const [k, v] = cache.entries().next().value as [string, ImageBitmap];
    cache.delete(k);
    bytes -= size(v);
    // do not close(): a displayed frame may still reference it
  }
}

export function cached(url: string): ImageBitmap | undefined {
  const b = cache.get(url);
  if (b) {
    cache.delete(url); // refresh LRU order
    cache.set(url, b);
  }
  return b;
}

export function loadBitmap(url: string): Promise<ImageBitmap> {
  const hit = cached(url);
  if (hit) return Promise.resolve(hit);
  const pending = inflight.get(url);
  if (pending) return pending;
  const p = fetch(url)
    .then(async (r) => {
      if (!r.ok) throw new Error(`slice request failed (${r.status})`);
      return createImageBitmap(await r.blob());
    })
    .then((b) => {
      put(url, b);
      return b;
    })
    .finally(() => inflight.delete(url));
  inflight.set(url, p);
  return p;
}

export function clearFrames() {
  cache.clear();
  inflight.clear();
  bytes = 0;
}

export function cacheBytes() {
  return bytes;
}

const outlines = new WeakMap<ImageBitmap, ImageBitmap>();

/** One-raster-pixel outline of an RGBA mask slice (pixel-exact, for the Outline overlay style). */
export function outlineOf(mask: ImageBitmap): ImageBitmap {
  const hit = outlines.get(mask);
  if (hit) return hit;
  const w = mask.width, h = mask.height;
  const c = new OffscreenCanvas(w, h);
  const g = c.getContext("2d")!;
  g.drawImage(mask, 0, 0);
  const src = g.getImageData(0, 0, w, h);
  const out = g.createImageData(w, h);
  const on = (x: number, y: number) => x >= 0 && y >= 0 && x < w && y < h && src.data[(y * w + x) * 4 + 3] > 0;
  for (let y = 0; y < h; y++)
    for (let x = 0; x < w; x++) {
      if (on(x, y) && !(on(x - 1, y) && on(x + 1, y) && on(x, y - 1) && on(x, y + 1))) {
        const i = (y * w + x) * 4;
        out.data[i] = 255; out.data[i + 1] = 128; out.data[i + 2] = 0; out.data[i + 3] = 255;
      }
    }
  g.putImageData(out, 0, 0);
  const bmp = c.transferToImageBitmap();
  outlines.set(mask, bmp);
  return bmp;
}
