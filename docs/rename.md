# Renaming the template's plugin

A repository made from this template still carries the sample plugin `lumen-hello` of the fictional publisher
`lumen`, signed by the template's own workflow. Rename three things before the first release.

| What | Template value | Becomes | Where |
| --- | --- | --- | --- |
| Plugin id | `lumen-hello` | your id: `^[a-z][a-z0-9-]{0,62}$`, unique on the market | `evo-plugin.json` (`id`), `market-entry.json` (`id`), log lines in `backend/main.py` |
| Publisher | `lumen` (name `Lumen Labs`) | your publisher id, the same for every plugin you publish | `evo-plugin.json` (`publisher`), `market-entry.json` (`publisher.id`, `publisher.name`) |
| Signer | `sigstore:maycuatroi1/evo-plugin-template/.github/workflows/ci.yml` | `sigstore:OWNER/REPO/.github/workflows/ci.yml` of your repository | `market-entry.json` (`publisher.signer`, `repo`, `homepage`) |

The manifest names no signer: a hub reads it from the market's entry, or takes the repository's
`.github/workflows/ci.yml` by default when an admin installs with `--github OWNER/REPO`. Keep the workflow's file name
`ci.yml`, or every hub that trusts you has to be told the new one.

Then make the plugin yours:

- `evo-plugin.json`: `name`, `description`, `version` (start at `0.1.0`), `permissions` (ask for no more than the
  plugin needs; a project's admin grants them), `config` and `contributes`.
- `backend/main.py`: the routes and tools the manifest contributes. `initialize` lists the manifest's tools, so a tool
  needs only its handler here.
- `pages/`: one directory per page, its words in `i18n/en.js` and `i18n/vi.js` beside it.
- `tests/test_plugin.py`: the tests of what you changed.
- `market-entry.json`: `name`, `description` (280 characters at most), `industries`, `categories`, `icon`, `price`,
  `license`.
- `README.md`: what the plugin does, for its users.

## Change the sample collection into your records

`lumen-hello` keeps one collection of records, `waves` (a lowercase name: letters, digits, `-` and `_`, starting with a
letter). Replace it with the files of your trade: `leave-requests`, `expense-vouchers`, `journal-drafts`, `customers`.

1. Name the collection once. `COLLECTION` in `backend/main.py` and `COLLECTION` in `pages/hello/hello.js` hold it; the
   tests and the page's words say "wave", so search for `wave` and rename what you find.
2. Decide, for each kind of record, its `level` (a level of the project's ladder: a leave request `internal`, an expense
   voucher `finance`) and its `audience` (`everyone` the level reaches, or `writer`, only the person who wrote it).
   The manifest declares them as `config` fields, as `wave_level` and `wave_audience` do, so a project's admin sets them
   when enabling the plugin; or fix them in the code when the trade does not vary. A record may not sit higher than
   the writer reaches.
3. Keep the permission `records:use` in `permissions`, and ask for nothing else the plugin does not need. A project's
   admin grants it; a job or an event, which has no caller, reaches only up to the `records_level` the admin set.
4. Shape the record's `body`: a JSON object of 64 KiB at most. Use generated keys for new records and `revision` on
   every update, so that two people do not overwrite each other.
5. The page keeps using the bridge (`records.list`, `records.get`, `records.put`, `records.delete`); the backend uses
   `Plugin.records`, which sends the plugin token and the call's `caller.token`. Never send a delegation from another call.
6. Rewrite the tests: `Hub` in `tests/test_plugin.py` stands in for the records API; `evo-agents plugin test .` also
   checks, with its own stand-in hub, that the plugin does not call the records without `records:use`, for another
   project's records, or with a delegation the call did not carry.

## Find what is left

```sh
git grep -n -i -e lumen -e evo-plugin-template -- ':!docs/rename.md' ':!README.md' ':!LICENSE'
```

On a fresh copy, this renames the three in one go (GNU sed; set the three values first):

```sh
ID=northwind-ledger PUBLISHER=northwind REPO=northwind/northwind-ledger
git grep -l -e lumen-hello -e evo-plugin-template -e '"lumen"' -- evo-plugin.json market-entry.json backend pages \
  | xargs sed -i -e "s#maycuatroi1/evo-plugin-template#$REPO#g" -e "s#lumen-hello#$ID#g" -e "s#\"lumen\"#\"$PUBLISHER\"#g"
```

Then change `Lumen Labs` and the words of the pages by hand, and run the checks of the README.
