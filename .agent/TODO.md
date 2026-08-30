# TODO

## Current task

None. Production implementation has not been authorized or activated.

## Activation note

Subplan 1 is ready in `.agent/subplans/1-stage7-agent-definition-foundation.md`. When the user
explicitly authorizes production implementation, activate it and copy only its current executable
tasks into this file before making code changes.

## Boundaries

- No production task is in progress until the user explicitly starts Subplan 1.
- No WorkflowDefinition, compiler, scheduler, multi-node execution or default-path switch.
- No second chat-history store or AgentRun/Workflow-owned ConversationLog writer.
- No new dependency, public AgentEvent lifecycle change, runtime-policy default change or Live test.
- Every new rejection rule must include an adjacent legal acceptance test and satisfy the master
  plan's proportionality criteria.
