# Security policy

## Reporting a vulnerability

Please report vulnerabilities privately, through GitHub's
[private vulnerability reporting](https://github.com/ianderso/dpla-catalog-mcp/security/advisories/new)
(the **Report a vulnerability** button on the repository's Security tab), not
in a public issue. Include what an attacker controls, what they gain, and the
steps to reproduce it. Never include your DPLA key.

You should hear back within a week. Fixes are released for the latest version
only.

## Scope

In scope: this server — the requests it makes, where its key goes, the files
it writes (its cache), and anything a tool argument, a DPLA response or an
institution's site can make it do.

Out of scope: the DPLA API and website, and the institutions' sites, which
this project does not operate. Report problems with those to DPLA or to the
institution.

## The security model, briefly

- **One credential, one destination.** The key is read from `DPLA_API_KEY`
  (or `.env` in the working directory) and sent only to `https://api.dp.la`,
  only in the `Authorization` header. It is never placed in a URL, so it is
  never in a log line, a cache key or an error. The API address is fixed in
  code, a request hook refuses any other host for the key-bearing client, and
  redirects are not followed. The key is scrubbed from any error text, left
  out of the configuration's `repr`, and never taken as a tool argument.
- **Institutions' sites get nothing.** IIIF manifests are fetched by a
  separate client with no key; any `Authorization` or `Cookie` header is
  stripped. The manifest address comes from a DPLA record, which hundreds of
  institutions write, so it is checked before the request and on every
  redirect: http or https only, a DNS name with a dot, never an IP address, a
  local name (`localhost`, `.local`, `.internal` and the like) or DPLA's API
  host. A name that resolves to a private address is not detected; the
  responses are parsed as JSON and never executed, and only a page list is
  returned.
- **Bounded reads.** A manifest over 15 MB is abandoned part-way.
- **Read requests only.** The server sends GET requests and nothing else. The
  key-request route, which sends email, is absent.
- **Inputs are validated** — ids, dates, text lengths, enumerations, the page
  limit — before they are placed in a request, and query text is escaped for
  DPLA's parser.
- **The only local writes are the cache,** under `DPLA_CACHE_DIR`, in files
  named by a hash of the request (never of the key), written atomically.
- **Responses carry untrusted text.** Descriptions and the raw harvested
  record are written by hundreds of institutions and reach the model verbatim,
  which makes them a channel for prompt injection. The server's instructions
  tell the model to treat that text as material, not as instructions, but the
  model still decides what to do next. An injection that leads the model to
  misuse this server's own tools is in scope; one that leads it to misuse
  other tools the client has connected is a client concern.
