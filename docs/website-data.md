# Version-gated website snapshots

Run this explicit maintenance command when you want to check for website updates:

```bash
.venv/bin/python refresh_website_data.py
```

The app also runs this refresh on its first page load after server startup,
before accepting questions. `prepare_website_data()` is cached with
`st.cache_resource`: one successful check per server process, shared by all
sessions and reruns, without repeating it for conversation resets. Restarting
the server (or explicitly clearing its resource cache) causes a fresh check.

Preparation refreshes snapshots if needed, reloads in-memory contact routing,
then reads the refreshed FAQ count. Only afterward can question validation,
corpus indexing and query embeddings run. The vector store still rebuilds only
when normalized FAQ content changes, not just because the site version changed.
If preparation fails, chat is disabled and a retry button is shown. Failures are
not cached as success, and no model/embedding calls run until preparation works.
This intentionally does not fall back to stale data when the site is unreachable.

No OpenAI API calls, embeddings or paid evaluation runs are made by the refresh
itself. CLI evaluation continues to use pinned snapshots without an automatic
network refresh, preserving the dataset/corpus freshness checks.

## Refresh flow

1. GET the [contact index](https://www.custojusto.pt/ajuda-contacto) and read the
   `content` attribute of its single `<meta name="version">` tag. The value is
   an opaque string, not an incrementing number or a content hash.
2. Compare it with `website_version` in `data/website_snapshot.json`. If it is
   unchanged and both snapshots exist, verify their checksums and stop. Neither
   scraper nor the browser runs, and no files or timestamps are rewritten.
3. On first run, any missing snapshot/marker, or a changed version, retrieve both
   sources: the FAQ JSON and every contact category. Reuse the already-fetched
   index. Missing files deliberately trigger recovery even for the same version.
4. Require matching version tags on every source page and validate both complete
   datasets before saving. Missing/empty/duplicate version tags, failed requests,
   malformed JSON, changed contact sections or unexpected headings abort the run.
5. Stage all output files, replace each snapshot atomically, then publish the
   success marker last. Network/validation failures leave the old files intact.
   This is not a multi-file filesystem transaction; a process/disk failure during
   replacement can leave a partial update, but cannot mark it successfully synced.

The saved marker records the version, URLs, successful refresh time and exact
SHA-256 checksums of `faqs.json` and `contact_forms.json`. Keep these three files
together. If any are deleted, startup refresh recreates the complete set after
successful scraping and validation. This includes deleting all three files:
importing the app tolerates a missing contact snapshot and loads it after
recovery, before enabling chat. A failed recovery leaves surviving files intact
and missing files absent until a successful retry.

Existing edited snapshots are different from missing ones: for an unchanged
version, checksum mismatches still cause an error rather than silently overwriting
local edits, even when the neighboring snapshot is missing. Restore the matching
files to recover from edits. A corrupt existing marker also remains an error.
For an independent full rebootstrap, use a new `--data-dir`, inspect its results
and adopt that complete set together; there is no force-refresh flag.

## FAQ source

The default source is
[the FAQ page](https://www.custojusto.pt/ajuda?section=faq). The extractor reads
only the `<script id="faqs">` JSON-LD object, preserving the existing `FAQPage`,
`mainEntity`, question order and answer HTML. It does not read accordions, execute
JSON as JavaScript or reconstruct answers from visible text.

If the script is present in the HTTP response, it is parsed directly. The current
site inserts it client-side: Playwright opens a fresh, unauthenticated headless
browser and waits for that exact script. Requests are limited to HTTPS GETs on
`www.custojusto.pt`; images, fonts, media, third-party requests and submissions are
blocked. The browser is closed after extracting the page. The rendered version
must still match the initial probe. There is no accordion fallback.

Dependencies are installed with `uv sync`. An installed `google-chrome` on PATH
is used automatically; otherwise install Playwright's Chromium once:

```bash
.venv/bin/playwright install chromium
```

Useful options:

```bash
.venv/bin/python refresh_website_data.py --data-dir /tmp/custojusto-snapshots
.venv/bin/python refresh_website_data.py --faq-url 'https://www.custojusto.pt/ajuda?section=faq'
```

The FAQ URL must be on the official HTTPS host. Changing it for an already-synced,
unchanged version is rejected to avoid reusing another source's snapshots.
The older `scrape_contact_forms.py` command and the new `scrape_faqs.py` command
both delegate to the same version-gated refresh of **both** datasets. Use
`--data-dir` for an alternative output set; the old single-file `--output` option
has been replaced so a refresh does not update only half of the dataset.

## Freshness and review

Website version, normalized FAQ content hash, and contact-catalog hash have
different purposes. A version change triggers a source refresh; unchanged FAQ
content does not rebuild Chroma, and unchanged routing content does not invalidate
evaluation labels. No evaluation labels or corpus/catalog hashes are automatically
approved or rewritten. If content changes, review them before running evaluation.
Startup preparation reloads the imported contact catalog automatically. Restart
a running app after a separate manual refresh to repeat that preparation.

This policy deliberately trusts the site's version marker. A content change
without a version change will not be detected. The current marker matches the
site's JavaScript build ID, so it should not be treated as proof that all support
content changes necessarily bump the version.
