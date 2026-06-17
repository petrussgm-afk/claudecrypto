"""
docs/build_html.py — convert FSC_paper.md to FSC_paper.html.

Math blocks ($$...$$) are extracted before markdown conversion so that
markdown's underscore/asterisk parser cannot corrupt LaTeX syntax.
MathJax 3 (CDN) renders equations in the browser.

Usage:
    py docs/build_html.py
    python docs/build_html.py
"""

import re
import sys
import os

try:
    import markdown
except ImportError:
    print("Installing markdown…")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "markdown"])
    import markdown

# ── paths ─────────────────────────────────────────────────────────────────────
ROOT   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC    = os.path.join(ROOT, "docs", "FSC_paper.md")
DEST   = os.path.join(ROOT, "docs", "FSC_paper.html")

# ── protect math before markdown conversion ───────────────────────────────────

def _protect_math(text: str):
    """
    Extract $$...$$ and $...$ blocks into placeholder dicts so that markdown
    cannot mangle LaTeX underscore/asterisk syntax.
    Returns (protected_text, display_map, inline_map).
    """
    display_map: dict[str, str] = {}
    inline_map:  dict[str, str] = {}

    def _sub_display(m):
        key = f"ZZDISPLAY{len(display_map)}ZZ"
        display_map[key] = m.group(0)
        return f"\n\n{key}\n\n"   # keep as own paragraph so markdown wraps in <p>

    def _sub_inline(m):
        key = f"ZZINLINE{len(inline_map)}ZZ"
        inline_map[key] = m.group(0)
        return key

    text = re.sub(r'\$\$(.+?)\$\$', _sub_display, text, flags=re.DOTALL)
    text = re.sub(r'\$([^$\n]+?)\$',  _sub_inline,  text)
    return text, display_map, inline_map


def _restore_math(html: str, display_map: dict, inline_map: dict) -> str:
    for key, val in display_map.items():
        html = html.replace(key, val)
    for key, val in inline_map.items():
        html = html.replace(key, val)
    return html


# ── CSS ───────────────────────────────────────────────────────────────────────

CSS = """
/* ── reset ──────────────────────────────────────────────────────────────── */
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

/* ── dark theme variables ────────────────────────────────────────────────── */
:root {
    --bg:       #0d0d12;
    --surface:  #13131a;
    --border:   #23233a;
    --text:     #cdd6f4;
    --muted:    #7f849c;
    --accent:   #89b4fa;
    --heading:  #cba6f7;
    --code-bg:  #1e1e2e;
    --code-fg:  #a6e3a1;
    --link:     #74c7ec;
    --th-bg:    #1a1a28;
    --td-alt:   #111118;
}

/* ── page ────────────────────────────────────────────────────────────────── */
body {
    background:  var(--bg);
    color:       var(--text);
    font-family: 'Georgia', 'Times New Roman', serif;
    font-size:   15.5px;
    line-height: 1.8;
    max-width:   880px;
    margin:      0 auto;
    padding:     2.5rem 2.2rem 5rem;
}

/* ── headings ────────────────────────────────────────────────────────────── */
h1 {
    font-size:     1.75rem;
    color:         var(--heading);
    margin-bottom: 0.3em;
    font-weight:   bold;
    font-family:   'Georgia', serif;
    border-bottom: 2px solid var(--border);
    padding-bottom: 0.4em;
}
h2 {
    font-size:    1.2rem;
    color:        var(--accent);
    margin-top:   2.2em;
    margin-bottom: 0.5em;
    border-bottom: 1px solid var(--border);
    padding-bottom: 0.25em;
}
h3 {
    font-size:    1.05rem;
    color:        #b4befe;
    margin-top:   1.6em;
    margin-bottom: 0.35em;
}

/* ── paragraph & em ─────────────────────────────────────────────────────── */
p  { margin: 0.75em 0; }
em { color: #f5c2e7; font-style: italic; }
strong { color: #f9e2af; }

/* ── links ───────────────────────────────────────────────────────────────── */
a { color: var(--link); text-decoration: none; }
a:hover { text-decoration: underline; }

/* ── horizontal rule ─────────────────────────────────────────────────────── */
hr {
    border: none;
    border-top: 1px solid var(--border);
    margin: 2.5em 0;
}

/* ── code (inline) ───────────────────────────────────────────────────────── */
code {
    font-family:      'JetBrains Mono', 'Fira Code', 'Cascadia Code', 'Consolas', monospace;
    font-size:        0.87em;
    background:       var(--code-bg);
    color:            var(--code-fg);
    border-radius:    4px;
    padding:          0.1em 0.35em;
    border:           1px solid var(--border);
}

/* ── code blocks ─────────────────────────────────────────────────────────── */
pre {
    background:    var(--code-bg);
    border:        1px solid var(--border);
    border-radius: 6px;
    padding:       1.1em 1.3em;
    overflow-x:    auto;
    margin:        1.2em 0;
    line-height:   1.55;
}
pre code {
    background:  transparent;
    border:      none;
    padding:     0;
    font-size:   0.85em;
    color:       #cdd6f4;
}

/* ── tables ──────────────────────────────────────────────────────────────── */
table {
    width:           100%;
    border-collapse: collapse;
    margin:          1.4em 0;
    font-size:       0.91em;
    font-family:     'JetBrains Mono', 'Consolas', monospace;
}
thead th {
    background:    var(--th-bg);
    color:         var(--accent);
    padding:       0.55em 0.8em;
    text-align:    left;
    border-bottom: 2px solid var(--border);
    font-weight:   bold;
}
tbody td {
    padding:       0.45em 0.8em;
    border-bottom: 1px solid var(--border);
    vertical-align: top;
}
tbody tr:nth-child(even) td { background: var(--td-alt); }

/* ── blockquote ──────────────────────────────────────────────────────────── */
blockquote {
    border-left: 3px solid var(--border);
    margin:      1em 0;
    padding:     0.5em 1.2em;
    color:       var(--muted);
    font-style:  italic;
}

/* ── lists ───────────────────────────────────────────────────────────────── */
ul, ol { margin: 0.6em 0 0.6em 1.6em; }
li     { margin: 0.25em 0; }

/* ── display math spacing ────────────────────────────────────────────────── */
p:has(mjx-container[display="true"]),
p > mjx-container[display="true"] {
    margin: 1em 0;
    overflow-x: auto;
}

/* ── abstract block ──────────────────────────────────────────────────────── */
h2:first-of-type + p,
p:has(strong:first-child) {
    /* intentionally unstyled — let paragraph CSS handle it */
}

/* ── print ───────────────────────────────────────────────────────────────── */
@media print {
    :root {
        --bg:      #ffffff;
        --surface: #f8f8f8;
        --border:  #cccccc;
        --text:    #111111;
        --muted:   #555555;
        --accent:  #0055aa;
        --heading: #220055;
        --code-bg: #f0f0f0;
        --code-fg: #006600;
        --link:    #0055aa;
        --th-bg:   #e8e8e8;
        --td-alt:  #f8f8f8;
    }
    body  { font-size: 11pt; padding: 0; max-width: 100%; }
    pre   { page-break-inside: avoid; }
    table { page-break-inside: avoid; }
    h2    { page-break-before: auto; }
}
"""

# ── MathJax config ────────────────────────────────────────────────────────────

MATHJAX = """
<script>
MathJax = {
  tex: {
    inlineMath:  [['$', '$']],
    displayMath: [['$$', '$$']],
    processEscapes: true
  },
  svg: { fontCache: 'global' },
  options: { skipHtmlTags: ['script','noscript','style','textarea','pre','code'] }
};
</script>
<script src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-svg.js"></script>
"""

# ── HTML template ─────────────────────────────────────────────────────────────

HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>FSC: A Multi-Domain Cryptographic Construction</title>
{mathjax}
<style>
{css}
</style>
</head>
<body>
{body}
</body>
</html>
"""

# ── build ─────────────────────────────────────────────────────────────────────

def build(src: str = SRC, dest: str = DEST) -> str:
    with open(src, encoding="utf-8") as f:
        raw = f.read()

    protected, display_map, inline_map = _protect_math(raw)

    md   = markdown.Markdown(extensions=["tables", "fenced_code"])
    body = md.convert(protected)
    body = _restore_math(body, display_map, inline_map)

    html = HTML_TEMPLATE.format(
        mathjax=MATHJAX,
        css=CSS,
        body=body,
    )

    with open(dest, "w", encoding="utf-8") as f:
        f.write(html)

    return dest


if __name__ == "__main__":
    out = build()
    size_kb = os.path.getsize(out) / 1024
    print(f"Saved: {out}  ({size_kb:.1f} kB)")
