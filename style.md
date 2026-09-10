# Athenaeum shared style

Version: **0.2.0**. Theme: **After hours**.

A small library after dark: ink and warm ivory, mint links, precise monospace typography, and the occasional quiet wink. Terminal influence comes from type, paths, and a cursor detail. Reading is the main event.

## Visual decisions

| Area | Decision |
| --- | --- |
| Theme | Dark only; no toggle, storage, or theme script. Paper prints white. |
| Palette | Canvas `#101516`, surface `#171e20`, text `#e7e9e1`, muted text `#a1ada8`, mint `#a6dfb5`, amber focus `#e7c589`, borders `#303c3b`. |
| Typography | Locally bundled CaskaydiaCove Nerd Font, regular and bold WOFF2 text subsets; system monospace fallback. KaTeX keeps its own local math fonts. |
| Reading | 16px body, 1.8 line height, 72ch reading width. Shell capped at 64rem. Fluid headings, smaller navigation and metadata. |
| Layout | Generous open space, a thin header rule, numbered collection rows, compact breadcrumbs, and a quiet footer. Single column on phones; breakpoint at 640px. |
| Surfaces | Flat dark surfaces, 1px borders, 3–4px corner radii for code and controls. No shadows or glass effects. |
| Branding | A tesseract-inspired wireframe: nested cubes, eight connecting edges, and an ivory center rotated 45 degrees counterclockwise inside mint geometry. No dash. One hand-authored SVG serves the header, home bookplate, and favicon. |
| Links | Mint inline links with underlines. Collection rows are single full-row links. Project launch links have a mint outline. |
| Accessibility | Semantic navigation, visible amber keyboard focus, skip link, wrapping navigation, and local scrolling for wide code, tables, and math. |
| Motion | 160ms row hover and a 4px arrow movement. Reduced motion removes both. No looping or entrance animation. |
| Markdown | Standard headings, prose, lists, quotes, code, tables, footnotes, images, and math receive styles automatically. No new frontmatter fields. |
| Print | Black text, white paper, no decorative shell. Tables expand and code wraps; math keeps KaTeX rendering. |
| Empty/error states | Plain text for empty collections; a small “404 / Off the shelf” label on the error page. |
| Other apps | Controls, charts, loading indicators, and app-specific components remain consumer work. This change styles Athenaeum only. |

## Editing and delivery

Page text and metadata stay in `content/`. Directories and existing frontmatter continue to determine navigation and collection listings. The home introduction, including its small work-in-progress note, is ordinary Markdown.

Shared shell copy lives in `src/layouts/Page.astro`; collection labels and the home eyebrow live in `src/pages/[...page].astro`. Edit `design/tokens.css` for palette/type/spacing, `design/base.css` for presentation, and `design/mark.svg` for the mark. No browser JavaScript or external font service is required.

Import tokens before base styles. Astro bundles fonts and the SVG from `design/`; the Docker build includes these assets. Font source, license, and reproducible subset instructions are in [design/fonts/README.md](design/fonts/README.md).

The version is a shared design contract, independent of the application version. Other applications should bundle a deliberate, pinned copy when adopting it. Changing these files does not restyle or deploy Quacktuaries.
