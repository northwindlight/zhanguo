"""Compatibility re-export.

The economic contract lives in :mod:`mods.native.contracts.economy`; this module
stays so older imports keep working while the owner stops being a domain module.
"""
from mods.native.contracts.economy import *  # noqa: F401,F403
