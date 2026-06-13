"""Shared command layer — the single implementation of every CLI verb.

Both the terminal CLI (typer) and the browser console call ``dispatch`` here, so
command logic lives in exactly one place. The terminal parses argv via typer; the
browser parses raw strings via ``parse_command_line``; both converge on dispatch.
"""
