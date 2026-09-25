---
name: research-synthesis
description: Answering questions from a body of documents or sources - search, cross-reference, multi-hop reasoning, cited synthesis.
---
## Workflow
1. **Map the corpus**: list files, sizes; grep for key entities from the question to find candidate documents.
2. **Multi-hop**: questions often chain (A → owned by B → led by C). Resolve each hop explicitly and note the source file for each fact in `notes`.
3. **Resolve conflicts**: prefer the most recent/authoritative document (look for dates, "deprecated", "superseded", "update" markers). Mention the conflict if it matters.
4. **Parallelise** broad questions: when there are several independent sub-questions or a large corpus, use `delegate` with one sub-agent per sub-question, each told exactly what to return with sources.
5. **Answer precisely**: exact names/values as they appear in the sources; cite the file(s). Say explicitly if something could not be found rather than guessing.
