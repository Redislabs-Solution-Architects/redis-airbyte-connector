# Publishing the documentation

The public documentation lives at
<https://redislabs-solution-architects.github.io/redis-airbyte-connector/>.
MkDocs converts the existing Markdown into linked HTML pages with navigation,
an on-site search index, canonical URLs and a sitemap.

## Edit and preview

Edit the root `README.md`, either connector's `README.md`, or Markdown under
`docs/`. The build reads these files directly; do not edit generated HTML.
`scripts/docs_hook.py` includes the three READMEs as the getting-started, source
and destination pages. Add new guides under `docs/` and list them in the `nav`
section of `mkdocs.yml`.

Use a separate environment; the connector and native Redis driver are not needed:

```sh
python3 -m venv .venv-docs
.venv-docs/bin/python -m pip install -r requirements-docs.txt
.venv-docs/bin/python -m mkdocs serve
```

Open the address printed by MkDocs. To build the publishable files in `site/`:

```sh
.venv-docs/bin/python -m mkdocs build --strict
```

## GitHub Pages deployment

In the upstream repository's **Settings → Pages**, set **Build and deployment →
Source** to **GitHub Actions**. This is a one-time repository setting and requires
the appropriate repository permissions.

The **Documentation site** workflow validates documentation changes on pull
requests and publishes changes pushed to upstream `main`. It can also be run
manually on `main` from the Actions tab. Forks validate the build without deploying
to the upstream site. Documentation publishing is independent of connector
releases and does not publish Python packages or container images.

## Google discovery

The generated HTML is public and linked through the navigation. Each page has
a title and canonical URL. MkDocs generates `sitemap.xml` at
<https://redislabs-solution-architects.github.io/redis-airbyte-connector/sitemap.xml>.

After the site is live, verify ownership of its URL-prefix property in
[Google Search Console](https://search.google.com/search-console/about) and
submit the sitemap URL. The verification method depends on your account and
domain access. Google decides when and whether to index pages; publication and
sitemap submission do not guarantee search placement.

If a custom domain is added, update `site_url` in `mkdocs.yml` to match it so
canonical URLs and sitemap entries continue to use the public address.
