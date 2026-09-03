"""Local Core API server: versioned Command/Query/Event/Approval protocol.

The server is a projection of Core state owned by the Core process. It never
introduces a second Agent implementation, a second event truth, or a second
writer for any durable datum.
"""
