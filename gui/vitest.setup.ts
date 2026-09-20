/**
 * jsdom has no layout engine, but CodeMirror measures with client rects.
 * Empty rects keep the editor usable in unit tests; real layout, scrolling and
 * IME evidence comes from the browser check instead.
 */
const emptyRect = {
  x: 0,
  y: 0,
  top: 0,
  left: 0,
  right: 0,
  bottom: 0,
  width: 0,
  height: 0,
  toJSON: () => ({}),
} as DOMRect

// Most suites run in the plain node environment; only jsdom exposes these.
if (typeof Range !== 'undefined') {
  Range.prototype.getClientRects = () => [] as unknown as DOMRectList
  Range.prototype.getBoundingClientRect = () => emptyRect
}
if (typeof Element !== 'undefined') {
  Element.prototype.getClientRects = () => [] as unknown as DOMRectList
  Element.prototype.getBoundingClientRect = () => emptyRect
}
