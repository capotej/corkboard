# DokuWiki syntax — everyday subset

A focused cheatsheet for authoring Corkboard/DokuWiki pages. Full reference:
<https://www.dokuwiki.org/wiki:syntax>.

## Namespaces & page ids

- Pages live in namespaces separated by `:` — `ns:subns:page`. The root is empty.
- Link absolutely with a leading colon: `[[:ns:page]]`. Subnamespaces resolve
  relative to the current page's namespace.
- Media ids are the same shape: `ns:image.png`.

## Headings

3–6 `=` signs, **more signs = higher level**. Always close with the same count.

```
====== Level 1 (H1) ======
===== Level 2 =====
==== Level 3 ====
=== Level 4 ===
```

> ⚠️ **On this build, `[[links]]` render raw inside heading text** — keep headings
> plain; put links in the body.

## Text formatting

```
**bold**       //italic//     ''monospace''
__also bold__  <sup>sup</sup> <sub>sub</sub>  <del>strike</del>
forced\\linebreak
```

- Escape markup with `<nowiki>...</nowiki>` or `%%...%%`.
- Plain URLs and `camelCase` words auto-link unless wrapped in `%%...%%`.

## Code & file blocks

Inline: `''code''` (monospace). Block — two leading spaces, or tagged blocks:

```
  indented by 2 spaces -> a <code> block

<code python>
def f(): return 42
</code>

<file text example.txt>
raw output / a data dump
</file>
```

`<file>` renders like `<code>` but styled as a downloadable file. The optional
language after `<code`/`<file>` enables syntax highlighting (must be an installed
GeSHi language; omit it for plain). If highlighting errors on a language, drop it.

## Tables

`^` = header cell, `|` = data cell — cells on a row separated by `|`, one row per
line. A cell of just `:::` merges with the cell to its **left** (colspan); an
empty cell below a header merges **up** (rowspan).

```
^ Heading A   ^ Heading B   ^
| cell 1      | cell 2      |
| spans both  | :::          |
```

(Here "spans both" absorbs the `:::` cell and spans columns A and B.) Start/end
each row with the same `^` or `|`; a leading/trailing space keeps content off
the borders.

## Links

```
[[ns:page]]              internal page (uses id as label)
[[ns:page|Label text]]   internal with label
[[ns:page#section]]      to a heading anchor
[[:ns:page]]             absolute namespace
[[https://example.org]]  external (auto-titled) — bare URLs auto-link too
[[mailto:me@x.org]]      email
[[wp>GPT]]               interwiki (Wikipedia); also [[google>query]], [[doi>...]]
```

## Images / media

```
{{ns:image.png}}                   inline, natural size
{{ns:image.png?400}}               width 400px
{{ns:image.png?400x300}}           width x height
{{ns:image.png?nolink}}            don't link to the file page
{{ns:image.png?400 |caption}}      caption (pipe optional when no other opts)
{{https://example.org/x.png}}      remote image
[[ns:page|{{ns:image.png}}]]       image that links somewhere
```

## Lists

Two leading spaces per nesting level. `*` unordered, `-` ordered.

```
  * item
    * nested
  * another
  - first
  - second
```

## Misc

- Horizontal rule: `----` (4+ dashes) on its own line.
- Footnote: `((footnote text))`.
- Page break: keep paragraphs separated by a blank line; a single newline is a
  soft wrap within a paragraph.
- Comments / hidden text: HTML comments `<!-- ... -->` are stripped; there's no
  first-class comment syntax otherwise.
