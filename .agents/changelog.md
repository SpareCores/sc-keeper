# Changelog rules

Rules for writing the root `CHANGELOG.md`, published as the
[API changelog](https://docs.sparecores.com/api/news). Readers are developers using the
Spare Cores Navigator API; they want to know what changed in the endpoints, parameters,
response fields and data they depend on.

## What to include

- Add each user-facing change in the same PR, under the section of the month the PR is
  merged (`## November 2026`); create the section if it is missing. If a PR is still
  open when a new month starts, move its entry to the new month before merging.
- User-facing changes: endpoints, query parameters, filters, response fields, headers,
  status codes, authentication, rate-limit credit costs, caching and compression, and
  the package version shown in the API docs and `/healthcheck`.
- Skip CI, tests, tooling, refactors and dependency upgrades unless they change API
  behavior; then describe that behavior, not the change behind it. There is no
  `Housekeeping:` subsection: list internal changes that API users notice (e.g.
  versioning, compression) under `Improvements:`, and leave out the rest.
- Describe the net change of a batch of related work, not each commit: when something
  is reworked over many commits or PRs, write one entry for the settled result, under
  the month its last PR is merged.
- Months are listed newest first. Skip a month with no entries.

## Crawler upgrades

- Do not write a standalone "Upgrade to `sparecores-crawler` x.y" entry. List the API
  changes the upgrade brings (new, renamed or removed fields, changed values), each
  under its own subsection, and name the crawler version in the entry, e.g.
  ``(`sparecores-crawler` 0.12.0)``.
- Link each crawler version to its release section in the
  [crawler changelog](https://docs.sparecores.com/crawler/news), e.g.
  ``([`sparecores-crawler` 0.12.0](https://docs.sparecores.com/crawler/news#v0120-november-21-2026))``.
  The anchor is the release heading in lowercase, without dots, parentheses and commas,
  and with spaces as hyphens.

## Subsections

- Use these subsections in this order and omit empty ones. Keep the headings exactly
  as written, in the plural even for a single entry, so the file stays easy to parse:
  - `Breaking changes:`
  - `New features:`
  - `Improvements:`
  - `Fixes:`
- A breaking change is a change in the data contract that can break an existing client:
  - a removed or renamed endpoint, query parameter, filter or response field;
  - a changed type, unit or meaning of a field;
  - a filter that rejects values it used to accept;
  - fields renamed or dropped by a crawler upgrade.

  State the cause and what clients should use instead. An endpoint or parameter that
  keeps working through a redirect or alias is not breaking; list it under
  `Improvements:`.
- A new endpoint, query parameter or filter is a feature, even when it joins an existing
  filter category, and so is any other new capability. A field or header added to an
  existing response is an improvement.

## Entries

- Describe what API users get now, not how it was implemented. Write each entry as a
  noun phrase or in the present tense; do not start it with a verb in any tense (`Add`,
  `Added`, `Fix`, `Fixed`, `Upgrade`, `Expose`, `Return`). Under `Improvements:`, start
  an entry for something added with "New", so readers can tell it is new.
  - Breaking change: `` `Server.gpu_*` and `gpus` fields renamed to `accelerator_*` and
    `accelerators` in responses and `order_by` (`sparecores-crawler` 0.10.0); the
    `gpu_*` query parameters keep their names ``
  - New feature: `` `/ping` liveness check that answers as long as the process runs,
    without the database or the thread pool shared by the other endpoints ``
  - Improvement: `` New `Retry-After` header in 429 responses, exposed to browsers via
    CORS along with the `X-RateLimit-*` headers ``
  - Fix: `` The `family` method of `/server/{vendor}/{server}/similar_servers/{by}/{num}`
    returns the servers of the same series ``, not `Fix the family method …`
- Be specific: name the endpoints, parameters, fields and headers in backticks, and give
  versions (e.g. `sparecores-crawler` 0.12.0). Group related small changes into one
  entry instead of writing "various improvements".
- End each entry with the PR number(s) when there is a PR, e.g. `(#123)` or
  `(#12, #15)`; no trailing period.

## Formatting

- Wrap entries at about 88 characters and indent continuation lines by two spaces.

## References

- GitLab, [Changelog entries](https://docs.gitlab.com/development/changelog/): describe
  the change by its effect on the reader, not its implementation.
- Google developer documentation style guide,
  [Present tense](https://developers.google.com/style/tense) and
  [Timeless documentation](https://developers.google.com/style/timeless-documentation):
  prefer the present tense; time-bound wording is fine in release notes.
