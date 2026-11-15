// Deterministic request gating for job/identity switches.
// A switch invalidates the gate; late responses from the previous scope are
// dropped before they can touch reactive state.  Pure module (no Vue imports)
// so it can be tested deterministically with `node --test`.

export class RequestGate {
  private current = 0

  /** Start a request in the current scope. */
  next(): number {
    this.current += 1
    return this.current
  }

  /** Invalidate every in-flight request (job or identity switch). */
  invalidate(): number {
    this.current += 1
    return this.current
  }

  isCurrent(token: number): boolean {
    return token === this.current
  }

  get epoch(): number {
    return this.current
  }
}

/**
 * Run a loader and apply its result only if the gate is still in the same
 * scope.  Returns whether the result was applied.
 */
export async function loadIfCurrent<T>(
  gate: RequestGate,
  loader: () => Promise<T>,
  apply: (value: T) => void
): Promise<boolean> {
  const token = gate.next()
  const value = await loader()
  if (!gate.isCurrent(token)) return false
  apply(value)
  return true
}

export interface PollScope {
  epoch: number
}

export function sameScope(current: number, scope: PollScope): boolean {
  return current === scope.epoch
}

/** Snapshot a job/identity scope before a multi-step operation. */
export interface OperationScope {
  jobId: string
  epoch: number
}

export function captureScope(jobId: string, epoch: number): OperationScope {
  return { jobId, epoch }
}

/** True only while neither the job nor the epoch changed since the snapshot. */
export function scopeValid(scope: OperationScope, jobId: string, epoch: number): boolean {
  return scope.jobId === jobId && scope.epoch === epoch
}
