# Web Enhancements Quick Plan

Entry: `/gsd-discuss`

Scope:
- Refine candidate web enhancements into approval-ready groups.
- Exclude a separate primary `Continue reading` button; keep the existing cover overlay shortcut.
- Exclude reader image display modes such as `Fit width`, `Original size`, or `Full bleed`.

## Enhancement Groups

### Story Info Experience
- Build out the story information page as the default destination from library cards.
- Show cover, title, author, description, tags, status, archive health, and available exports.
- Keep chapter list and archive actions on the story page so users can inspect before reading.
- Add a clear `Back to story info` link from chapter pages.

### Library Management
- Add sorting controls for title, author, last archived time, completion status, and warning count.
- Show last-read chapter and progress percentage on each library card.
- Add local tags or collections independent of Wattpad reading lists.
- Add bulk export for selected stories.
- Detect duplicate story archives caused by renamed or changed slug directories.

### Archive Reliability
- Validate Wattpad cookie health before long-running archive jobs.
- Add pre-archive checks that flag missing cookie, stale cookie, and unreachable API states early.
- Expand repair summaries with missing chapters, missing comments, missing cover, and stale metadata.
- Record retry history per story so repeated failures are easier to diagnose.
- Add an archive dry run that estimates story size before fetching chapters and comments.

### Reader Improvements
- Add image click-to-zoom/lightbox while keeping default images constrained to reader width.
- Add keyboard shortcuts for next chapter, previous chapter, table of contents, and theme toggle.
- Add a sticky chapter title or reading progress indicator while scrolling.
- Add a setting to hide comments by default for cleaner reading.

### History And Diagnostics
- Expose historical archive runs from `_state.sqlite` with result counts and failure reasons.
- Link failed runs to repair actions where enough story context is available.
- Surface warning trends so recurring scraper or cookie issues are visible earlier.

## Approval Order

1. Story Info Experience
2. Archive Reliability
3. Library Management
4. Reader Improvements
5. History And Diagnostics