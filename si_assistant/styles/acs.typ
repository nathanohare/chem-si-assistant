// Chem SI Assistant — ACS style (Org. Lett., J. Org. Chem., JACS).
// A journal style is a plain Typst template: page, fonts, headings and the layout of
// compound blocks, schemes and spectra. Copy this file to make a new style.

#let si-font = ("Times New Roman", "Liberation Serif", "Libertinus Serif")

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
  set page(paper: "us-letter", margin: 1in, numbering: fmt,
           header: if header == none { none } else {
             si-page-line(header, fmt, number-size, number-bold, number-on-cover) },
           footer: si-page-line(footer, fmt, number-size, number-bold, number-on-cover))
  set text(font: si-font, size: 11pt, lang: "en")
  set par(justify: true, leading: 0.6em, spacing: 1em)
  set figure.caption(separator: [. ])
  show figure.caption: set text(size: 10pt)
  show heading: set text(font: si-font)
  show heading.where(level: 1): it => block(above: 1.6em, below: 0.9em, sticky: true,
    text(size: 13pt, weight: "bold", it.body))
  show heading.where(level: 2): it => block(above: 1.2em, below: 0.6em, sticky: true,
    text(size: 11.5pt, weight: "bold", it.body))
  show heading.where(level: 3): it => block(above: 1.1em, below: 0.5em, sticky: true,
    text(size: 11pt, weight: "bold", style: "italic", it.body))
  // Deeper headings (typed inside the text of a sub-sub-section, or of a compound).
  show heading: it => if it.level <= 3 { it } else {
    block(above: 1em, below: 0.5em, sticky: true,
      text(size: 11pt, weight: "regular", style: "italic", it.body)) }
  show outline.entry.where(level: 1): set text(weight: "bold")
  body
}

// Sections are headings of level 1, 2 and 3 (1, 1.1, 1.1.1); general procedures and
// headings typed in a text are one level below their section and never numbered.

// A compound title ("Ethyl … (3g)."): a bold line kept with the structure. It is a
// heading one level below its section, so the table of contents can list it, but it is
// never numbered and always looks the same.
#let si-compound-title(level, outlined, body) = {
  show heading: it => block(above: 1.4em, below: 0.5em, sticky: true,
    text(size: 11pt, weight: "bold", it.body))
  heading(level: level, outlined: outlined, numbering: none, body)
}

// The cover page. `heading`: auto = this style's own ("Supporting Information");
// `extra`: optional text under the affiliations (Front matter ▸ Cover page).
#let si-cover(heading: auto, title: [], authors: [], affiliations: [], contact: [],
              extra: none) = {
  align(center)[
    #text(size: 16pt, weight: "bold", if heading == auto [Supporting Information] else { heading })
    #v(1em)
    #text(size: 14pt, weight: "bold", title)
    #v(1em)
    #authors
    #v(0.6em)
    #text(size: 10pt, affiliations)
    #v(0.4em)
    #text(size: 10pt, contact)
  ]
  if extra != none { v(1.2em); extra }
  v(1.5em)
}

// A structure drawing under a compound title.
#let si-structure(img) = block(above: 0.4em, below: 0.8em, breakable: false, sticky: true,
  align(center, img))

// A label and its caption, e.g. "Figure S1. Caption". The program works out the
// label (Project settings ▸ Labels), so the PDF matches the editor; Typst's own figure
// numbering is off. `caption` is none when there is no caption text.
#let si-caption(label, caption) = if caption == none { strong(label + [.]) } else {
  strong(label + [.]) + [ ] + caption }

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
        let body = { set text(size: 10pt); tbl }
        // The footnotes start at the table's left edge.
        block(width: measure(body, width: room.width).width, {
          body
          if notes != none { v(0.2em); align(left, text(size: 9pt, notes)) }
        })
      }),
      caption: figure.caption(position: top, si-caption(label, caption)),
      kind: table, supplement: none, numbering: none)
  }
  fig(split or measure(fig(false), width: page.width).height > page.height)
})

// A scheme picture with compound IDs printed on it. `labels` = ((x, y, [3a]), …) with x, y
// as fractions of the picture; each label is centred on that point.
#let si-scheme(img, w, h, labels, label, caption) = figure(
  box(width: w, height: h, {
    img
    for l in labels {
      place(top + left, dx: l.at(0) * w - 2cm, dy: l.at(1) * h - 0.6em,
        box(width: 4cm, align(center, text(size: 10pt, weight: "bold", l.at(2)))))
    }
  }),
  caption: si-caption(label, caption), kind: "scheme", supplement: none, numbering: none)

// The References section: only cited entries, numbered in order of first citation, in
// the journal's citation style (Typst's built-in CSL styles).
#let si-citation-style = "american-chemical-society"
#let si-references(path) = {
  pagebreak(weak: true)
  set text(size: 10pt)
  bibliography(path, title: [References], style: si-citation-style)
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
                 id-size: auto, id-bold: true, frame: none) = layout(size => {
  let head = if header == none { none } else {
    align(center, text(size: 11pt, weight: "bold", header)) }
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
        text(size: if id-size == auto { 11pt } else { id-size },
             weight: if id-bold { "bold" } else { "regular" }, id)
      }
      if caption != none {
        if structure != none or id != none { linebreak() }
        text(size: 9pt, caption)
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
