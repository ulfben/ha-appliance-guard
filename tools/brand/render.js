// Renders the Appliance Guard brand images: a shield holding a heartbeat line.
// Same shield as Fuse Guard (ulfben/ha-fuse-guard), so the two read as a family.
//
//   npm install @resvg/resvg-js@2.6.2
//   node tools/brand/render.js custom_components/appliance_guard/brand
//
// The wordmark uses Segoe UI (Windows); another system font changes the logo.
const { Resvg } = require("@resvg/resvg-js");
const fs = require("fs");
const path = require("path");

const out = process.argv[2];
fs.mkdirSync(out, { recursive: true });

// Shield is 220 x 256 in its own units.
const shield = `
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#3CC47C"/>
      <stop offset="1" stop-color="#0B6B3A"/>
    </linearGradient>
  </defs>
  <path d="M110 2 L206 36 Q216 40 216 50 L216 118 Q216 202 110 254 Q4 202 4 118 L4 50 Q4 40 14 36 Z"
        fill="url(#g)"/>
  <path d="M110 14 L200 46 Q205 48 205 54 L205 118 Q205 194 110 242 Q15 194 15 118 L15 54 Q15 48 20 46 Z"
        fill="none" stroke="#FFFFFF" stroke-opacity="0.18" stroke-width="3"/>
  <polyline points="26,132 72,132 90,98 112,176 136,70 154,132 176,132"
            fill="none" stroke="#FFFFFF" stroke-width="16"
            stroke-linecap="round" stroke-linejoin="round"/>
  <circle cx="190" cy="132" r="11" fill="#FFC107"/>
`;

function render(svg, file, opts = {}) {
  const resvg = new Resvg(svg, {
    fitTo: opts.width ? { mode: "width", value: opts.width } : { mode: "original" },
    font: { loadSystemFonts: true, defaultFontFamily: "Segoe UI" },
  });
  if (opts.crop) {
    const bbox = resvg.getBBox();
    if (bbox) resvg.cropByBBox(bbox);
  }
  const png = resvg.render().asPng();
  fs.writeFileSync(path.join(out, file), png);
  console.log(file, png.length, "bytes");
}

function icon(size, file) {
  // Square canvas; the shield fills the height, centred horizontally.
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 256 256">
    <g transform="translate(18 0)">${shield}</g></svg>`;
  render(svg, file);
}

function logo(height, file, textColor) {
  // Shield, then the wordmark. Measure the drawing first, then render it
  // trimmed to that box at the wanted height.
  const body = `${shield}
    <text x="262" y="176" font-family="Segoe UI" font-weight="600" font-size="150"
          fill="${textColor}">Appliance Guard</text>`;
  const probe = new Resvg(
    `<svg xmlns="http://www.w3.org/2000/svg" width="1700" height="256" viewBox="0 0 1700 256">${body}</svg>`,
    { font: { loadSystemFonts: true, defaultFontFamily: "Segoe UI" } },
  );
  const b = probe.getBBox();
  const width = Math.round((height * b.width) / b.height);
  render(
    `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}"
       viewBox="${b.x} ${b.y} ${b.width} ${b.height}">${body}</svg>`,
    file,
    { width },
  );
}

icon(256, "icon.png");
icon(512, "icon@2x.png");
logo(256, "logo.png", "#1F2933");
logo(512, "logo@2x.png", "#1F2933");
logo(256, "dark_logo.png", "#F5F7FA");
logo(512, "dark_logo@2x.png", "#F5F7FA");
