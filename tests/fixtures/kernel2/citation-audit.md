# Example: Citation Existence and Retraction Audit

| Field | Value |
|---|---|
| Skill | citation-management (existence gate); citation-audit after Phase 3 |
| Command | /researcher:verify-citations (planned command; the audit runs today via the citation-management skill) |
| Trigger phrase | "Verify every reference in my bibliography actually exists and check for retractions" |
| Connectors used | OpenAlex, Crossref, Semantic Scholar, arXiv (existence gate); OpenAlex is_retracted + Crossref update-to (retraction sweep) |
| Generated | 2026-07-12, all real entries verified on this date |

## Invocation

> Verify every reference in `references/library.bib` actually exists in the literature, flag anything that cannot be resolved, and check for retractions.

## Input

A 10-entry `library.bib`. Nine entries are the real papers from the shared example bibliography. **One entry (`kessler2021cmae`) is synthetic, inserted deliberately for demonstration** so the gate has something to reject. It is clearly labeled below and does not appear in any other example.

```bibtex
@article{mehari2022ssl,
  title={Self-supervised representation learning from 12-lead ECG data},
  author={Mehari, Temesgen and Strodthoff, Nils},
  journal={Computers in Biology and Medicine}, volume={141}, pages={105114}, year={2022},
  doi={10.1016/j.compbiomed.2021.105114}}

@article{diamant2022pclr,
  title={Patient contrastive learning: A performant, expressive, and practical approach to electrocardiogram modeling},
  author={Diamant, Nathaniel and Reinertsen, Erik and Song, Steven and Aguirre, Aaron D. and Stultz, Collin M.},
  journal={PLOS Computational Biology}, volume={18}, number={2}, pages={e1009862}, year={2022},
  doi={10.1371/journal.pcbi.1009862}}

@article{wagner2020ptbxl,
  title={PTB-XL, a large publicly available electrocardiography dataset},
  author={Wagner, Patrick and Strodthoff, Nils and Bousseljot, Ralf-Dieter and Kreiseler, Dieter and others},
  journal={Scientific Data}, volume={7}, pages={154}, year={2020},
  doi={10.1038/s41597-020-0495-6}}

@article{strodthoff2021benchmark,
  title={Deep Learning for ECG Analysis: Benchmarks and Insights from PTB-XL},
  author={Strodthoff, Nils and Wagner, Patrick and Schaeffter, Tobias and Samek, Wojciech},
  journal={IEEE Journal of Biomedical and Health Informatics}, volume={25}, number={5}, pages={1519--1528}, year={2021},
  doi={10.1109/jbhi.2020.3022989}}

@article{ribeiro2020automatic,
  title={Automatic diagnosis of the 12-lead ECG using a deep neural network},
  author={Ribeiro, Ant{\^o}nio H. and others},
  journal={Nature Communications}, volume={11}, pages={1760}, year={2020},
  doi={10.1038/s41467-020-15432-4}}

@article{hannun2019cardiologist,
  title={Cardiologist-level arrhythmia detection and classification in ambulatory electrocardiograms using a deep neural network},
  author={Hannun, Awni Y. and others},
  journal={Nature Medicine}, volume={25}, number={1}, pages={65--69}, year={2019},
  doi={10.1038/s41591-018-0268-3}}

@article{lai2023practical,
  title={Practical intelligent diagnostic algorithm for wearable 12-lead ECG via self-supervised learning on large-scale dataset},
  author={Lai, Jiewei and Tan, Huixin and Wang, Jinliang and Ji, Lei and others},
  journal={Nature Communications}, volume={14}, pages={3741}, year={2023},
  doi={10.1038/s41467-023-39472-8}}

@article{liu2023review,
  title={Self-Supervised Contrastive Learning for Medical Time Series: A Systematic Review},
  author={Liu, Ziyu and Alavi, Azadeh and Li, Minyi and Zhang, Xiang},
  journal={Sensors}, volume={23}, number={9}, pages={4221}, year={2023},
  doi={10.3390/s23094221}}

@article{sarkar2022ssl,
  title={Self-Supervised ECG Representation Learning for Emotion Recognition},
  author={Sarkar, Pritam and Etemad, Ali},
  journal={IEEE Transactions on Affective Computing}, volume={13}, number={3}, pages={1541--1554}, year={2022},
  doi={10.1109/taffc.2020.3014842}}

% (synthetic, for demonstration); this reference does not exist
@article{kessler2021cmae,
  title={Contrastive masked autoencoders for single-lead ECG anomaly detection},
  author={Kessler, Marta and Vaughan, Thomas},
  journal={IEEE Transactions on Biomedical Engineering}, volume={68}, number={9}, pages={2811--2820}, year={2021},
  doi={10.1109/TBME.2021.3098765}}
```

<!-- freshness: expect-unresolvable 10.1109/TBME.2021.3098765 -->

## Output

### Existence gate

Each reference is checked against up to four indexes. A verdict of `verified` requires confirmation by at least two independent indexes with title similarity at least 0.70 and matching year and first author.

| Key | Verdict | Confirmed by | Notes |
|---|---|---|---|
| mehari2022ssl | verified | OpenAlex, Crossref, S2 | title 1.00, year match |
| diamant2022pclr | verified | OpenAlex, Crossref, S2 | title 1.00, year match |
| wagner2020ptbxl | verified | OpenAlex, Crossref, S2 | title 1.00, year match |
| strodthoff2021benchmark | verified | OpenAlex, Crossref | title 1.00; IEEE JBHI 25(5) |
| ribeiro2020automatic | verified | OpenAlex, Crossref, S2 | title 1.00, year match |
| hannun2019cardiologist | verified | OpenAlex, Crossref, S2 | title 1.00, year match |
| lai2023practical | verified | OpenAlex, Crossref | title 1.00, year match |
| liu2023review | verified | OpenAlex, Crossref | title 1.00, year match |
| sarkar2022ssl | verified | OpenAlex, Crossref | title 1.00; TAC 13(3) |
| **kessler2021cmae** | **unresolvable** | none | DOI 10.1109/TBME.2021.3098765 does not resolve in Crossref; no title match in any index. **Likely fabricated. (synthetic, for demonstration)** |

**Gate result: 9 verified, 0 mismatch, 1 unresolvable.**

The `unresolvable` entry is refusal-grade: the audit will not certify this bibliography as clean while it remains. A real run would stop here and ask the author to supply a genuine source or remove the citation.

### Retraction and correction sweep

Checked via OpenAlex `is_retracted` and Crossref `update-to` metadata.

| Key | Retracted? | Correction/erratum on record |
|---|---|---|
| all 9 verified entries | No | hannun2019cardiologist has a Publisher Correction (Nat Med, 2019; 10.1038/s41591-019-0359-9); ribeiro2020automatic has an Author Correction (Nat Commun, 2020; 10.1038/s41467-020-16172-1). Neither is a retraction; both are routine post-publication corrections and are noted, not flagged. |

**Sweep result: 0 retractions. 2 routine corrections noted for author awareness.**

### Audit verdict

> **NOT CLEAN.** 1 of 10 references is unresolvable (`kessler2021cmae`) and must be resolved or removed before this bibliography can be certified. No retractions found. Two corrections noted.

## What this demonstrates

- The existence gate confirms real references across multiple independent indexes and produces an `unresolvable` verdict for a fabricated citation, which is exactly the fabricated-citation failure mode the tool exists to catch.
- The single fake entry is unmistakably labeled `(synthetic, for demonstration)` and is the only invented reference anywhere in `examples/`.
- The retraction sweep distinguishes a genuine retraction (none here) from routine corrections and errata (present, noted without alarm), rather than conflating the two.
- The audit refuses to certify a bibliography that contains an unresolvable reference, modeling the human-in-the-loop stop that the Phase 3 citation-audit skill and the Phase 4 integrity gate enforce.
