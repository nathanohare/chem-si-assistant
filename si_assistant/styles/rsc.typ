// Chem SI Assistant — RSC style (Chem. Sci., Org. Biomol. Chem., Chem. Commun.).
// A4 paper, numbered section headings, citations in RSC format.
// A journal style is a plain Typst template: copy this file to make a new style.

#let si-font = ("Times New Roman", "Liberation Serif", "Libertinus Serif")
#let si-head-font = ("Arial", "Liberation Sans", "Helvetica", "DejaVu Sans")

// A header or footer line (Project settings ▸ Page). `f` is a function
// (page, pages) => (left, centre, right) given the formatted page numbers. Nothing is
// shown on the cover (page 1) unless `on-cover`.
#let si-page-line(f, fmt, size, bold, on-cover) = context {
  if here().page() > 1 or on-cover {
    set par(justify: false)
    let num(n) = {
      let t = text(weight: if bold { "bold" } else { "regular" }, fmt(n))
      if size == auto { t } else { text(size: size, t) }
    }
    let (l, c, r) = f(num(counter(page).get().first()), num(counter(page).final().first()))
    grid(columns: (1fr, auto, 1fr), align(left, l), align(center, c), align(right, r))
  }
}

// `header`/`footer`: none (this style's default: no header, page number centred) or a
// function as for si-page-line. Page numbers are `number-prefix` + 1/i/I.
#let si-doc(title: "", header: none, footer: none, number-prefix: "S", number-style: "1",
            number-size: auto, number-bold: false, number-on-cover: false, body) = {
  set document(title: title)
  let fmt = (..n) => number-prefix + numbering(number-style, n.pos().first())
  let footer = if footer == none { (p, t) => ([], p, []) } else { footer }
  // Text width matches the US-letter styles (6.5 in), so pictures are sized the same.
  set page(paper: "a4", margin: (x: 2.2cm, y: 2.5cm), numbering: fmt,
           header: if header == none { none } else {
             si-page-line(header, fmt, number-size, number-bold, number-on-cover) },
           footer: si-page-line(footer, fmt, number-size, number-bold, number-on-cover))
  set text(font: si-font, size: 10.5pt, lang: "en")
  set par(justify: true, leading: 0.6em, spacing: 1em)
  // Sections are numbered 1, 1.1, 1.1.1 (levels 1-3). General procedures, compound
  // titles and headings typed in a text are never numbered (they pass numbering: none).
  set heading(numbering: "1.1")
  set figure.caption(separator: [ ])
  show figure.caption: set text(size: 9.5pt)
  show heading: set text(font: si-head-font)
  let num(it) = if it.numbering != none { counter(heading).display(it.numbering); h(0.6em) }
  show heading.where(level: 1): it => block(above: 1.6em, below: 0.9em, sticky: true,
    text(size: 12.5pt, weight: "bold", { num(it); it.body }))
  show heading.where(level: 2): it => block(above: 1.2em, below: 0.6em, sticky: true,
    text(size: 11pt, weight: "bold", { num(it); it.body }))
  show heading.where(level: 3): it => block(above: 1.1em, below: 0.5em, sticky: true,
    text(size: 10.5pt, weight: "bold", style: "italic", { num(it); it.body }))
  // Deeper headings (typed inside the text of a sub-sub-section, or of a compound).
  show heading: it => if it.level <= 3 { it } else {
    block(above: 1em, below: 0.5em, sticky: true,
      text(size: 10.5pt, weight: "regular", style: "italic", it.body)) }
  show outline.entry.where(level: 1): set text(weight: "bold")
  body
}

// A compound title ("Ethyl … (3g)."): a bold line kept with the structure. It is a
// heading one level below its section, so the table of contents can list it, but it is
// never numbered and always looks the same.
#let si-compound-title(level, outlined, body) = {
  show heading: it => block(above: 1.4em, below: 0.5em, sticky: true,
    text(font: si-font, size: 10.5pt, weight: "bold", it.body))
  heading(level: level, outlined: outlined, numbering: none, body)
}

// The cover page. `heading`: auto = this style's own ("Electronic Supplementary
// Information"); `extra`: optional text under the affiliations (Front matter ▸ Cover page).
#let si-cover(heading: auto, title: [], authors: [], affiliations: [], contact: [],
              extra: none) = {
  align(left)[
    #text(font: si-head-font, size: 14pt, weight: "bold",
          if heading == auto [Electronic Supplementary Information] else { heading })
    #v(1em)
    #text(font: si-head-font, size: 13pt, weight: "bold", title)
    #v(1em)
    #authors
    #v(0.6em)
    #text(size: 9.5pt, affiliations)
    #v(0.4em)
    #text(size: 9.5pt, contact)
  ]
  if extra != none { v(1.2em); extra }
  v(1.5em)
}

// A structure drawing under a compound title.
#let si-structure(img) = block(above: 0.4em, below: 0.8em, breakable: false, sticky: true,
  align(center, img))

// A label and its caption, e.g. "Figure S1 Caption". The program works out the
// label (Project settings ▸ Labels), so the PDF matches the editor; Typst's own figure
// numbering is off. `caption` is none when there is no caption text.
#let si-caption(label, caption) = if caption == none { strong(label + []) } else {
  strong(label + []) + [ ] + caption }

// A figure: a picture in a custom section or General Information, or a file shown inline
// with a compound (e.g. an HPLC trace). Listed in the table of contents' list of figures.
#let si-figure(img, label, caption) = figure(img, caption: si-caption(label, caption),
  kind: "si-figure", supplement: none, numbering: none)
#let si-inline-figure = si-figure

// A table: label and caption above it, footnotes (or none) below it. `tbl` is a Typst
// table with rules above and below the header and at the bottom. The table is kept whole,
// moving to the next page if it does not fit where it is, unless it is taller than a page
// or `split` is true (table editor: "May run over a page break"); then it continues on
// the next page with its header rows repeated.
#let si-table(tbl, notes, label, caption, split: false) = layout(page => {
  let fig(breakable) = {
    show figure: set block(breakable: breakable)
    figure(layout(room => {
        let body = { set text(size: 9.5pt); tbl }
        // The footnotes start at the table's left edge.
        block(width: measure(body, width: room.width).width, {
          body
          if notes != none { v(0.2em); align(left, text(size: 8.5pt, notes)) }
        })
      }),
      caption: figure.caption(position: top, si-caption(label, caption)),
      kind: table, supplement: none, numbering: none)
  }
  fig(split or measure(fig(false), width: page.width).height > page.height)
})

// A scheme made with the scheme editor (SPEC §13n.4). `items`, left to right:
//   (kind: "mol", body: image(…), label: [3a] or none, dx: 0pt, dy: 0pt, yield: [92%] or none)
//   (kind: "plus")
//   (kind: "arrow", top: ([line], …), bottom: ([line], …), top-align: center, bottom-align: center)
// Each arrow is as long as the wider of its two conditions texts plus `before` and
// `after`, but at least `min-arrow`. Structures sit centred on the arrow's line; an ID is
// centred under its structure (moved by dx, dy where it was dragged), with the yield
// under it. Too wide for the page: `fit: "wrap"` starts a new row at an arrow (the row
// begins with that arrow, so the continuation is clear), "shrink" makes the whole scheme
// smaller to fit one line. A row still too wide is shrunk to fit. `factor` (the scheme's
// Scale) enlarges or shrinks everything.
#let si-reaction-scheme(items, factor: 100%, fit: "wrap", gap: 2mm, before: 2mm,
                        after: 3mm, min-arrow: 12mm, size: 9pt, label-size: 10pt,
                        row-gap: 5mm, label: none, caption: none, align-ids: true,
                        key: "") = {
  let stroke-w = 0.6pt
  let head-len = 5pt
  let head-half = 1.9pt
  let lines-block(lines, a) = {
    if lines.len() == 0 { return none }
    let w = calc.max(..lines.map(l => measure(text(size: size, l)).width))
    // (measured from the ascenders to the descenders, so nothing touches the arrow)
    text(size: size, top-edge: "ascender", bottom-edge: "descender", stack(dir: ttb,
      spacing: 0.1em,
      ..lines.map(l => box(width: w, align(a, l)))))
  }
  let piece(it) = {
    if it.kind == "plus" {
      let p = text(size: size * 1.4, [+])
      let m = measure(p)
      return (w: m.width, up: m.height / 2, down: m.height / 2, new-row: false,
              mol: false,
              draw: (x, axis, low) => place(top + left, dx: x, dy: axis - m.height / 2, p))
    }
    if it.kind == "arrow" {
      let ta = it.at("top-align", default: center)
      let ba = it.at("bottom-align", default: center)
      let t = lines-block(it.top, ta)
      let b = lines-block(it.bottom, ba)
      let tm = if t == none { (width: 0pt, height: 0pt) } else { measure(t) }
      let bm = if b == none { (width: 0pt, height: 0pt) } else { measure(b) }
      let room = calc.max(tm.width, bm.width)
      let len = calc.max(room + before + after, min-arrow)
      // the conditions sit over the part of the arrow between `before` and `after`
      let x0 = before + (len - before - after - room) / 2
      let gap-v = 0.3 * size
      let up = calc.max(head-half, if t == none { 0pt } else { tm.height + gap-v })
      let down = calc.max(head-half, if b == none { 0pt } else { bm.height + gap-v })
      return (w: len, up: up, down: down, new-row: true, mol: false,
              draw: (x, axis, low) => {
        place(top + left, dx: x, dy: axis - stroke-w / 2,
              line(length: len - head-len * 0.6, stroke: stroke-w))
        place(top + left, dx: x + len - head-len, dy: axis - head-half,
              polygon(fill: black, stroke: none, (0pt, 0pt), (head-len, head-half),
                      (0pt, 2 * head-half), (head-len * 0.22, head-half)))
        if t != none {
          place(top + left, dx: x + x0, dy: axis - gap-v - tm.height,
                box(width: room, align(ta, t)))
        }
        if b != none {
          place(top + left, dx: x + x0, dy: axis + gap-v, box(width: room, align(ba, b)))
        }
      })
    }
    // a structure, with its ID and yield under it
    let m = measure(it.body)
    let under = ()
    if it.label != none { under.push(text(size: label-size, weight: "bold", it.label)) }
    if it.yield != none { under.push(text(size: size, it.yield)) }
    let u = if under.len() > 0 {
      stack(dir: ttb, spacing: 0.3em, ..under.map(x => align(center, x))) } else { none }
    let um = if u == none { (width: 0pt, height: 0pt) } else { measure(u) }
    let w = calc.max(m.width, um.width)
    let label-gap = 2pt
    let under-h = if u == none { 0pt } else { label-gap + um.height + calc.max(0pt, it.dy) }
    // `low`: how far under the arrow the IDs start; with align-ids the lowest structure
    // bottom of the row (all IDs of a row on one line), else this structure's own bottom.
    return (w: w, up: m.height / 2, down: m.height / 2 + under-h, new-row: false,
            mol: true, bottom: m.height / 2, under: under-h,
            draw: (x, axis, low) => {
      place(top + left, dx: x + (w - m.width) / 2, dy: axis - m.height / 2, it.body)
      if u != none {
        let start = if low == none { m.height / 2 } else { low }
        place(top + left, dx: x + (w - um.width) / 2 + it.dx,
              dy: axis + start + label-gap + it.dy, box(width: um.width, u))
      }
    })
  }
  let row-block(ps) = {
    let mols = ps.filter(p => p.mol)
    let low = if align-ids and mols.len() > 0 { calc.max(..mols.map(p => p.bottom)) } else {
      none }
    let up = calc.max(..ps.map(p => p.up))
    let down = calc.max(..ps.map(p => if p.mol and low != none { low + p.under } else {
      p.down }))
    let total = ps.map(p => p.w).sum() + gap * (ps.len() - 1)
    (w: total, body: box(width: total, height: up + down, {
      let x = 0pt
      for p in ps {
        (p.draw)(x, up, low)
        x += p.w + gap
      }
    }))
  }
  let body = layout(room => {
    let ps = items.map(piece)
    let k = factor / 100%
    let avail = room.width / k
    let rows = ()
    if fit == "wrap" {
      // A step (its arrow and products) is kept together: rows start at an arrow.
      let chunks = ()
      for p in ps {
        if p.new-row or chunks.len() == 0 { chunks.push((p,)) } else {
          chunks.last().push(p) }
      }
      let row = ()
      let w = 0pt
      for c in chunks {
        let cw = c.map(p => p.w).sum() + gap * (c.len() - 1)
        if row.len() > 0 and w + gap + cw > avail {
          rows.push(row)
          row = ()
          w = 0pt
        }
        if row.len() > 0 { w += gap }
        row += c
        w += cw
      }
      if row.len() > 0 { rows.push(row) }
    } else {
      rows = (ps,)
    }
    let blocks = rows.map(row-block)
    let widest = calc.max(..blocks.map(b => b.w))
    let f = if widest > avail { k * avail / widest } else { k }
    // (the conditions' printed size, for the compile warning when it gets too small)
    [#metadata((key: key, size: size * f / 1pt)) <si-scheme-text>]
    align(center, scale(f * 100%, reflow: true,
      stack(dir: ttb, spacing: row-gap, ..blocks.map(b => align(center, b.body)))))
  })
  // No label (the default for scheme pages, SPEC §13p.1): the scheme alone, not
  // numbered and not in the List of Schemes.
  if label == none { block(width: 100%, above: 1.2em, below: 1.2em, body) } else {
    figure(body, caption: si-caption(label, caption), kind: "scheme", supplement: none,
      numbering: none) }
}

// The References section: only cited entries, numbered in order of first citation.
#let si-citation-style = "royal-society-of-chemistry"
#let si-references(path) = {
  pagebreak(weak: true)
  set text(size: 9.5pt)
  bibliography(path, title: [Notes and references], style: si-citation-style)
}

// The Spectra section (SPEC §13k.12). The program sets out the spectra page by page:
// `n` (1, 2 or 4) slots on a page that fills the rest of the page body; 2 are stacked on a
// portrait page and side by side on a landscape one, 4 make a 2 × 2 grid. A page that is
// not full keeps the same slot size.
#let si-spectra-page(n, landscape, slots) = block(width: 100%, height: 1fr, breakable: false, {
  let (cols, rows) = if n == 4 { (2, 2) } else if n == 2 {
    if landscape { (2, 1) } else { (1, 2) } } else { (1, 1) }
  grid(columns: (1fr,) * cols, rows: (1fr,) * rows, column-gutter: 0.6cm, row-gutter: 0.8cm,
    ..slots)
})

// One spectrum in its slot: a header line (or none), then the spectrum as large as fits,
// keeping its shape, with the compound's structure, ID and caption on a box over it.
//   img(w, h): the picture at that size; aspect: its height / width (none if unknown)
//   crop: (left, top, right, bottom) fractions of the picture to show (auto-crop: only
//     blank white is cut, so the axes stay); the rest is scaled to fill the slot
//   pos: (x, y) fractions of the shown spectrum for the box's centre (dragged), or none
//     for `corner`; box-stroke / box-fill: the box's frame (or none) and background;
//   id-size: auto = the text size; frame: a frame around the spectrum (or none).
#let si-spectrum(header, img, aspect, crop: none, structure: none, id: none, caption: none,
                 pos: none, corner: top + left, box-stroke: none, box-fill: white,
                 id-size: auto, id-bold: true, frame: none,
                 caption-size: 9pt) = layout(size => {
  let head = if header == none { none } else {
    align(center, text(size: 10.5pt, weight: "bold", header)) }
  let gap = 0.3em.to-absolute()
  let head-h = if head == none { 0pt } else { measure(head, width: size.width).height + gap }
  let (l, t, r, b) = if crop == none { (0, 0, 1, 1) } else { crop }
  let avail-h = size.height - head-h
  let (w, h, full-w, full-h) = if aspect == none { (size.width, avail-h, size.width, avail-h) } else {
    let shape = aspect * (b - t) / (r - l)
    let w = calc.min(size.width, avail-h / shape)
    let full-w = w / (r - l)
    (w, w * shape, full-w, full-w * aspect)
  }
  let label = if structure == none and id == none and caption == none { none } else {
    box(fill: box-fill, stroke: box-stroke, inset: 3pt, align(center, {
      if structure != none { structure }
      if id != none {
        if structure != none { linebreak() }
        text(size: if id-size == auto { 10.5pt } else { id-size },
             weight: if id-bold { "bold" } else { "regular" }, id)
      }
      if caption != none {
        if structure != none or id != none { linebreak() }
        text(size: caption-size, caption)
      }
    }))
  }
  let shown = align(center, box(width: w, height: h, stroke: frame, {
    box(width: w, height: h, clip: true,
      place(top + left, dx: -l * full-w, dy: -t * full-h, img(full-w, full-h)))
    if label != none {
      if pos == none {
        let at-right = corner == top + right or corner == bottom + right
        let at-bottom = corner == bottom + left or corner == bottom + right
        place(corner, dx: if at-right { -0.4cm } else { 0.4cm },
          dy: if at-bottom { -0.4cm } else { 0.4cm }, label)
      } else {
        let s = measure(label)
        place(top + left, dx: pos.at(0) * w - s.width / 2, dy: pos.at(1) * h - s.height / 2,
          label)
      }
    }
  }))
  if head == none { shown } else { stack(dir: ttb, spacing: gap, head, shown) }
})
