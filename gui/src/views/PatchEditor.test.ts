import { describe, expect, it } from 'vitest'
import { ApiError } from '../api/client'
import type {
  PatchApplyResultWire,
  PatchDiffWire,
  PatchValidationWire,
  WorkflowDefinitionSourceWire,
  WorkflowRunWire,
} from '../api/types'
import {
  buildPatchDraft,
  canApplyPatch,
  diffSummaryLines,
  patchApplyErrorMessage,
  patchId,
  patchResultMessage,
} from './lib/editor'
import { RISK_REASON_LABELS } from './lib/labels'

function makeRun(overrides: Partial<WorkflowRunWire> = {}): WorkflowRunWire {
  return {
    workflow_run_id: 'wrun_parent0001',
    workflow_revision_id: 'wrev_base0001',
    root_task_run_id: 'task_root_0001',
    status: 'paused',
    row_version: 7,
    started_at: null,
    completed_at: null,
    budget_snapshot: {
      max_agent_generation_requests: null,
      default_node_max_agent_generation_requests: null,
      admission_timeout_seconds: null,
      max_concurrency: 1,
    },
    admission_deadline_at: null,
    input_artifacts: [],
    result_status: null,
    pending_terminal_intent: null,
    pause_requested: true,
    run_relation: 'initial',
    lineage_budget_root_run_id: null,
    parent_run_id: null,
    superseded_reason: null,
    ...overrides,
  }
}

function makeSource(): WorkflowDefinitionSourceWire {
  return {
    workflow_definition_id: 'wdef_0001',
    name: 'Flow',
    description: '',
    tags: [],
    origin: 'user',
    input_contract: { kind: 'TaskContract', version: 1 },
    required_outputs: [{ node_id: 'agent_1', output_slot: 'result' }],
    edges: [],
    default_budget: {
      max_agent_generation_requests: null,
      default_node_max_agent_generation_requests: null,
      admission_timeout_seconds: null,
      max_concurrency: 1,
    },
    nodes: [],
  }
}

function makeDiff(overrides: Partial<PatchDiffWire> = {}): PatchDiffWire {
  return {
    added_node_ids: ['agent_2'],
    removed_node_ids: [],
    changed_node_ids: [],
    added_edges: [],
    removed_edges: [],
    required_outputs_changed: false,
    budget_changed: false,
    ...overrides,
  }
}

function makeValidation(overrides: Partial<PatchValidationWire> = {}): PatchValidationWire {
  return {
    valid: true,
    diagnostics: [],
    past_node_ids: ['agent_1'],
    execution_node_ids: ['agent_2'],
    diff: makeDiff(),
    risk: { level: 'low', reasons: [] },
    ...overrides,
  }
}

function makeApplyResult(overrides: Partial<PatchApplyResultWire> = {}): PatchApplyResultWire {
  return {
    workflow_patch_id: 'wpatch_0001',
    workflow_revision_id: 'wrev_child0001',
    parent_run: makeRun(),
    child_run: makeRun({ workflow_run_id: 'wrun_child0001', status: 'running' }),
    driving: true,
    ...overrides,
  }
}

describe('buildPatchDraft', () => {
  it('assembles the wire patch from the paused parent run and editable source', () => {
    const source = makeSource()
    const patch = buildPatchDraft({
      workflow_patch_id: 'wpatch_sess_1',
      workspace_id: 'ws_one',
      run: makeRun(),
      source,
    })

    expect(patch).toEqual({
      workflow_patch_id: 'wpatch_sess_1',
      workspace_id: 'ws_one',
      parent_run_id: 'wrun_parent0001',
      base_workflow_revision_id: 'wrev_base0001',
      expected_parent_row_version: 7,
      source,
      requested_by: 'gui_user',
    })
    expect(patch.request_kind).toBeUndefined()
  })

  it('honors an explicit requested_by and request_kind', () => {
    const patch = buildPatchDraft({
      workflow_patch_id: 'wpatch_sess_2',
      workspace_id: 'ws_one',
      run: makeRun(),
      source: makeSource(),
      requested_by: 'cli_user',
      request_kind: 'approved_proposal',
    })

    expect(patch.requested_by).toBe('cli_user')
    expect(patch.request_kind).toBe('approved_proposal')
  })
})

describe('canApplyPatch', () => {
  it('allows a low-risk valid preview without acknowledgement', () => {
    expect(canApplyPatch(makeValidation(), false)).toBe(true)
    expect(canApplyPatch(makeValidation({ risk: { level: 'low', reasons: ['node_removed'] } }), false)).toBe(true)
  })

  it('requires the explicit acknowledgement when the risk is elevated', () => {
    const elevated = makeValidation({ risk: { level: 'elevated', reasons: ['node_removed'] } })
    expect(canApplyPatch(elevated, false)).toBe(false)
    expect(canApplyPatch(elevated, true)).toBe(true)
  })

  it('blocks apply on an invalid preview even when acknowledged', () => {
    const invalid = makeValidation({ valid: false, diagnostics: [{ severity: 'error', code: 'x', message: 'bad', node_id: null, edge_id: null }] })
    expect(canApplyPatch(invalid, true)).toBe(false)
  })

  it('blocks apply when there is no preview at all', () => {
    expect(canApplyPatch(null, true)).toBe(false)
    expect(canApplyPatch(null, false)).toBe(false)
  })

  it('treats a valid preview without risk info as not elevated', () => {
    expect(canApplyPatch(makeValidation({ risk: null }), false)).toBe(true)
  })
})

describe('diffSummaryLines', () => {
  it('lists changed ids and boolean flags in stable order, omitting empty sets', () => {
    const lines = diffSummaryLines(
      makeDiff({
        added_node_ids: ['agent_2'],
        removed_node_ids: ['agent_0'],
        changed_node_ids: ['agent_1'],
        added_edges: ['agent_1->agent_2'],
        removed_edges: [],
        required_outputs_changed: true,
        budget_changed: true,
      }),
    )

    expect(lines).toEqual([
      { label: '新增节点', value: 'agent_2' },
      { label: '删除节点', value: 'agent_0' },
      { label: '修改节点', value: 'agent_1' },
      { label: '新增边', value: 'agent_1->agent_2' },
      { label: '结果输出已变更', value: null },
      { label: '预算/并发/超时已变更', value: null },
    ])
  })

  it('returns no lines for a null diff', () => {
    expect(diffSummaryLines(null)).toEqual([])
  })
})

describe('patchResultMessage', () => {
  it('names the continuation child and the superseded parent', () => {
    const message = patchResultMessage(makeApplyResult())
    expect(message).toContain('wrun_child0')
    expect(message).toContain('已创建并从 running 继续')
    expect(message).toContain('superseded')
  })

  it('explains an empty execution set finishing the parent directly', () => {
    const message = patchResultMessage(makeApplyResult({ child_run: null }))
    expect(message).toContain('执行集为空，父运行已直接收尾')
    expect(message).toContain('superseded')
  })
})

describe('patchApplyErrorMessage', () => {
  it('passes ApiError messages through as-is (compile errors)', () => {
    const compile = new ApiError(400, 'invalid', 'compile failed', null, [
      { severity: 'error', code: 'x', message: 'bad', node_id: null, edge_id: null },
    ])
    expect(patchApplyErrorMessage(compile)).toBe('compile failed')
  })

  it('adds reopen guidance on a 409 stale-parent conflict', () => {
    const stale = new ApiError(409, 'conflict', 'patch parent revision conflict')
    expect(patchApplyErrorMessage(stale)).toContain('patch parent revision conflict')
    expect(patchApplyErrorMessage(stale)).toContain('关闭并重新打开此流程')
  })

  it('handles generic errors and unknown values', () => {
    expect(patchApplyErrorMessage(new Error('boom'))).toBe('boom')
    expect(patchApplyErrorMessage('plain')).toBe('plain')
  })
})

describe('patchId', () => {
  it('generates wpatch_ prefixed ids with underscore separators only', () => {
    const id = patchId()
    expect(id.startsWith('wpatch_')).toBe(true)
    expect(id).not.toContain('-')
  })
})

describe('RISK_REASON_LABELS', () => {
  it('covers every known patch risk reason code with non-empty labels', () => {
    const codes = [
      'node_removed',
      'review_or_test_gate_removed',
      'output_contract_relaxed',
      'report_dependency_removed',
      'control_edge_removed',
      'writer_order_changed',
      'required_outputs_retargeted',
      'conversation_scope_changed',
      'provider_model_boundary_changed',
      'cap_or_deadline_relaxed',
      'permission_widened',
      'role_replaced',
    ]
    for (const code of codes) {
      expect(RISK_REASON_LABELS[code]).toBeTruthy()
    }
  })
})
