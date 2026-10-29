/**
 * Minimal vector PDF writer: pages, filled/stroked shapes, lines and text in
 * the standard Helvetica fonts.  No dependencies; enough for the timetable.
 *
 * Coordinates are in points with the origin at the top-left corner of the
 * page (PDF's own origin is bottom-left; the writer flips them).
 */

export const A4_LANDSCAPE = { width: 841.89, height: 595.28 };

const FONTS = { regular: "F1", bold: "F2" };
const KAPPA = 0.5523;

// WinAnsiEncoding characters outside Latin-1.
const WIN_ANSI = {
  "€": 0x80, "‚": 0x82, "„": 0x84, "…": 0x85, "‘": 0x91, "’": 0x92,
  "“": 0x93, "”": 0x94, "•": 0x95, "–": 0x96, "—": 0x97, "™": 0x99,
};

// Text as WinAnsi bytes (one char per byte); emojis are dropped.
function winAnsi(text) {
  let out = "";
  for (const char of String(text ?? "").normalize("NFC")) {
    const code = char.codePointAt(0);
    if (WIN_ANSI[char]) out += String.fromCharCode(WIN_ANSI[char]);
    else if ((code >= 0x20 && code <= 0x7e) || (code >= 0xa0 && code <= 0xff)) out += char;
    else if (code <= 0xffff && !/[☀-➿️]/.test(char)) out += "?";
  }
  return out;
}

const pdfString = (bytes) => `(${bytes.replace(/[\\()]/g, (char) => `\\${char}`)})`;
const num = (value) => Number(value.toFixed(2)).toString();

export function hexToRgb(hex) {
  const value = /^#?([0-9a-f]{6})$/i.exec(hex ?? "")?.[1] ?? "000000";
  return [0, 2, 4].map((index) => parseInt(value.slice(index, index + 2), 16) / 255);
}

// ``amount`` of ``color`` over ``base`` (both [r, g, b] in 0..1).
export function mix(color, base, amount) {
  return color.map((channel, index) => channel * amount + base[index] * (1 - amount));
}

let measureContext = null;

// Width in points of ``text`` at ``size`` points (Helvetica ≈ Arial metrics).
export function textWidth(text, size, font = "regular") {
  measureContext ??= document.createElement("canvas").getContext("2d");
  measureContext.font = `${font === "bold" ? "bold " : ""}${size}px Helvetica, Arial, sans-serif`;
  return measureContext.measureText(text).width;
}

// Cut ``text`` with an ellipsis so it fits in ``width``.
export function fitText(text, width, size, font) {
  if (textWidth(text, size, font) <= width) return text;
  let cut = text;
  while (cut && textWidth(`${cut}…`, size, font) > width) cut = cut.slice(0, -1);
  return cut ? `${cut.trimEnd()}…` : "";
}

// Word-wrap ``text`` into at most ``maxLines`` lines of ``width``.
export function wrapText(text, width, size, font, maxLines) {
  const lines = [];
  let line = "";
  const words = String(text).split(/\s+/).filter(Boolean);
  for (let index = 0; index < words.length; index += 1) {
    const candidate = line ? `${line} ${words[index]}` : words[index];
    if (!line || textWidth(candidate, size, font) <= width) {
      line = candidate;
      continue;
    }
    if (lines.length === maxLines - 1) {
      lines.push(fitText(`${line} ${words.slice(index).join(" ")}`, width, size, font));
      return lines;
    }
    lines.push(fitText(line, width, size, font));
    line = words[index];
  }
  if (line) lines.push(fitText(line, width, size, font));
  return lines.slice(0, maxLines);
}

export class PdfDocument {
  constructor({ width, height } = A4_LANDSCAPE, { title = "" } = {}) {
    this.width = width;
    this.height = height;
    this.title = title;
    this.pages = [];
  }

  addPage() {
    this.ops = [];
    this.pages.push(this.ops);
    return this;
  }

  #y(y) {
    return this.height - y;
  }

  #paint({ fill, stroke, lineWidth = 0.6, dash }) {
    if (fill) this.ops.push(`${fill.map(num).join(" ")} rg`);
    if (stroke) this.ops.push(`${stroke.map(num).join(" ")} RG ${num(lineWidth)} w [${dash ? dash.join(" ") : ""}] 0 d`);
    this.ops.push(fill && stroke ? "B" : fill ? "f" : "S");
  }

  rect(x, y, width, height, style) {
    this.ops.push(`${num(x)} ${num(this.#y(y + height))} ${num(width)} ${num(height)} re`);
    this.#paint(style);
    return this;
  }

  roundedRect(x, y, width, height, radius, style) {
    const r = Math.min(radius, width / 2, height / 2);
    const k = r * KAPPA;
    const [left, right, top, bottom] = [x, x + width, this.#y(y), this.#y(y + height)];
    this.ops.push(
      [
        `${num(left + r)} ${num(top)} m`,
        `${num(right - r)} ${num(top)} l`,
        `${num(right - r + k)} ${num(top)} ${num(right)} ${num(top - r + k)} ${num(right)} ${num(top - r)} c`,
        `${num(right)} ${num(bottom + r)} l`,
        `${num(right)} ${num(bottom + r - k)} ${num(right - r + k)} ${num(bottom)} ${num(right - r)} ${num(bottom)} c`,
        `${num(left + r)} ${num(bottom)} l`,
        `${num(left + r - k)} ${num(bottom)} ${num(left)} ${num(bottom + r - k)} ${num(left)} ${num(bottom + r)} c`,
        `${num(left)} ${num(top - r)} l`,
        `${num(left)} ${num(top - r + k)} ${num(left + r - k)} ${num(top)} ${num(left + r)} ${num(top)} c`,
        "h",
      ].join(" "),
    );
    this.#paint(style);
    return this;
  }

  line(x1, y1, x2, y2, style) {
    this.ops.push(`${num(x1)} ${num(this.#y(y1))} m ${num(x2)} ${num(this.#y(y2))} l`);
    this.#paint({ ...style, fill: null });
    return this;
  }

  // ``y`` is the text baseline; ``align`` is left, center or right.
  text(value, x, y, { size = 10, font = "regular", color = [0, 0, 0], align = "left" } = {}) {
    if (!value) return this;
    const width = align === "left" ? 0 : textWidth(value, size, font);
    const left = align === "right" ? x - width : align === "center" ? x - width / 2 : x;
    this.ops.push(
      `BT /${FONTS[font]} ${num(size)} Tf ${color.map(num).join(" ")} rg ${num(left)} ${num(this.#y(y))} Td ${pdfString(winAnsi(value))} Tj ET`,
    );
    return this;
  }

  toBlob() {
    const objects = [];
    const add = (body) => objects.push(body) && objects.length;
    const catalog = add(null);
    const pagesId = add(null);
    const regular = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>");
    const bold = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>");
    const info = add(`<< /Title ${pdfString(winAnsi(this.title))} /Producer (HUSC) >>`);
    const pageIds = this.pages.map((ops) => {
      const stream = ops.join("\n");
      const content = add(`<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`);
      return add(
        `<< /Type /Page /Parent ${pagesId} 0 R /MediaBox [0 0 ${num(this.width)} ${num(this.height)}] ` +
          `/Resources << /Font << /F1 ${regular} 0 R /F2 ${bold} 0 R >> >> /Contents ${content} 0 R >>`,
      );
    });
    objects[catalog - 1] = `<< /Type /Catalog /Pages ${pagesId} 0 R >>`;
    objects[pagesId - 1] = `<< /Type /Pages /Kids [${pageIds.map((id) => `${id} 0 R`).join(" ")}] /Count ${pageIds.length} >>`;

    let output = "%PDF-1.4\n%\xe2\xe3\xcf\xd3\n";
    const offsets = objects.map((body, index) => {
      const offset = output.length;
      output += `${index + 1} 0 obj\n${body}\nendobj\n`;
      return offset;
    });
    const xref = output.length;
    output += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
    output += offsets.map((offset) => `${String(offset).padStart(10, "0")} 00000 n \n`).join("");
    output += `trailer\n<< /Size ${objects.length + 1} /Root ${catalog} 0 R /Info ${info} 0 R >>\nstartxref\n${xref}\n%%EOF\n`;

    const bytes = new Uint8Array(output.length);
    for (let index = 0; index < output.length; index += 1) bytes[index] = output.charCodeAt(index);
    return new Blob([bytes], { type: "application/pdf" });
  }
}
