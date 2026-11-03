"""`scieflow manuscript revision` (M-G 5.10-5.11): ingest a decision letter, prompt, adopt the comments.

letter.py stores and splits the letter, draft.py builds the structuring prompt for one sub-agent, adopt.py
checks the agent's draft against the stored letter and writes one manuscript-review document per block.
Later steps (response letter, coverage, render: M-G 5.12) live in the same package.
"""
