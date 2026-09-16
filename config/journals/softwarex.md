# SoftwareX — Submission Profile

## Scope and audience

SoftwareX publishes openly inspectable and reusable research software whose
scientific relevance and potential impact can be peer reviewed and cited. Its
audience spans research-software engineers and researchers in mathematical,
physical, engineering, environmental, medical, biological, humanities, and
social-science domains. The journal particularly welcomes reusable,
domain-independent tools and software that can affect more than one research
domain.

The submission has two linked objects: a short descriptive paper and an
open-source software distribution with supporting material. The paper is an
accompanying note for readers and potential software users, not a conventional
full-length validation article.

## Article types (with length limits)

- **Original Software Publication:** for a software package not previously
  published in SoftwareX. This is the appropriate type for SegSnake.
- **Software Update:** for an update to software already published in
  SoftwareX; it has a separate journal template.
- **Unresolved official-source conflict:** the current ScienceDirect SoftwareX
  page states a **3,000-word limit** for the descriptive paper. The bundled
  copyright-2026 Elsevier SoftwareX OSP LaTeX template states **4,000 words**
  and a **maximum of six pages**, excluding metadata, tables, figures, and
  references, with priority placed on word count. Unless the editor confirms
  otherwise, use the conservative intersection: **no more than 3,000 words and
  no more than six main-text pages**.
- The same SoftwareX-specific template says **“Ca. 100 words”** for the
  abstract and allows at most **six figures**. A longer abstract range reported
  by the support-agent pass could not be tied to a retrieved
  SoftwareX-specific official passage and is rejected.
- An optional illustrative video is limited by the template to one MP4 file,
  150 MB, recommended 640 x 480 pixels at no more than 30 frames/s.

## Formatting requirements (structure, figures, references style)

- Use the journal-specific Original Software Publication Word or LaTeX
  template. The supplied LaTeX template uses `elsarticle` in single-column
  preprint format.
- Include the mandatory Code Metadata table with C1--C8: current version,
  permanent GitHub link, license, version-control system, languages/tools,
  requirements/dependencies, documentation link, and support email.
- The bundled 2026 template says the C2 repository must be on GitHub and that
  the repository must contain a documented `README.md` and a license file.
- Preserve the five required main sections and their order: **Motivation and
  significance**, **Software description**, **Illustrative examples**,
  **Impact**, and **Conclusions**.
- Include at least one reproducible illustrative example. Use no more than six
  figures; captions belong with their figures and every figure/table must be
  cited in the text.
- Use Elsevier numbered references (`elsarticle-num` or an equivalent numeric
  style). Every in-text citation must resolve and every listed reference must
  be cited.
- The current guide includes title-page, abstract, keywords, highlights,
  graphical-abstract, research-data, data-statement, ethics, competing-interest,
  funding, and generative-AI guidance. The exact highlight/keyword numerical
  limits were not recoverable from a SoftwareX-specific official passage in
  this run, so generic Elsevier numbers must not be represented as verified
  SoftwareX limits.

## Review criteria (what reviewers at this journal weight most)

- The journal template explicitly says **Impact** is the main section and will
  be weighted accordingly. It should explain new questions enabled, improved
  pursuit of existing questions, effects on users' practice, reuse/adoption,
  and any commercial impact, without inventing adoption metrics.
- The software must solve a non-trivial scientific or technical problem and
  have a credible route to reuse beyond the immediate demonstration.
- Reviewers must be able to inspect the open-source distribution, understand
  installation and dependencies, and follow documentation and an illustrative
  example.
- Architecture, functionality, inputs, outputs, provenance, and limitations
  should be clear enough for a reader to understand what the software actually
  guarantees.
- The manuscript, metadata table, repository, license, and documentation must
  agree about the released version and capabilities.

## Common rejection reasons

- Deviating substantially from the journal-specific OSP template or omitting
  one of its five required sections.
- Missing/inaccessible GitHub code, inadequate README/documentation, or absent
  acceptable open-source licensing.
- Incomplete or inconsistent Code Metadata fields.
- Exceeding the conservative 3,000-word/six-page envelope without written
  editorial clarification of the conflicting official sources.
- Providing no executable illustrative example or too little information to
  understand installation and use.
- Weakly substantiated impact, novelty, or reuse claims; invented adoption
  metrics; or presenting a conventional research paper instead of a focused
  software publication.
- Manuscript claims that exceed what the software or supplied evidence can
  support.

## Submission checklist

- [ ] Select **Original Software Publication** and use its current template.
- [ ] Keep main descriptive text at or below 3,000 words and six pages, or
  obtain written clarification from `softwarex@elsevier.com` about the current
  3,000-vs-4,000-word conflict.
- [ ] Reduce the abstract to approximately 100 words.
- [ ] Retain all five required main sections and the complete C1--C8 metadata
  table.
- [ ] Verify the permanent public GitHub URL, released version, MIT license,
  root README/license filenames, documentation URL, and support email.
- [ ] Include at least one reproducible example and no more than six figures.
- [ ] Ensure all claims and numbers trace to the frozen evidence package and
  preserve the technical-robustness/not-anatomical-validity boundary.
- [ ] Supply the final affiliation, funding/HPC acknowledgement, competing-
  interest statement, and any applicable author-contribution, data/code-
  availability, and AI-use declarations requested by the live submission
  system.
- [ ] Provide any required highlights/graphical abstract as separate submission
  files only after rechecking their current SoftwareX-specific portal fields.
- [ ] Resolve every citation in both directions and compile the final editable
  LaTeX sources successfully.

Official sources cross-checked on 2026-07-22:

- [SoftwareX Guide for Authors](https://www.sciencedirect.com/journal/softwarex/publish/guide-for-authors)
- [SoftwareX journal page and aims/scope](https://www.sciencedirect.com/journal/softwarex)
- Bundled Elsevier OSP template:
  `workspace/2026-07-segsnake-softwarex-paper1-validation/manuscript/figures/template/softwarex-osp-template.tex`

The web pages returned HTTP 403 to direct retrieval, so current live claims
were cross-checked against the official ScienceDirect search index. The local
copyright-2026 Elsevier template was read directly. Their conflicting word
limits are intentionally not silently reconciled.
