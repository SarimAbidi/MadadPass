// Fallback decoder for the version-4/M, byte-mode codes issued by MadadPass.
const size = 33;
const masks = [
  (y, x) => (y + x) % 2 === 0,
  y => y % 2 === 0,
  (_, x) => x % 3 === 0,
  (y, x) => (y + x) % 3 === 0,
  (y, x) => (Math.floor(y / 2) + Math.floor(x / 3)) % 2 === 0,
  (y, x) => (y * x) % 2 + (y * x) % 3 === 0,
  (y, x) => ((y * x) % 2 + (y * x) % 3) % 2 === 0,
  (y, x) => ((y + x) % 2 + (y * x) % 3) % 2 === 0,
];

function reserved(y, x) {
  return (y <= 8 && (x <= 8 || x >= 25)) || (x <= 8 && y >= 25) || y === 6 || x === 6 ||
    (x >= 24 && x <= 28 && y >= 24 && y <= 28);
}

export function decodeMatrix(matrix) {
  for (const mask of masks) {
    const words = [];
    let up = true;
    let word = 0;
    let length = 0;
    for (let column = size - 1; column > 0; column -= 2) {
      if (column === 6) column--;
      for (let index = 0; index < size; index++) {
        const row = up ? size - 1 - index : index;
        for (const x of [column, column - 1]) {
          if (reserved(row, x)) continue;
          word = (word << 1) | Number(Boolean(matrix[row][x]) !== mask(row, x));
          if (++length === 8) {
            words.push(word);
            length = word = 0;
          }
        }
      }
      up = !up;
    }
    // Two 32-byte data blocks are interleaved before being placed in the grid.
    const interleaved = words.slice(0, 64);
    const data = interleaved.filter((_, index) => index % 2 === 0)
      .concat(interleaved.filter((_, index) => index % 2 === 1));
    if (data[0] >> 4 !== 4 || (((data[0] & 15) << 4) | (data[1] >> 4)) !== 46) continue;
    let token = '';
    for (let index = 1; index <= 46; index++) token += String.fromCharCode(((data[index] & 15) << 4) | (data[index + 1] >> 4));
    if (/^mp_[A-Za-z0-9_-]{43}$/.test(token)) return token;
  }
  return null;
}

export function decodeVoucherSvg(text) {
  const path = text.match(/<path d="((?:M\d{1,2},\d{1,2}h1v1h-1z)+)" fill="black"\s*\/>/)?.[1];
  if (!path) return null;
  const matrix = Array.from({ length: size }, () => Array(size).fill(false));
  for (const [, x, y] of path.matchAll(/M(\d+),(\d+)h1v1h-1z/g)) {
    if (x >= 4 && y >= 4 && x < 37 && y < 37) matrix[y - 4][x - 4] = true;
  }
  return decodeMatrix(matrix);
}

export function decodeVoucherImage(source) {
  const width = source.videoWidth || source.width;
  const height = source.videoHeight || source.height;
  if (!width || !height) return null;
  const scale = Math.min(1, 560 / Math.max(width, height));
  const canvas = document.createElement('canvas');
  canvas.width = Math.round(width * scale);
  canvas.height = Math.round(height * scale);
  const context = canvas.getContext('2d', { willReadFrequently: true });
  context.drawImage(source, 0, 0, canvas.width, canvas.height);
  const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
  const dark = (x, y) => {
    const index = (Math.floor(y) * canvas.width + Math.floor(x)) * 4;
    return pixels[index + 3] > 128 && (pixels[index] + pixels[index + 1] + pixels[index + 2]) < 285;
  };
  let left = canvas.width, right = -1, top = canvas.height, bottom = -1;
  for (let y = 0; y < canvas.height; y++) for (let x = 0; x < canvas.width; x++) {
    if (dark(x, y)) {
      left = Math.min(left, x); right = Math.max(right, x);
      top = Math.min(top, y); bottom = Math.max(bottom, y);
    }
  }
  const columns = right - left + 1, rows = bottom - top + 1;
  if (columns < size || rows < size || Math.abs(columns / rows - 1) > .14) return null;
  let matrix = Array.from({ length: size }, (_, y) => Array.from({ length: size }, (_, x) =>
    dark(left + (x + .5) * columns / size, top + (y + .5) * rows / size)));
  for (let rotation = 0; rotation < 4; rotation++) {
    const token = decodeMatrix(matrix);
    if (token) return token;
    matrix = matrix[0].map((_, index) => matrix.map(row => row[index]).reverse());
  }
  return null;
}
