import {
  ApiError,
  getProjectMaintenanceRun,
  type ProjectMaintenanceRun,
} from './client'

export interface ProjectMaintenanceRouteIdentity {
  projectId: string
  workflowRunId: string
}

export interface ProjectMaintenancePollOptions {
  maxAttempts: number
  intervalMs: number
  onUpdate: (run: ProjectMaintenanceRun) => void
  onError?: (error: ApiError) => void
}

export type ProjectMaintenancePollResult = 'completed' | 'max_attempts' | 'cancelled' | 'failed'

type MaintenanceRunLoader = (
  projectId: string,
  workflowRunId: string,
  signal?: AbortSignal,
) => Promise<ProjectMaintenanceRun>

const stoppingStatuses = new Set<ProjectMaintenanceRun['status']>([
  'USER_CONFIRMATION',
  'PROJECT_UPDATED',
  'CANCELLED',
])

function safeQueryError(error: unknown): ApiError {
  return error instanceof ApiError
    ? error
    : new ApiError(0, 'request_failed', 'The request could not be completed.')
}

function waitForNextAttempt(delayMs: number, signal: AbortSignal): Promise<boolean> {
  if (signal.aborted) return Promise.resolve(false)
  if (delayMs === 0) return Promise.resolve(true)
  return new Promise((resolve) => {
    const timer = window.setTimeout(() => {
      signal.removeEventListener('abort', onAbort)
      resolve(true)
    }, delayMs)
    const onAbort = (): void => {
      window.clearTimeout(timer)
      resolve(false)
    }
    signal.addEventListener('abort', onAbort, { once: true })
  })
}

export class ProjectMaintenanceQuery {
  private generation = 0
  private controller: AbortController | null = null
  private readonly loadRun: MaintenanceRunLoader

  constructor(loadRun: MaintenanceRunLoader = getProjectMaintenanceRun) {
    this.loadRun = loadRun
  }

  cancel(): void {
    this.generation += 1
    this.controller?.abort()
    this.controller = null
  }

  private releaseController(generation: number, controller: AbortController): void {
    if (generation === this.generation && this.controller === controller) this.controller = null
  }

  async poll(
    identity: ProjectMaintenanceRouteIdentity,
    options: ProjectMaintenancePollOptions,
  ): Promise<ProjectMaintenancePollResult> {
    if (
      !identity.projectId
      || !identity.workflowRunId
      || !Number.isInteger(options.maxAttempts)
      || options.maxAttempts < 1
      || options.maxAttempts > 100
      || !Number.isInteger(options.intervalMs)
      || options.intervalMs < 0
      || options.intervalMs > 60_000
    ) {
      throw new ApiError(0, 'invalid_request', 'The project maintenance query is invalid.')
    }

    this.controller?.abort()
    const generation = ++this.generation
    const controller = new AbortController()
    this.controller = controller

    for (let attempt = 0; attempt < options.maxAttempts; attempt += 1) {
      let run: ProjectMaintenanceRun
      try {
        run = await this.loadRun(identity.projectId, identity.workflowRunId, controller.signal)
      } catch (error: unknown) {
        if (generation !== this.generation || controller.signal.aborted) return 'cancelled'
        options.onError?.(safeQueryError(error))
        if (generation !== this.generation || controller.signal.aborted) return 'cancelled'
        this.releaseController(generation, controller)
        return 'failed'
      }
      if (generation !== this.generation || controller.signal.aborted) return 'cancelled'
      if (run.id !== identity.workflowRunId) {
        options.onError?.(new ApiError(0, 'invalid_response', 'The server returned an invalid response.'))
        if (generation !== this.generation || controller.signal.aborted) return 'cancelled'
        this.releaseController(generation, controller)
        return 'failed'
      }
      options.onUpdate(run)
      if (generation !== this.generation || controller.signal.aborted) return 'cancelled'
      if (stoppingStatuses.has(run.status)) {
        this.releaseController(generation, controller)
        return 'completed'
      }
      if (attempt + 1 < options.maxAttempts) {
        const shouldContinue = await waitForNextAttempt(options.intervalMs, controller.signal)
        if (!shouldContinue || generation !== this.generation) return 'cancelled'
      }
    }

    this.releaseController(generation, controller)
    return generation === this.generation ? 'max_attempts' : 'cancelled'
  }
}
