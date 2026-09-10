# Shared design contract

Version: **0.2.0**. Theme: **After hours**. See [style.md](../style.md) for the visual specification.

Import `tokens.css`, then `base.css`. Variables use the `--ath-` prefix; reusable classes use `ath-`. The static shell includes dark styling, responsive navigation and collection rows, keyboard focus, a skip link, Markdown reading styles, math, and print rules.

CaskaydiaCove is bundled as two WOFF2 text subsets, about 107 KiB combined, with `font-display: swap`. See [font provenance and reproduction](fonts/README.md). KaTeX retains its existing local fonts. There is no runtime font request to another origin and no client JavaScript.

`mark.svg` is a tesseract-inspired wireframe: two nested cubes joined at their eight corresponding corners, with the inner cube rotated 45 degrees counterclockwise relative to the outer cube. Mint outer edges and an ivory inner cube give it depth; there is no cursor dash. Astro imports the asset directly so the same source is used in every placement. The header's wordmark comes from the home Markdown title.

For another framework or repository, bundle a pinned copy of the styles and their relative font assets; do not fetch mutable styles at runtime. Consumer adoption and deployment remain explicit.
