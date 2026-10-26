# Privacy

ScieFlow sends no telemetry. It does not report usage, crashes or run contents
to its authors or to any service of its own.

Data leaves the machine only through the destinations listed in the egress table
of [Data protection](docs/data-protection.md#egress). Each row says what leaves,
when, what controls it, and what an L2 run does there. Destinations include the
literature and scite services you enable, the NotebookLM session you log in to,
the git remotes you configure, and the model endpoint of each agent you dispatch.

You decide what enters a run and what it holds. ScieFlow does not know what a
file contains. Declare the level of each run's data with `scieflow data declare`
(see [Data protection](docs/data-protection.md#declaring-a-runs-data)).

Salts for pseudonymous identifiers, and the credentials ScieFlow uses, stay in
your home folder and are not written into a run or a remote.
