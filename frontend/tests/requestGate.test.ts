// Deterministic race tests for the job/identity request gate (node --test).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { RequestGate, loadIfCurrent, sameScope, captureScope, scopeValid } from '../src/lib/requestGate.ts'

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((r) => { resolve = r })
  return { promise, resolve }
}

test('late response from the previous job is dropped after a switch', async () => {
  const gate = new RequestGate()
  const pending = deferred<string>()
  const applied: string[] = []
  const run = loadIfCurrent(gate, () => pending.promise, (value) => applied.push(value))
  gate.invalidate() // simulate selectJob()
  pending.resolve('stale-AI-result')
  assert.equal(await run, false)
  assert.deepEqual(applied, [])
})

test('older request never overwrites a newer one', async () => {
  const gate = new RequestGate()
  const slower = deferred<string>()
  const faster = deferred<string>()
  const applied: string[] = []
  const older = loadIfCurrent(gate, () => slower.promise, (value) => applied.push(value))
  const newer = loadIfCurrent(gate, () => faster.promise, (value) => applied.push(value))
  faster.resolve('newer-java')
  assert.equal(await newer, true)
  slower.resolve('older-ai')
  assert.equal(await older, false)
  assert.deepEqual(applied, ['newer-java'])
})

test('identity switch invalidates both scopes through a shared gate reset', async () => {
  const jobGate = new RequestGate()
  const identityGate = new RequestGate()
  const pendingJobs = deferred<string[]>()
  const applied: string[][] = []
  const run = loadIfCurrent(identityGate, () => pendingJobs.promise,
    (value) => applied.push(value))
  jobGate.invalidate()
  identityGate.invalidate() // applyIdentity() bumps both
  pendingJobs.resolve(['stale-owner-jobs'])
  assert.equal(await run, false)
  assert.deepEqual(applied, [])
})

test('polling stops when the scope changes', () => {
  const epoch = 4
  assert.equal(sameScope(epoch, { epoch: 4 }), true)
  assert.equal(sameScope(epoch + 1, { epoch: 4 }), false)
})

test('sequential jobs each load their own scope only', async () => {
  const gate = new RequestGate()
  const values: string[] = []
  await loadIfCurrent(gate, async () => 'ai', (value) => values.push(value))
  gate.invalidate()
  await loadIfCurrent(gate, async () => 'java', (value) => values.push(value))
  gate.invalidate()
  await loadIfCurrent(gate, async () => 'sales', (value) => values.push(value))
  assert.deepEqual(values, ['ai', 'java', 'sales'])
})

test('multi-step operation aborts after a job switch (simulated latency)', async () => {
  const gate = new RequestGate()
  const scope = captureScope('job-ai', gate.epoch)
  const steps: string[] = []
  // step 1: confirm (simulated slow backend)
  await new Promise((r) => setTimeout(r, 30))
  steps.push('confirm')
  // user switches job while step 2 is waiting
  gate.invalidate()
  const nextJob = { jobId: 'job-java', epoch: gate.epoch }
  await new Promise((r) => setTimeout(r, 30))
  // every subsequent step re-validates the captured scope
  const stillValid = scopeValid(scope, nextJob.jobId, nextJob.epoch)
  if (stillValid) steps.push('create-version')
  if (scopeValid(scope, nextJob.jobId, nextJob.epoch)) steps.push('publish')
  assert.equal(stillValid, false)
  assert.deepEqual(steps, ['confirm'])
})

test('scope stays valid when nothing switches', async () => {
  const gate = new RequestGate()
  const scope = captureScope('job-ai', gate.epoch)
  await new Promise((r) => setTimeout(r, 10))
  assert.equal(scopeValid(scope, 'job-ai', gate.epoch), true)
  gate.invalidate()
  assert.equal(scopeValid(scope, 'job-ai', gate.epoch), false)
})
