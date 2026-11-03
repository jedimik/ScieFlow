"""Synthetic claim-ledger fixture (M-G t1). Every study, number, file and reference here is invented.

`claims_ms` writes a small article into the synthetic run's manuscript/ folder (the `synth` fixture of the
parent conftest). The line numbers asserted by the claim tests are the line numbers of CLAIMS_MAIN below.
"""

from __future__ import annotations

import pytest

CLAIMS_MAIN = r"""\documentclass[11pt]{article}
\bibliographystyle{plainnat}
\begin{document}
\title{Gait timing in synthetic cohorts}
\begin{abstract}
Synthetic abstract reports 42 invented walkers. % source: [data:tbl-metrics]
\end{abstract}
\maketitle
\section{Introduction}
Gait timing matters in 1200 invented walkers \cite{smith2001}.
\input{methods}
\section{Results}
Mean stride time was 0.91 s in block one. % source: [data:tbl-metrics]
Block two reached 1,234 steps as Figure~\ref{fig:one} shows. % source: [run:denoise/sigma-1.5]
Accuracy stayed under 5\% in the 2019 cohort. % source: [data:tbl-metrics]
The fit was $p < 0.05$ in block three.
\begin{equation}
y = 12.5 x
\end{equation}
Sigma was 1.5 in the inline run [run:denoise/sigma-1.5].
\begin{figure}
\includegraphics[width=0.5\textwidth]{fig1.pdf}
\caption{Synthetic figure one, n = 7 walkers.}
\end{figure}
\begin{table}
\caption{Synthetic table of metrics.} % source: [data:tbl-metrics]
\begin{tabular}{lc}
Stride & 0.91 \\
\end{tabular}
\end{table}
\section{Discussion}
Discussion of synthetic results.
\bibliography{references}
\end{document}
"""

METHODS_TEX = "\\section{Methods}\nThree cameras recorded walkers \\cite{lee2010}.\n"

BIB_CLAIMS = """@article{smith2001,
  author = {Synthetic, A.},
  title = {A synthetic title},
  year = {2001}
}
@article{lee2010,
  author = {Invented, B.},
  title = {Another invented title},
  year = {2010},
  doi = {https://doi.org/10.5555/SYN.2010.1}
}
"""

# The claim units of CLAIMS_MAIN in document order: (kind, file, first line, last line, section).
FIXTURE_UNITS = [
    ("abstract", "main.tex", 6, 6, ""),
    ("sentence", "main.tex", 10, 10, "Introduction"),
    ("sentence", "methods.tex", 2, 2, "Methods"),
    ("sentence", "main.tex", 13, 13, "Results"),
    ("sentence", "main.tex", 14, 14, "Results"),
    ("sentence", "main.tex", 15, 15, "Results"),
    ("sentence", "main.tex", 16, 16, "Results"),
    ("sentence", "main.tex", 17, 20, "Results"),
    ("figure", "main.tex", 21, 24, "Results"),
    ("table", "main.tex", 25, 30, "Results"),
    ("sentence", "main.tex", 32, 32, "Discussion"),
]


@pytest.fixture
def claims_ms(synth):
    """The synthetic article with the claim fixture; returns the manuscript folder."""
    synth.write_manuscript({"main.tex": CLAIMS_MAIN, "methods.tex": METHODS_TEX,
                            "references.bib": BIB_CLAIMS})
    return synth.ms
