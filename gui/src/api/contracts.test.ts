import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

import { CONTRACT_VERSION } from './contracts'
import type {
  ExecutionSegmentIdentity,
  GuiControlRequest,
  GuiPauseRequest,
  PauseIntentFact,
  PlanningRequestOutcome,
  SafeContentRef,
  TimelineCursor,
  TimelineEntryIdentity,
  TimelineSinkEntry,
  TimelineSnapshotPage,
  TurnInterruptOutcome,
} from './contracts'

interface WireFixtures {
  contract_version: string
  samples: Record<string, Record<string, unknown>>
}

const fixtures: WireFixtures = JSON.parse(
  readFileSync(
    fileURLToPath(
      new URL('../../../tests/fixtures/parallel_contracts/wire-fixtures.json', import.meta.url),
    ),
    'utf-8',
  ),
)

const isStr = (value: unknown) => typeof value === 'string' && value.length > 0
const isNull = (value: unknown) => value === null
const isStrOrNull = (value: unknown) => isStr(value) || isNull(value)
const isInt = (value: unknown) => typeof value === 'number' && Number.isInteger(value)
const isIntOrNull = (value: unknown) => isInt(value) || isNull(value)

function expectExactKeys(sample: Record<string, unknown>, keys: readonly string[]) {
  expect(Object.keys(sample).sort()).toEqual([...keys].sort())
}

function expectShape(sample: Record<string, unknown>, spec: Record<string, (v: unknown) => boolean>) {
  for (const [key, check] of Object.entries(spec)) {
    expect(check(sample[key]), `field ${key} = ${JSON.stringify(sample[key])}`).toBe(true)
  }
}

describe('frozen parallel contracts (C0 wire fixtures)', () => {
  it('matches the Python-side contract version', () => {
    expect(fixtures.contract_version).toBe(CONTRACT_VERSION)
  })

  it('turn_interrupt_outcome keeps the frozen interrupted literals', () => {
    const sample = fixtures.samples.turn_interrupt_outcome
    expectExactKeys(sample, [
      'turn_id',
      'agent_run_id',
      'finish_reason',
      'stop_code',
      'committed_position',
      'partial_text',
      'workflow_run_id',
      'node_run_id',
      'segment_id',
    ])
    expect(sample.finish_reason).toBe('interrupted')
    expect(sample.stop_code).toBe('user_pause')
    // Compile-time check: the wire sample is assignable to the frozen type.
    const typed: TurnInterruptOutcome = sample as unknown as TurnInterruptOutcome
    expect(typed.turn_id).toEqual(sample.turn_id)
  })

  it('pause_intent_fact uses the frozen pause lifecycle', () => {
    const sample = fixtures.samples.pause_intent_fact
    expectExactKeys(sample, [
      'control_generation',
      'command_id',
      'owner',
      'owner_id',
      'reason',
      'lifecycle',
      'requested_at',
      'suspended_at',
      'resumed_at',
    ])
    expect(sample.lifecycle).toBe('suspended')
    expect(sample.reason).toBe('user_interrupt')
    const typed: PauseIntentFact = sample as unknown as PauseIntentFact
    expect(typed.control_generation).toBe(7)
  })

  it('execution_segment_identity carries the append-only segment fields', () => {
    const sample = fixtures.samples.execution_segment_identity
    expectExactKeys(sample, [
      'segment_id',
      'workflow_run_id',
      'node_run_id',
      'ordinal',
      'leaf_session_id',
      'leaf_task_run_id',
      'agent_run_id',
      'turn_id',
      'status',
      'pause_reason',
      'previous_segment_id',
      'accepted_command_id',
    ])
    expect(sample.status).toBe('active')
    const typed: ExecutionSegmentIdentity = sample as unknown as ExecutionSegmentIdentity
    expect(typed.ordinal).toBe(2)
  })

  it('planning outcomes keep started/ended facts and layer values', () => {
    const expected = [
      'operation_id',
      'attempt',
      'request_sequence',
      'layer',
      'outcome',
      'started_at',
      'ended_at',
      'reason',
      'revision',
    ] as const
    const cases = [
      { name: 'planning_request_outcome_model_request', layer: 'model_request', outcome: 'interrupted' },
      { name: 'planning_request_outcome_validation', layer: 'candidate_validation', outcome: 'invalid' },
      { name: 'planning_request_outcome_operation', layer: 'planning_operation', outcome: 'paused' },
    ] as const
    for (const item of cases) {
      const sample = fixtures.samples[item.name]
      expectExactKeys(sample, expected)
      expect(sample.layer).toBe(item.layer)
      expect(sample.outcome).toBe(item.outcome)
      const typed: PlanningRequestOutcome = sample as unknown as PlanningRequestOutcome
      expect(typed.started_at).toEqual(sample.started_at)
    }
  })

  it('timeline entries carry source identity and committed availability', () => {
    const identityKeys = [
      'workspace_id',
      'root_session_id',
      'item_id',
      'kind',
      'source_kind',
      'source_id',
      'source_session_id',
      'source_position',
      'planning_binding_id',
      'planning_operation_id',
      'workflow_run_id',
      'node_run_id',
      'segment_id',
      'parent_item_id',
      'revision',
      'occurred_at',
      'content_ref',
      'availability',
    ] as const
    const identity = fixtures.samples.timeline_entry_identity
    expectExactKeys(identity, identityKeys)
    expectShape(identity, {
      workspace_id: isStr,
      root_session_id: isStr,
      item_id: isStr,
      kind: isStr,
      source_kind: isStr,
      source_id: isStr,
      source_session_id: isStr,
      source_position: isIntOrNull,
      revision: isInt,
      occurred_at: isStr,
      availability: (value) => value === 'committed',
    })
    const typedIdentity: TimelineEntryIdentity = identity as unknown as TimelineEntryIdentity
    expect(typedIdentity.kind).toBe('planning_input')

    const sinkEntry = fixtures.samples.timeline_sink_entry
    expectExactKeys(sinkEntry, [...identityKeys, 'timeline_position'])
    expectShape(sinkEntry, { timeline_position: isInt })
    const typedEntry: TimelineSinkEntry = sinkEntry as unknown as TimelineSinkEntry
    expect(typedEntry.timeline_position).toBe(120)
  })

  it('snapshot page embeds a cursor with a schema-versioned high water', () => {
    const page = fixtures.samples.timeline_snapshot_page
    expectExactKeys(page, ['entries', 'cursor', 'revision', 'reset'])
    const cursor = page.cursor as Record<string, unknown>
    expectExactKeys(cursor, [
      'schema_version',
      'workspace_id',
      'root_session_id',
      'high_water',
      'before_position',
    ])
    expectShape(cursor, {
      schema_version: isInt,
      workspace_id: isStr,
      root_session_id: isStr,
      high_water: isInt,
      before_position: isIntOrNull,
    })
    const typed: TimelineSnapshotPage = page as unknown as TimelineSnapshotPage
    expect(typed.reset).toBe(false)
    expect(typed.entries[0].item_id).toEqual((page.entries as TimelineSinkEntry[])[0].item_id)
    const cursorTyped: TimelineCursor = cursor as unknown as TimelineCursor
    expect(cursorTyped.high_water).toBe(120)
  })

  it('safe content refs only expose committed offsets', () => {
    const sample = fixtures.samples.safe_content_ref
    expectExactKeys(sample, ['content_id', 'kind', 'committed_offset', 'total_length', 'availability'])
    expectShape(sample, {
      content_id: isStr,
      kind: (value) => value === 'blob' || value === 'inline',
      committed_offset: isInt,
      total_length: isIntOrNull,
      availability: isStr,
    })
    const typed: SafeContentRef = sample as unknown as SafeContentRef
    expect(typed.committed_offset).toBe(4096)
  })

  it('GUI control and pause requests keep the frozen bodies', () => {
    const control = fixtures.samples.gui_control_request
    expectExactKeys(control, ['command_id', 'session_id', 'text', 'client_message_id'])
    expectShape(control, {
      command_id: isStr,
      session_id: isStr,
      text: isStr,
      client_message_id: isStrOrNull,
    })
    const typedControl: GuiControlRequest = control as unknown as GuiControlRequest
    expect(typedControl.text).toBe('继续')

    const pause = fixtures.samples.gui_pause_request
    expectExactKeys(pause, ['command_id', 'session_id', 'expected_run_row_version'])
    expectShape(pause, {
      command_id: isStr,
      session_id: isStr,
      expected_run_row_version: isIntOrNull,
    })
    const typedPause: GuiPauseRequest = pause as unknown as GuiPauseRequest
    expect(typedPause.expected_run_row_version).toBe(11)
  })
})
