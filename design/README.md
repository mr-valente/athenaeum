# Provisional shared design contract

Version: **0.1.0**. Status: **provisional**. The authority for selected visual decisions remains [style.md](../style.md).

Import `tokens.css`, then `base.css`. All shared variables use the `--ath-` prefix; reusable layout classes use `ath-`. The initial shell provides readable text, a bounded content width, keyboard focus, a skip link, tables, code, responsive local images, and print rules. It has no animations, final palette, or branding. The site bundles KaTeX fonts locally for math; body text uses system fonts.

The browser's system colors and fonts are temporary defaults. Their use is not a light/dark theme decision. The tokens and classes are the initial cross-framework interface; Quacktuaries has not adopted them yet. Phase B adds math-compatible reading and print styles. Final component and chart styling belongs to the later shared-presentation phase.

For the current site, import these files directly at build time. When cross-repository integration is implemented, distribute a checksummed versioned archive and pin the selected version in each consumer. Do not fetch mutable styles at runtime. Record deliberate contract changes here and update consumers explicitly; the complete packaging/release workflow remains future work.
