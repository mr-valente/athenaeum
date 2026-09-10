# Writing content

Put all page text, project listings, and downloads in `content/`. The build derives routes, navigation, breadcrumbs, and section listings from that directory. No template or navigation configuration is needed for a new page.

## Start here: arrange your site with files and folders

Think of each Markdown file as a page and each folder as a section. A file named `index.md` is the introduction to its folder. Write your introduction there; the site adds a list of the section's pages underneath it automatically.

Your current layout is:

```text
content/
├── index.md                 Home: Valente Math
├── statistics/
│   ├── index.md             AP Statistics introduction
│   ├── bernoulli.md         Bernoulli app landing page
│   └── quacktuaries.md      Quacktuaries app landing page
└── projects/
    ├── index.md             Projects introduction
    ├── example.md           Draft Markdown example
    └── assets/
        └── example-data.csv
```

The home page lists **AP Statistics** and **Projects**. Those same sections appear in the header. AP Statistics lists **Bernoulli** and **Quacktuaries**. The draft example is hidden. A folder of downloads does not become a section unless it contains published Markdown pages.

| I want to… | Edit or create… |
| --- | --- |
| Change the home heading, introduction, or work-in-progress note | `content/index.md` |
| Change a section's name or introduction | That section's `index.md` |
| Add a lesson to AP Statistics | `content/statistics/my-lesson.md` |
| Group lessons into a unit | `content/statistics/unit-one/index.md` and lesson files beside it |
| Add a new top-level section | For example, `content/notes/index.md`; it appears on the home page and in the header |
| Change a card's description | The destination page's `description` field |
| Reorder pages or sections | Their `order` fields; smaller numbers come first |
| Change an app's launch or source link | Its `href` or `repository` field |

You never need to maintain a separate menu. Breadcrumbs follow the folders too: a lesson inside `statistics/unit-one/` gets a trail through Home → AP Statistics → Unit one.

## Your first edit

Open `content/index.md`. The short block between `---` lines is **frontmatter**: settings for the page. Everything below it is the visible Markdown content.

```markdown
---
title: Valente Math
description: Notes, projects, and classroom resources.
---

# Valente Math

Welcome! Explore classroom activities and notes below.

> A work in progress. Curiosity welcome.
```

`title` labels the page in navigation and the browser tab. `# Valente Math` is the large heading visitors read. `description` supplies the summary when another page lists this one; it does not replace your opening paragraph. Usually, keep the title and heading the same.

With the [local Node setup](site-development.md#pinned-toolchain) ready, run `npm run dev` in the repository and open the address it prints. Save your Markdown to see the update. Leave the server running while you write.

## Build a section, then put pages inside it

For a unit of lessons, create `content/statistics/unit-one/index.md`:

```markdown
---
title: Exploring data
description: Our first unit on describing and comparing data.
order: 10
---

# Exploring data

Start with the first lesson, then try the practice problems.
```

Then add `first-lesson.md` and `practice.md` in that same folder. Give them `order: 10` and `order: 20`. The unit page lists them in that order, below your introduction. AP Statistics gains a link to the unit. Only top-level sections appear in the global header.

To reorder AP Statistics and Projects on the home page, edit `order` in their respective `index.md` files. To reorder lessons inside a unit, edit the lesson files instead. Numbers such as 10, 20, and 30 leave room to insert a page at 15 later. Without `order`, pages use 0 and then sort by title.

Moving or renaming a file changes its URL. Update links pointing to it; the build catches broken local links but does not create redirects for old bookmarks. To change just the displayed name, edit `title` and the heading while leaving the filename alone.

## Common Markdown building blocks

Use a blank line between paragraphs. One `#` heading names the page; `##` and `###` divide its content into sections.

```markdown
## Before you begin

Read the **important point**, then try the *optional challenge*.

- Bring your calculator.
- Work with a partner.

### What to do

1. Collect a sample.
2. Make a plot.
3. Explain what you notice.

> A short reminder or question to think about.

[Back to this section](index.md)

| Outcome | Count |
| --- | ---: |
| Heads | 12 |
| Tails | 8 |
```

The theme takes care of spacing, type, tables, and mobile layout. There are no special layout tags or components to learn. Copy `content/projects/example.md` when you want a starting point with math, a table, a download, code, and a footnote.

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

Content changes require rebuilding and publishing the Athenaeum image, then deploying it manually. See [image builds](image-builds.md) and [daily usage](../guides/3-daily-usage.md).

## Paths and navigation

| File | URL |
| --- | --- |
| `content/index.md` | `/` |
| `content/statistics/index.md` | `/statistics/` |
| `content/statistics/first-lesson.md` | `/statistics/first-lesson/` |
| `content/Unit One/Worked_Example.md` | `/unit-one/worked-example/` |

Names are lowercased; spaces and underscores become hyphens. Use ASCII letters, numbers, and hyphens for predictable URLs. Dotfiles are ignored and symlinks are rejected. `foo.md` and `foo/index.md` collide; choose one.

Missing parent indexes are generated from published descendants. An empty directory builds a minimal home page. Each section lists its immediate children; the header lists top-level pages. Listings sort by ascending `order`, then title, then URL. Generated indexes use order `0`; add an `index.md` to customize one.

`/quacktuaries/` and `/bernoulli/` are reserved for the applications. Register future application slugs in `deploy/reserved-paths.json`; this reserves their entire first URL segment. System routes such as `/404/`, `/_health`, `/_content/`, and `/_astro/` cannot be content routes.

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
| `repository` | Optional full `https://github.com/…` repository URL, shown below the launch button; project-only |

Use one `#` heading per page. If there is no level-one heading, the template inserts the page title. Explicit `title` controls navigation and browser metadata without replacing a heading in your Markdown.

## Projects

Create `content/projects/my-app.md`:

```markdown
---
title: My app
description: A short explanation of its purpose.
kind: project
href: https://example.com/
repository: https://github.com/mr-valente/my-app
status: Available
order: 20
---

# My app

Explain what visitors can do here.
```

The project page appears at `/projects/my-app/` with an “Open My app” link and the GitHub link underneath. Replace both example URLs with the real destinations. Omit `repository` to hide the GitHub link; the filename and title do not determine the repository URL.

Project pages can live in any section: `content/statistics/bernoulli.md` produces the landing page `/statistics/bernoulli/`, while its launch button opens the separate app at `/bernoulli/`. The Quacktuaries landing page follows the same arrangement. A Markdown landing page describes an app; it does not install or deploy that app. After an app is integrated and its slug registered, its destination can be `/my-app/`.

## Links and downloads

Prefer source-relative Markdown links. For a page inside `content/statistics/unit-one/`, these paths point up to the Statistics section and its assets (create the example files before using the links):

```markdown
[Back to statistics](../index.md)
[Worked example](../first-lesson.md#worked-example)
![Distribution of sample values](../assets/distribution.png)
[Worksheet](../assets/worksheet.pdf)
[Data](../assets/values.csv)
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

KaTeX renders at build time into HTML and MathML. Fonts are bundled locally; no runtime math script or CDN is required. Unsupported expressions fail the build. Tables, fenced code blocks, and footnotes use ordinary GitHub-flavored Markdown. See the unpublished `content/projects/example.md` for a complete example.

Use the browser's Print / Save as PDF command. Print styles use white paper and black text, hide site navigation, preserve readable math and tables, and wrap code. Check unusually wide equations and tables in print preview. The site itself always uses the dark theme defined in `style.md`.

## Before you publish

1. Preview the page and click its links. If it is missing, check for `draft: true` on the page or any parent `index.md`.
2. Run `npm run verify`. Read any error's filename first: it points to the content to fix.
3. Commit your intended content changes. Build and publish the Athenaeum image, then deploy it using [daily usage](../guides/3-daily-usage.md). Saving Markdown or pushing Git alone does not update the live site.

| What happened? | What to check |
| --- | --- |
| A page is missing | Draft status, including parent sections; then whether the current content has been built and deployed |
| A page is in the wrong place | Its folder determines its section; `order` only changes position among siblings |
| “Unrecognized” metadata | Check the frontmatter field names against the table above |
| A YAML error | Use spaces, not tabs; quote text containing `: `, such as `title: "Lesson 1: Sampling"` |
| A broken link or missing asset | Check the path relative to the Markdown file containing the link |
| A duplicate route | Do not create both `lesson.md` and `lesson/index.md` |

The header's GitHub account and footer slogans are shared site copy in `src/layouts/Page.astro`, not page content. Ordinary writing, section organization, app descriptions, and app repository links all stay in Markdown.
