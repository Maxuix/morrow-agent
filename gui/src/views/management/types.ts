import type { ManagementCommand } from '../../api/management'

export type Mutate = (kind: ManagementCommand, body: Record<string, unknown>, target?: string) => Promise<boolean>
