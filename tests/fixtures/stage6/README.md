# Stage 6 offline fixtures

These fixtures are inert test inputs. They are copied into a pytest `tmp_path`
or passed to an isolated service; they never read the user's Morrow state,
credential store, project files or external MCP configuration.

- `handwritten-skill/` is the imported Skill used by the integrated lifecycle,
  selection, frozen resource and workspace-isolation acceptance.
- `generated-draft/candidate.json` is the bounded candidate payload used as the
  source facts for a generated Draft acceptance.
- `fake-provider.py` documents the second local Provider adapter shape; the
  acceptance test registers the adapter in memory and never calls a network.
- The deterministic Fake stdio MCP process is
  `tests/spikes/fake_mcp_stdio_server.py`; MCP acceptance starts it only with a
  temporary state and the local Python interpreter.
