# Credits

ScieFlow's literature kernel vendors code from **researcher** (MIT License), version
1.0.0, at https://github.com/sokolmarek/researcher. The licence text is kept with the copied code in
`src/scieflow/_vendor/researcher_core/LICENSE`.

What was copied, and why: the deterministic dedupe, rank, inbound-sanitiser and query-repair
modules, which are tested upstream and are used by the search fan-out (M-C1 t4). Every
vendored file, its checksum and the single line changed in it are listed in
[VENDORED.md](VENDORED.md). The transport, cache, ledger, snapshot and network-mode code is
ScieFlow's own.
