/**
 * Generate the Huawei ICT Competition (Track 3) preliminary-round design document.
 *
 * Follows the docx skill's create route:
 *   report scene -> cover recipe R1 + MC-1 palette, TOC in its own section,
 *   3-section page numbering, Profile A formal fonts (SimHei / SimSun / TNR).
 */
const fs = require("fs");
const path = require("path");
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  WidthType, BorderStyle, AlignmentType, HeadingLevel, PageBreak,
  TableOfContents, Footer, PageNumber, ShadingType, TableLayoutType,
  SectionType, NumberFormat,
} = require("docx");

const SRC = process.argv[2] || path.join(__dirname, "content.json");
const OUT = process.argv[3] || path.join(__dirname, "..", "Nexent-KnowEvo-开发设计文档-初赛提交版.docx");
const data = JSON.parse(fs.readFileSync(SRC, "utf8"));

// ── Palette: MC-1 (Medical Blue) — healthcare / clinical report ──────────────
const PAL = {
  bg: "F5F8FC",
  primary: "1A5276",
  accent: "2E86C1",
  cover: { titleColor: "1A5276", subtitleColor: "606060", metaColor: "707070", footerColor: "A0A0A0" },
  table: { headerBg: "2E86C1", headerText: "FFFFFF", accentLine: "1A5276", innerLine: "D0DDE8", surface: "EDF3F8" },
};
const P = PAL;

const NB = { style: BorderStyle.NONE, size: 0, color: "FFFFFF" };
const noBorders = { top: NB, bottom: NB, left: NB, right: NB };
const allNoBorders = { top: NB, bottom: NB, left: NB, right: NB, insideHorizontal: NB, insideVertical: NB };

const FONTS = {
  h: { eastAsia: "SimHei", ascii: "Times New Roman" },
  body: { eastAsia: "SimSun", ascii: "Times New Roman" },
  mono: { eastAsia: "SimSun", ascii: "Courier New" },
  cover: { eastAsia: "Microsoft YaHei", ascii: "Arial" },
};
const PAGE = { size: { width: 11906, height: 16838 }, margin: { top: 1440, bottom: 1440, left: 1701, right: 1417 } };

// ── Cover helpers (design-system.md) ────────────────────────────────────────
function splitTitleLines(title, charsPerLine) {
  if (title.length <= charsPerLine) return [title];
  const breakAfter = new Set([..."，。、；：！？", ..."的与和及之在于为", ..."-_—–·/", ..." \t"]);
  const lines = [];
  let remaining = title;
  while (remaining.length > charsPerLine) {
    let breakAt = -1;
    for (let i = charsPerLine; i >= Math.floor(charsPerLine * 0.6); i--) {
      if (i < remaining.length && breakAfter.has(remaining[i - 1])) { breakAt = i; break; }
    }
    if (breakAt === -1) {
      const limit = Math.min(remaining.length, Math.ceil(charsPerLine * 1.3));
      for (let i = charsPerLine + 1; i < limit; i++) {
        if (breakAfter.has(remaining[i - 1])) { breakAt = i; break; }
      }
    }
    if (breakAt === -1) {
      breakAt = charsPerLine;
      const prev = remaining[breakAt - 1], next = remaining[breakAt];
      if (prev && next && !breakAfter.has(prev) && !breakAfter.has(next) &&
          /[\u4e00-\u9fff]/.test(prev) && /[\u4e00-\u9fff]/.test(next)) breakAt -= 1;
    }
    lines.push(remaining.slice(0, breakAt).trim());
    remaining = remaining.slice(breakAt).trim();
  }
  if (remaining) lines.push(remaining);
  if (lines.length > 1 && lines[lines.length - 1].length <= 2) {
    lines[lines.length - 2] += lines.pop();
  }
  return lines;
}

function calcTitleLayout(title, maxWidthTwips, preferredPt = 40, minPt = 24) {
  const charWidth = (pt) => pt * 20;
  const charsPerLine = (pt) => Math.floor(maxWidthTwips / charWidth(pt));
  let titlePt = preferredPt, lines;
  while (titlePt >= minPt) {
    const cpl = charsPerLine(titlePt);
    if (cpl < 2) { titlePt -= 2; continue; }
    lines = splitTitleLines(title, cpl);
    if (lines.length <= 3) break;
    titlePt -= 2;
  }
  if (!lines || lines.length > 3) { lines = splitTitleLines(title, charsPerLine(minPt)); titlePt = minPt; }
  return { titlePt, titleLines: lines };
}

function calcCoverSpacing(params) {
  const {
    titleLineCount = 1, titlePt = 36, hasSubtitle = false, hasEnglishLabel = false,
    metaLineCount = 0, fixedHeight = 800, pageHeight = 16838, marginTop = 0, marginBottom = 0,
  } = params;
  const SAFETY = 1200;
  const usableHeight = pageHeight - marginTop - marginBottom - SAFETY;
  const titleHeight = titleLineCount * (titlePt * 23 + 200);
  const subtitleHeight = hasSubtitle ? (12 * 23 + 600) : 0;
  const englishLabelHeight = hasEnglishLabel ? (9 * 23 + 600) : 0;
  const metaHeight = metaLineCount * (10 * 23 + 100);
  const implicitParaHeight = 3 * 300;
  const contentHeight = titleHeight + subtitleHeight + englishLabelHeight + metaHeight + fixedHeight + implicitParaHeight;
  const safeRemaining = Math.max(usableHeight - contentHeight, 400);
  const FOOTER_MIN = 800;
  const rawBottom = Math.floor(safeRemaining * 0.45);
  const bottomSpacing = Math.max(rawBottom, FOOTER_MIN);
  const rawTop = Math.floor(safeRemaining * 0.45);
  const topSpacing = Math.max(rawTop - Math.max(0, FOOTER_MIN - rawBottom), 400);
  const midSpacing = Math.max(safeRemaining - topSpacing - bottomSpacing, 0);
  return { topSpacing, midSpacing, bottomSpacing };
}

// Cover recipe R1 — Pure Paragraph Cover (left-aligned), single 16838 wrapper.
function buildCoverR1(config) {
  const C = P.cover;
  const padL = 1200, padR = 800;
  const availableWidth = 11906 - padL - padR - 300;
  const { titlePt, titleLines } = calcTitleLayout(config.title, availableWidth, 40, 24);
  const titleSize = titlePt * 2;
  const spacing = calcCoverSpacing({
    titleLineCount: titleLines.length, titlePt,
    hasSubtitle: !!config.subtitle, hasEnglishLabel: !!config.englishLabel,
    metaLineCount: (config.metaLines || []).length, fixedHeight: 400,
  });

  const accentLeft = { style: BorderStyle.SINGLE, size: 8, color: P.accent, space: 12 };
  const ch = [];

  ch.push(new Paragraph({ spacing: { before: spacing.topSpacing } }));

  if (config.englishLabel) {
    ch.push(new Paragraph({
      indent: { left: padL, right: padR }, spacing: { after: 500 },
      border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: P.accent, space: 8 } },
      children: [new TextRun({
        text: config.englishLabel, size: 18, color: P.accent,
        font: { ascii: "Calibri", eastAsia: "SimHei" }, characterSpacing: 20,
      })],
    }));
  }

  for (let i = 0; i < titleLines.length; i++) {
    ch.push(new Paragraph({
      indent: { left: padL },
      spacing: { after: i < titleLines.length - 1 ? 100 : 300, line: Math.ceil(titlePt * 23), lineRule: "atLeast" },
      children: [new TextRun({
        text: titleLines[i], size: titleSize, bold: true,
        color: C.titleColor, font: { eastAsia: "SimHei", ascii: "Arial" },
      })],
    }));
  }

  if (config.subtitle) {
    ch.push(new Paragraph({
      indent: { left: padL }, spacing: { after: 800, line: 320, lineRule: "atLeast" },
      children: [new TextRun({
        text: config.subtitle, size: 24, color: C.subtitleColor, font: FONTS.cover,
      })],
    }));
  }

  for (const line of (config.metaLines || [])) {
    ch.push(new Paragraph({
      indent: { left: padL + 200 }, spacing: { after: 80 },
      border: { left: accentLeft },
      children: [new TextRun({ text: line, size: 22, color: C.metaColor, font: FONTS.cover })],
    }));
  }

  ch.push(new Paragraph({ spacing: { before: spacing.bottomSpacing } }));

  // Footer line: top accent separator + borderless 2-column table for alignment
  // (tab stops render inconsistently between Word and WPS, so use a table).
  ch.push(new Table({
    width: { size: 100, type: WidthType.PERCENTAGE },
    layout: TableLayoutType.FIXED,
    borders: allNoBorders,
    rows: [new TableRow({
      children: [
        new TableCell({
          width: { size: 60, type: WidthType.PERCENTAGE }, borders: noBorders,
          margins: { top: 0, bottom: 0, left: padL, right: 0 },
          children: [new Paragraph({
            spacing: { before: 200 },
            border: { top: { style: BorderStyle.SINGLE, size: 2, color: P.accent, space: 8 } },
            children: [new TextRun({ text: config.footerLeft || "", size: 16, color: C.footerColor, font: FONTS.cover })],
          })],
        }),
        new TableCell({
          width: { size: 40, type: WidthType.PERCENTAGE }, borders: noBorders,
          margins: { top: 0, bottom: 0, left: 0, right: 400 },
          children: [new Paragraph({
            alignment: AlignmentType.RIGHT,
            spacing: { before: 200 },
            border: { top: { style: BorderStyle.SINGLE, size: 2, color: P.accent, space: 8 } },
            children: [new TextRun({ text: config.footerRight || "", size: 16, color: C.footerColor, font: FONTS.cover })],
          })],
        }),
      ],
    })],
  }));

  return [new Table({
    width: { size: 100, type: WidthType.PERCENTAGE },
    layout: TableLayoutType.FIXED,
    borders: allNoBorders,
    rows: [new TableRow({
      height: { value: 16838, rule: "exact" },
      children: [new TableCell({
        shading: { type: ShadingType.CLEAR, fill: P.bg }, borders: noBorders,
        children: ch,
      })],
    })],
  })];
}

// ── Body component builders ────────────────────────────────────────────────
function heading(text, level) {
  const isH1 = level === HeadingLevel.HEADING_1;
  const size = isH1 ? 32 : 28;
  return new Paragraph({
    heading: level,
    alignment: isH1 ? AlignmentType.CENTER : AlignmentType.LEFT,
    keepNext: true,
    spacing: { before: isH1 ? 400 : 300, after: isH1 ? 200 : 140, line: Math.ceil((size / 2) * 23), lineRule: "atLeast" },
    children: [new TextRun({
      text, bold: true, size, color: P.primary, font: FONTS.h,
    })],
  });
}

function body(text) {
  return new Paragraph({
    alignment: AlignmentType.JUSTIFIED,
    indent: { firstLine: 480 },
    spacing: { line: 312, after: 80 },
    children: [new TextRun({ text, size: 24, color: "000000", font: FONTS.body })],
  });
}

function notePara(text) {
  return new Paragraph({
    alignment: AlignmentType.JUSTIFIED,
    spacing: { line: 312, before: 80, after: 120 },
    indent: { left: 240, right: 120 },
    border: { left: { style: BorderStyle.SINGLE, size: 12, color: P.accent, space: 10 } },
    shading: { type: ShadingType.CLEAR, fill: P.table.surface },
    children: [new TextRun({ text, size: 21, color: "595959", font: FONTS.body })],
  });
}

function codeBlock(text) {
  const lines = text.split("\n");
  return lines.map((ln, i) => new Paragraph({
    alignment: AlignmentType.LEFT,
    spacing: { line: 260, after: i === lines.length - 1 ? 160 : 0, before: i === 0 ? 120 : 0 },
    indent: { left: 240 },
    shading: { type: ShadingType.CLEAR, fill: P.table.surface },
    children: [new TextRun({ text: ln === "" ? " " : ln, size: 18, color: "1F3864", font: FONTS.mono })],
  }));
}

function tableBlock(headers, rows, widths) {
  const n = headers.length;
  const w = (widths && widths.length === n)
    ? widths
    : Array.from({ length: n }, () => Math.floor(100 / n));
  const cellMargins = { top: 60, bottom: 60, left: 120, right: 120 };

  const headerRow = new TableRow({
    tableHeader: true,
    cantSplit: true,
    children: headers.map((h, i) => new TableCell({
      width: { size: w[i], type: WidthType.PERCENTAGE },
      shading: { type: ShadingType.CLEAR, fill: P.table.headerBg },
      margins: cellMargins,
      children: [new Paragraph({
        alignment: AlignmentType.CENTER,
        spacing: { line: 312 },
        children: [new TextRun({ text: h, bold: true, size: 21, color: P.table.headerText, font: FONTS.h })],
      })],
    })),
  });

  const bodyRows = rows.map((r) => new TableRow({
    cantSplit: true,
    children: r.map((c, i) => new TableCell({
      width: { size: w[i], type: WidthType.PERCENTAGE },
      margins: cellMargins,
      children: [new Paragraph({
        alignment: AlignmentType.LEFT,
        spacing: { line: 312 },
        children: [new TextRun({ text: c, size: 21, color: "000000", font: FONTS.body })],
      })],
    })),
  }));

  return new Table({
    width: { size: 100, type: WidthType.PERCENTAGE },
    layout: TableLayoutType.FIXED,
    borders: {
      top: { style: BorderStyle.SINGLE, size: 4, color: P.table.accentLine },
      bottom: { style: BorderStyle.SINGLE, size: 4, color: P.table.accentLine },
      left: NB, right: NB,
      insideHorizontal: { style: BorderStyle.SINGLE, size: 1, color: P.table.innerLine },
      insideVertical: NB,
    },
    rows: [headerRow, ...bodyRows],
  });
}

// Numbered lists need unique references so counters restart per list.
let numRefSeq = 0;
const numberingConfig = [];

function listPara(text, kind, ref) {
  const common = {
    alignment: AlignmentType.JUSTIFIED,
    spacing: { line: 312, after: 60 },
    children: [new TextRun({ text, size: 24, color: "000000", font: FONTS.body })],
  };
  if (kind === "numbered") return new Paragraph({ ...common, numbering: { reference: ref, level: 0 } });
  return new Paragraph({ ...common, bullet: { level: 0 } });
}

// ── Assemble body blocks ──────────────────────────────────────────────────
const bodyChildren = [];
for (const b of data.blocks) {
  switch (b.type) {
    case "h1": bodyChildren.push(heading(b.text, HeadingLevel.HEADING_1)); break;
    case "h2": bodyChildren.push(heading(b.text, HeadingLevel.HEADING_2)); break;
    case "p": bodyChildren.push(body(b.text)); break;
    case "note": bodyChildren.push(notePara(b.text)); break;
    case "code": bodyChildren.push(...codeBlock(b.text)); break;
    case "table": bodyChildren.push(tableBlock(b.headers, b.rows, b.widths)); break;
    case "bullets": b.items.forEach((it) => bodyChildren.push(listPara(it, "bullets"))); break;
    case "numbered": {
      const ref = `numlist${++numRefSeq}`;
      numberingConfig.push({
        reference: ref,
        levels: [{
          level: 0, format: "decimal", text: "%1.", alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 720, hanging: 360 } } },
        }],
      });
      b.items.forEach((it) => bodyChildren.push(listPara(it, "numbered", ref)));
      break;
    }
    default: throw new Error("unknown block type: " + b.type);
  }
}

// ── Page-number footer ────────────────────────────────────────────────────
function pageNumFooter() {
  return new Footer({
    children: [new Paragraph({
      alignment: AlignmentType.CENTER,
      spacing: { before: 100 },
      children: [new TextRun({ children: [PageNumber.CURRENT], size: 18, color: "606060", font: FONTS.body })],
    })],
  });
}

// ── TOC page (front matter) ───────────────────────────────────────────────
const frontMatter = [
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 480, after: 360, line: 400, lineRule: "atLeast" },
    children: [new TextRun({ text: "目　　录", bold: true, size: 32, color: "000000", font: FONTS.h })],
  }),
  new TableOfContents("Table of Contents", { hyperlink: true, headingStyleRange: "1-3" }),
  new Paragraph({
    spacing: { before: 200 },
    children: [new TextRun({
      text: "说明：本目录由 Word 域代码生成。编辑文档后，请在目录上右键选择「更新域」以刷新页码。",
      italics: true, size: 18, color: "888888", font: FONTS.body,
    })],
  }),
];

const doc = new Document({
  numbering: { config: numberingConfig },
  styles: {
    default: {
      document: {
        run: { font: { ascii: "Times New Roman", eastAsia: "SimSun" }, size: 24, color: "000000" },
        paragraph: { spacing: { line: 312 } },
      },
    },
  },
  sections: [
    // Section 1 — cover (no page number, margin 0)
    {
      properties: { page: { size: { width: 11906, height: 16838 }, margin: { top: 0, bottom: 0, left: 0, right: 0 } } },
      children: buildCoverR1({ ...data.cover, palette: P }),
    },
    // Section 2 — front matter (TOC), Roman numerals
    {
      properties: {
        type: SectionType.NEXT_PAGE,
        page: { ...PAGE, pageNumbers: { start: 1, formatType: NumberFormat.UPPER_ROMAN } },
      },
      footers: { default: pageNumFooter() },
      children: frontMatter,
    },
    // Section 3 — body, Arabic numerals starting at 1
    {
      properties: {
        type: SectionType.NEXT_PAGE,
        page: { ...PAGE, pageNumbers: { start: 1, formatType: NumberFormat.DECIMAL } },
      },
      footers: { default: pageNumFooter() },
      children: bodyChildren,
    },
  ],
});

Packer.toBuffer(doc).then((buf) => {
  fs.writeFileSync(OUT, buf);
  console.log("wrote", OUT, buf.length, "bytes");
});
