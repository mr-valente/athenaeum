# Writing content

Put all page text, project listings, and downloads in `content/`. The build derives routes, navigation, breadcrumbs, and section listings from that directory. No template or navigation configuration is needed for a new page.

## Add a page

Create `content/statistics/first-lesson.md`:

```markdown
---
title: First lesson
description: Notes and resources for this lesson.
order: 10
draft: true
---

# First lesson

Write the lesson here.
```

Run `npm run dev`. Set `draft: false` to preview the page at `/statistics/first-lesson/`. Run `npm run verify` before publishing. Development and production both exclude drafts; preview a draft by changing it locally, then restore `draft: true` if it should stay unpublished.

Content changes require rebuilding the production image. Automatic GitHub deployment belongs to Phase E and is not installed yet.

## Paths and navigation

| File | URL |
| --- | --- |
| `content/index.md` | `/` |
| `content/statistics/index.md` | `/statistics/` |
| `content/statistics/first-lesson.md` | `/statistics/first-lesson/` |
| `content/Unit One/Worked_Example.md` | `/unit-one/worked-example/` |

Names are lowercased; spaces and underscores become hyphens. Use ASCII letters, numbers, and hyphens for predictable URLs. Dotfiles are ignored and symlinks are rejected. `foo.md` and `foo/index.md` collide; choose one.

Missing parent indexes are generated from published descendants. An empty directory builds a minimal home page. Each section lists its immediate children; the header lists top-level pages. Listings sort by ascending `order`, then title, then URL. Generated indexes use order `0`; add an `index.md` to customize one.

`/quacktuaries/` is reserved for the application. Register future application slugs in `deploy/reserved-paths.json`; this reserves their entire first URL segment. System routes such as `/404/`, `/_health`, `/_content/`, and `/_astro/` cannot be content routes.

## Frontmatter

All fields are optional except `href` on a project. Unknown fields, duplicate YAML keys, wrong types, and invalid dates fail the build with the source filename.

| Field | Value and behavior |
| --- | --- |
| `title` | Nonempty text; otherwise the first heading, then the filename or section name |
| `description` | Plain text for metadata and section listings |
| `order` | Number, default `0`; lower appears first |
| `draft` | Boolean, default `false` |
| `date` | Real `YYYY-MM-DD` date, displayed on the page |
| `tags` | List of text labels, displayed on the page |
| `kind` | `page` (default) or `project` |
| `href` | Required project destination; not valid on ordinary pages |
| `status` | Optional short project status; not valid on ordinary pages |

Use one `#` heading per page. If there is no level-one heading, the template inserts the page title. Explicit `title` controls navigation and browser metadata without replacing a heading in your Markdown.

## Projects

Create `content/projects/my-app.md`:

```markdown
---
title: My app
description: A short explanation of its purpose.
kind: project
href: https://example.com/
status: Available
order: 20
---

# My app

Explain what visitors can do here.
```

The project page appears at `/projects/my-app/` with an “Open My app” link. After an app is integrated and its slug registered, its destination can be `/my-app/`. The Quacktuaries listing now targets `/quacktuaries/` in the local ecosystem. Its existing Cloud Run subdomain remains unchanged until the later live cutover.

## Links and downloads

Prefer source-relative Markdown links:

```markdown
[Back to statistics](index.md)
[Worked example](first-lesson.md#worked-example)
![Distribution of sample values](assets/distribution.png)
[Worksheet](assets/worksheet.pdf)
[Data](assets/values.csv)
```

Place these files beside the page, under its `assets/` directory, or elsewhere inside `content/`. Local page links, fragments, and files are checked during the build. Root-relative published URLs also work. Application links must be root-relative or absolute; registered app deep links are passed through without checking application endpoints. Absolute HTTP(S) links are external and are not checked for availability.

Supported local assets: PNG, JPG/JPEG, GIF, WebP, AVIF, PDF, CSV, and TXT. Only assets referenced by published Markdown links, images, or project destinations are copied to `/_content/…`. Keep writing source paths; the build rewrites URLs. Adding an unreferenced file does not publish it. Raw HTML, inline SVG, scripts, MDX, arbitrary download extensions, path traversal, and symlinks are not supported. Use descriptive image alt text.

External HTTP(S) images are allowed, but introduce an external request; use local files when you want self-contained pages. Ordinary external links, `mailto:`, and `tel:` links are supported.

## Drafts

`draft: true` removes a page from HTML, navigation, section listings, and the sitemap. A draft `index.md` hides its entire section. Assets referenced only by hidden pages are omitted. Linking a published page to a draft fails the build. Sharing an asset with a published page makes that asset public.

Metadata and route validation still applies to drafts. Keep unfinished content structurally valid. There is no search index or hidden preview endpoint.

**Drafts are not private in a public Git repository.** Do not commit student records, private answer keys, or credentials.

## Math, tables, and print

Use `$x^2$` for inline math and a separate display block:

```markdown
$$
z = \frac{x - \mu}{\sigma}
$$
```

KaTeX renders at build time into HTML and MathML. Fonts are bundled locally; no runtime math script or CDN is required. Unsupported expressions fail the build. Tables, fenced code blocks, and footnotes use ordinary GitHub-flavored Markdown. See the unpublished `content/statistics/example.md` for a complete example.

Use the browser's Print / Save as PDF command. Print styles hide site navigation, preserve readable math and tables, and wrap code. Check unusually wide equations and tables in print preview. The shared visual style remains provisional until `style.md` is filled in.
