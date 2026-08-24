"""Skill package adapters: canonical trees, manifests, envelopes and discovery.

The managed package storage layout is:

    skills/<source-kind>/<skill-id>/<skv-id>/package/...
    skills/<source-kind>/<skill-id>/<skv-id>/managed-version.json

Only Morrow writes envelopes; packages may never carry their own
``managed-version.json`` inside ``package/`` (reserved name).
"""
