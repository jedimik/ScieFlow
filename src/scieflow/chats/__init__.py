"""Selective backup and cross-machine restore of agent chats.

Agent conversations live in per-tool HOME directories (`~/.claude`, `~/.codex`,
`~/.gemini`) and every store keys its data by absolute path, so a plain copy
produces sessions the CLI cannot find. This module picks chats one by one,
associates only the skills and plugins those chats actually used, packs them
into one encrypted bundle, and restores that bundle on another machine with
the paths rewritten.
"""
